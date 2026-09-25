"""Regression-evaluate the deployed OSHA agent endpoint with the osha_answer_eval harness.

The same question sets, code scorers and Llama 3.3 judges as the in-process evaluation, but every
answer comes from the serving endpoint: this checks that the deployed agent behaves like the one
that was evaluated. A warm-up question (a DEV off-topic one, declined by the threshold without a
model call) wakes a scaled-to-zero endpoint and reads the threshold it serves. Report text for
the local employer-name scan: `per_question`, as osha_answer_eval prints it.
"""
import argparse
import json
import re
import sys
import time

parser = argparse.ArgumentParser()
parser.add_argument("--catalog", required=True)
parser.add_argument("--endpoint-name", required=True)
parser.add_argument("--judge-endpoint", required=True)
parser.add_argument("--workers", type=int, required=True)
parser.add_argument("--question-sets", required=True, help="Comma-separated names from answer_eval.SETS")
parser.add_argument("--job-run-id", required=True)
parser.add_argument("--source-root", required=True)
args = parser.parse_args()
# Serverless Python tasks execute via exec(), where __file__ is not defined.
sys.path.insert(0, args.source_root)

import mlflow
from pyspark.sql import SparkSession

from sentinelops import answer_eval
from sentinelops.agent import EndpointAssistant, EndpointClient

if not re.fullmatch(r"[a-zA-Z_][a-zA-Z0-9_]*", args.catalog) or not all(
        re.fullmatch(r"[a-z0-9-]+", e) for e in (args.endpoint_name, args.judge_endpoint)):
    raise ValueError("Catalog must be a simple identifier and endpoints serving endpoint names")
if not re.fullmatch(r"[0-9]+", args.job_run_id):
    raise ValueError("Job run ID must be numeric")
sets = {name: answer_eval.SETS[name] for name in args.question_sets.split(",")}  # KeyError on unknown names

started = time.monotonic()
spark = SparkSession.builder.getOrCreate()
texts = {int(row.report_id): row.document
         for row in spark.table(f"{args.catalog}.gold.osha_documents").select("report_id", "document").collect()}
# A cold start can take minutes: long timeout, and 503/504 while scaling up are retried.
query = EndpointClient.from_workspace(args.endpoint_name, timeout=300, retries=6, backoff_seconds=10)
endpoint = EndpointAssistant(query, texts)

warm_question = next(q["question"] for q in answer_eval.DEV if q["category"] == answer_eval.OFF_TOPIC)
warm_started = time.monotonic()
warm = query(warm_question)["custom_outputs"]
warm_up = {"status": warm["status"], "seconds": round(time.monotonic() - warm_started, 1)}
threshold = warm["min_score"]

user = spark.sql("SELECT current_user()").first()[0]
mlflow.set_experiment(f"/Users/{user}/sentinelops-safety-rag")
with mlflow.start_run(run_name=f"agent-endpoint-eval-{answer_eval.VERSION}") as run:
    mlflow.log_params({"eval_version": answer_eval.VERSION, "eval_fingerprint": answer_eval.fingerprint(),
                       "prompt_version": warm["prompt_version"], "endpoint": args.endpoint_name,
                       "judge_model": args.judge_endpoint, "min_score": threshold, "workers": args.workers,
                       "question_sets": args.question_sets, "job_run_id": args.job_run_id})
    eval_started = time.monotonic()
    result, rows = answer_eval.evaluate_assistant(endpoint, sets, judge_model=f"databricks:/{args.judge_endpoint}",
                                                  workers=args.workers)
    eval_seconds = time.monotonic() - eval_started
    results_by_set = answer_eval.by_set(rows, threshold)
    mlflow.log_metrics({f"{set_name}_{group}_{name}": float(value) for set_name, result_set in results_by_set.items()
                        for group, values in result_set["summary"].items()
                        for name, value in values.items() if isinstance(value, (int, float))})
    per_question = [{key: row[key] for key in ("question_set", "question_id", "category", "status", "top1_score",
                                               "judges", "answer", "trace_id")}
                    | {"cited_ids": (row["citations"] or {}).get("cited_ids"),
                       "coverage": (row["citations"] or {}).get("coverage")} for row in rows]
    report = {"mlflow_run_id": run.info.run_id, "endpoint": args.endpoint_name,
              "eval_fingerprint": answer_eval.fingerprint(), "prompt_version": warm["prompt_version"],
              "min_score": threshold, "warm_up": warm_up, "by_set": results_by_set,
              "overall": answer_eval.summarize(rows)["overall"],
              "seconds": {"evaluate": round(eval_seconds, 1), "total": round(time.monotonic() - started, 1)},
              "per_question": sorted(per_question, key=lambda r: (r["question_set"], r["category"], r["question_id"]))}
    mlflow.log_dict(report, "agent_endpoint_report.json")
print(json.dumps(report, default=str))
