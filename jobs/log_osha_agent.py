"""Package the OSHA assistant as a ResponsesAgent, register it in Unity Catalog, and smoke-test it.

The artifacts are built from Gold exactly as osha_answer_eval builds its index: current
embeddings only, their first 256 dimensions rescaled to unit length, plus each report's text.
The decline threshold is recalibrated with the same rule on questions that are never evaluated
(retrieval-eval paraphrases and DEV questions). The model is logged from code with the chat and
embedding endpoints declared as resources (automatic authentication passthrough), registered,
reloaded from the registry and asked two DEV questions (paid calls, cents) before any deployment.
"""
import argparse
import json
import re
import sys
import tempfile
import time
from pathlib import Path

parser = argparse.ArgumentParser()
parser.add_argument("--catalog", required=True)
parser.add_argument("--schema", required=True)
parser.add_argument("--embedding-endpoint", required=True)
parser.add_argument("--chat-endpoint", required=True)
parser.add_argument("--dimensions", type=int, required=True)
parser.add_argument("--k", type=int, required=True)
parser.add_argument("--max-tokens", type=int, required=True)
parser.add_argument("--reasoning-effort", required=True)
parser.add_argument("--job-run-id", required=True)
parser.add_argument("--source-root", required=True)
args = parser.parse_args()
# Serverless Python tasks execute via exec(), where __file__ is not defined.
sys.path.insert(0, args.source_root)

import mlflow
import numpy as np
from mlflow.models.resources import DatabricksServingEndpoint
from pyspark.sql import SparkSession, functions as F

from sentinelops import agent, answer_eval, retrieval_eval
from sentinelops.answers import ANSWERED, ERROR, PROMPT_VERSION
from sentinelops.embeddings import format_query, unit_vectors
from sentinelops.retrieval import ExactIndex, truncate

for identifier in (args.catalog, args.schema):
    if not re.fullmatch(r"[a-zA-Z_][a-zA-Z0-9_]*", identifier):
        raise ValueError("Catalog/schema must be simple SQL identifiers")
if not all(re.fullmatch(r"[a-z0-9-]+", e) for e in (args.embedding_endpoint, args.chat_endpoint)):
    raise ValueError("Endpoints must be serving endpoint names")

started = time.monotonic()
spark = SparkSession.builder.getOrCreate()
gold = f"{args.catalog}.gold"
documents = spark.table(f"{gold}.osha_documents").select("report_id", "document", "document_sha256")
prefixes = spark.table(f"{gold}.osha_embeddings").filter(F.col("model") == args.embedding_endpoint) \
    .select("report_id", "document_sha256", F.slice("embedding", 1, args.dimensions).alias("prefix"))
frame = documents.join(prefixes, ["report_id", "document_sha256"]).orderBy("report_id").toPandas()
if len(frame) != documents.count():
    raise ValueError(f"{documents.count() - len(frame)} documents lack a current embedding; run osha_embed first")
ids = frame.report_id.to_numpy()
index = ExactIndex(ids, truncate(np.stack(frame.prefix.to_numpy()), args.dimensions))
texts = dict(zip(ids.tolist(), frame.document))


def dev(category):
    return [q["question"] for q in answer_eval.DEV if q["category"] == category]


# The same calibration as osha_answer_eval; EVAL questions are never embedded here.
on_topic = [q["question"] for q in retrieval_eval.QUESTIONS] + dev(answer_eval.ANSWERABLE)
off_topic = [q["question"] for q in retrieval_eval.OFF_TOPIC] + dev(answer_eval.OFF_TOPIC)
asked = list(dict.fromkeys(on_topic + off_topic))
query_frame = spark.createDataFrame([(i, format_query(q)) for i, q in enumerate(asked)], "i INT, text STRING")
embedded = {row.i: row.e for row in query_frame.selectExpr("i", f"ai_query('{args.embedding_endpoint}', text) AS e").collect()}
vectors = dict(zip(asked, unit_vectors([embedded[i] for i in range(len(asked))])))


def top1(items):
    _, scores = index.search(truncate(np.stack([vectors[q] for q in items]), args.dimensions), 1)
    return [round(float(s), 4) for s in scores[:, 0]]


threshold = answer_eval.calibrate_threshold(top1(on_topic), top1(off_topic))
config = {"chat_endpoint": args.chat_endpoint, "embedding_endpoint": args.embedding_endpoint,
          "dimensions": args.dimensions, "k": args.k, "min_score": threshold, "max_tokens": args.max_tokens,
          "reasoning_effort": args.reasoning_effort, "prompt_version": PROMPT_VERSION}
paths = agent.save_artifacts(Path(tempfile.mkdtemp()) / "osha_agent", index, texts, config)
sizes_mb = {name: round(Path(path).stat().st_size / 2**20, 1) for name, path in paths.items()}

smoke = [dev(answer_eval.ANSWERABLE)[0], dev(answer_eval.OFF_TOPIC)[0]]
model_name = f"{args.catalog}.{args.schema}.{agent.MODEL_NAME}"
source = Path(args.source_root) / "sentinelops"
user = spark.sql("SELECT current_user()").first()[0]
mlflow.set_registry_uri("databricks-uc")
mlflow.set_experiment(f"/Users/{user}/sentinelops-safety-rag")
with mlflow.start_run(run_name="osha-agent-log") as run:
    mlflow.log_params({**config, "documents": len(frame), "job_run_id": args.job_run_id})
    info = mlflow.pyfunc.log_model(
        name="agent",
        python_model=str(source / "agent_model.py"),
        code_paths=[str(source)],
        artifacts=paths,
        resources=[DatabricksServingEndpoint(endpoint_name=args.chat_endpoint),
                   DatabricksServingEndpoint(endpoint_name=args.embedding_endpoint)],
        pip_requirements=["mlflow==3.16.1", "numpy==2.5.3", "databricks-sdk==0.140.0"],
        input_example={"input": [{"role": "user", "content": smoke[0]}]},
        registered_model_name=model_name,
    )
    version = int(info.registered_model_version)
    # Load what the endpoint will load (from the registry) and ask two DEV questions.
    loaded = mlflow.pyfunc.load_model(f"models:/{model_name}/{version}")
    checks = []
    for question in smoke:
        out = loaded.predict({"input": [{"role": "user", "content": question}]})
        custom = out["custom_outputs"]
        checks.append({"question": question, "status": custom["status"], "top1_score": custom["top1_score"]})
    mlflow.log_dict({"config": config, "smoke": checks}, "agent_log_report.json")

report = {"model": model_name, "version": version, "model_uri": info.model_uri, "mlflow_run_id": run.info.run_id,
          "config": config, "documents": len(frame), "artifact_mb": sizes_mb, "smoke": checks,
          "seconds": round(time.monotonic() - started, 1)}
print(json.dumps(report, default=str))
if any(check["status"] == ERROR for check in checks) or checks[0]["status"] != ANSWERED:
    raise RuntimeError("Smoke test failed: the reloaded agent must answer the DEV question without errors")
