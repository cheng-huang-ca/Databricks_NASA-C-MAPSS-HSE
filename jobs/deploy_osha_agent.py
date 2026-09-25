"""Deploy a registered OSHA agent version with the Agent Framework, then wait until it's ready.

agents.deploy creates (or updates) a scale-to-zero CPU serving endpoint, provisions short-lived
credentials for the resources the model declares, enables the Review App, AI Gateway inference
tables and real-time tracing to the active experiment. Production-monitoring scorers are never
started here, so no judge runs on the endpoint's traffic. The endpoint is a bounded demo: delete
it with agents.delete_deployment or `databricks serving-endpoints delete` when done.
"""
import argparse
import json
import re
import sys
import time
from datetime import timedelta

parser = argparse.ArgumentParser()
parser.add_argument("--catalog", required=True)
parser.add_argument("--schema", required=True)
parser.add_argument("--model-version", required=True, help="A registered version number, or 'latest'")
parser.add_argument("--endpoint-name", required=True)
parser.add_argument("--job-run-id", required=True)
parser.add_argument("--source-root", required=True)
args = parser.parse_args()
# Serverless Python tasks execute via exec(), where __file__ is not defined.
sys.path.insert(0, args.source_root)

import mlflow
from databricks import agents
from databricks.sdk import WorkspaceClient
from mlflow import MlflowClient

from sentinelops.agent import MODEL_NAME

for identifier in (args.catalog, args.schema):
    if not re.fullmatch(r"[a-zA-Z_][a-zA-Z0-9_]*", identifier):
        raise ValueError("Catalog/schema must be simple SQL identifiers")
if not re.fullmatch(r"[a-z0-9-]+", args.endpoint_name) or not re.fullmatch(r"latest|[0-9]+", args.model_version):
    raise ValueError("Endpoint must be a serving endpoint name and the version a number or 'latest'")

started = time.monotonic()
w = WorkspaceClient()
mlflow.set_registry_uri("databricks-uc")
model_name = f"{args.catalog}.{args.schema}.{MODEL_NAME}"
versions = [int(v.version) for v in MlflowClient().search_model_versions(f"name='{model_name}'")]
version = max(versions) if args.model_version == "latest" else int(args.model_version)
if version not in versions:
    raise ValueError(f"{model_name} has no version {version}")
# Traces from the endpoint go to the experiment active at deploy time.
mlflow.set_experiment(f"/Users/{w.current_user.me().user_name}/sentinelops-safety-rag")
deployment = agents.deploy(model_name, version, endpoint_name=args.endpoint_name, scale_to_zero_enabled=True,
                           tags={"project": "sentinelops", "lifetime": "bounded-demo"})
endpoint = w.serving_endpoints.wait_get_serving_endpoint_not_updating(args.endpoint_name,
                                                                      timeout=timedelta(minutes=40))
entities = [{"name": e.name, "entity_name": e.entity_name, "entity_version": e.entity_version,
             "workload_size": e.workload_size, "scale_to_zero_enabled": e.scale_to_zero_enabled}
            for e in (endpoint.config.served_entities if endpoint.config else [])]
report = {"model": model_name, "version": version, "endpoint": args.endpoint_name,
          "state": {"ready": str(endpoint.state.ready), "config_update": str(endpoint.state.config_update)},
          "served_entities": entities,
          "deployment": {key: str(value) for key, value in vars(deployment).items() if not key.startswith("_")},
          "seconds": round(time.monotonic() - started, 1)}
print(json.dumps(report, default=str))
if "READY" not in report["state"]["ready"] or "FAILED" in report["state"]["config_update"]:
    raise RuntimeError(f"Endpoint {args.endpoint_name} is not ready: {report['state']}")
