"""Benchmark FD001-FD004 from the Gold tables: raw vs condition-standardized features.

Per subset, both feature sets are tuned with engine-grouped CV on the fit engines, the one with
the lower CV RMSE is chosen, its untouched validation engines are evaluated with
mlflow.models.evaluate, and only then are the official test endpoints scored, once. Nothing is
registered: FD001's @champion stays the only operational model (promotion goes through
cmapss_promote). One MLflow run per subset, nested under one parent run.
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
parser.add_argument("--subsets", required=True)
parser.add_argument("--candidates", type=int, required=True)
parser.add_argument("--folds", type=int, required=True)
parser.add_argument("--source-root", required=True)
args = parser.parse_args()
# Serverless Python tasks execute via exec(), where __file__ is not defined.
sys.path.insert(0, args.source_root)

import mlflow
from pyspark.sql import SparkSession, functions as F

from sentinelops.benchmark import FEATURE_SETS, run_subset
from sentinelops.medallion import KEYS, digest, endpoint_frame, training_frame
from sentinelops.rul_evaluation import evaluate_predictions

subsets = args.subsets.split(",")
if not re.fullmatch(r"[a-zA-Z_][a-zA-Z0-9_]*", args.catalog) or not all(re.fullmatch(r"FD00[1-4]", s) for s in subsets):
    raise ValueError("Catalog must be a simple SQL identifier and subsets FD001-FD004")

spark = SparkSession.builder.getOrCreate()
gold = f"{args.catalog}.gold"
tables = {"raw": f"{gold}.cmapss_features", "condition": f"{gold}.cmapss_condition_features"}
user = spark.sql("SELECT current_user()").first()[0]
mlflow.set_experiment(f"/Users/{user}/sentinelops-cmapss-benchmark")

results = {}
with mlflow.start_run(run_name="cmapss-benchmark") as parent:
    mlflow.log_params({"subsets": args.subsets, "search_candidates": args.candidates, "cv_folds": args.folds,
                       "selection": "lower CV RMSE on fit engines; test scored once, for the chosen set only"})
    for subset in subsets:
        started = time.time()
        scope = F.col("subset") == subset
        labels = spark.table(f"{gold}.cmapss_training_labels").filter(scope).toPandas()
        official = spark.table(f"{gold}.cmapss_test_endpoints").filter(scope).select(*KEYS, "rul")
        train, test = {}, {}
        for name, table in tables.items():
            features = spark.table(table).filter(scope)
            train[name] = training_frame(features.toPandas(), labels, subset, FEATURE_SETS[name])
            test[name] = endpoint_frame(features.join(official, KEYS).toPandas(), subset, FEATURE_SETS[name])
        report, validation, _, predictions = run_subset(train, test, args.candidates, args.folds)
        chosen = report["chosen_by_cv"]
        report["digests"] = {"features": digest(train[chosen][["unit", *FEATURE_SETS[chosen]]]),
                             "training_labels": digest(train[chosen][["unit", "cycle", "rul"]]),
                             "test_endpoints": digest(test[chosen][["unit", *FEATURE_SETS[chosen], "rul"]])}
        with mlflow.start_run(run_name=subset, nested=True):
            mlflow.log_params({"subset": subset, "feature_set": chosen, "feature_table": tables[chosen],
                               **{f"hgb_{k}": v for k, v in report["feature_sets"][chosen]["params"].items()},
                               **{f"digest_{k}": v for k, v in report["digests"].items()}})
            for name, result in report["feature_sets"].items():
                mlflow.log_metrics({f"{name}_cv_rmse": result["cv_rmse"], f"{name}_validation_rmse": result["validation_rmse"]})
            report["validation_evaluate"] = evaluate_predictions(validation, prefix="validation_")
            mlflow.log_metrics({f"test_{k}": v for k, v in report["test"].items()})
            mlflow.log_metric("baseline_test_rmse", report["constant_baseline"]["rmse"])
            report["seconds"] = round(time.time() - started, 1)
            mlflow.log_dict(report, "report.json")
            with tempfile.TemporaryDirectory() as temporary:
                path = Path(temporary) / "test_predictions.csv"
                predictions.to_csv(path, index=False)
                mlflow.log_artifact(str(path))
        results[subset] = report
        print(json.dumps({"subset": subset, "chosen_by_cv": chosen, "test": report["test"], "seconds": report["seconds"]}))
    mlflow.log_dict(results, "benchmark.json")
    print(json.dumps({"mlflow_run_id": parent.info.run_id, "results": results}, default=str))
