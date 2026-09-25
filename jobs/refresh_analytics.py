"""Rebuild the analytics marts behind the AI/BI dashboards and the Genie space, then check their SQL.

- gold.osha_injury_facts: harmonized OSHA categories per report (sentinelops.analytics); the
  narrative and document text are never read.
- gold.cmapss_fleet_status: the @champion's prediction at each fleet engine's last cycle.
Both are rebuilt with INSERT OVERWRITE, which keeps their comments and primary keys. Column
comments are also applied to the monitoring tables Genie reads. Finally every dashboard dataset
and Genie example query runs here on serverless job compute, and widget fields are checked
against their dataset's columns, so broken SQL fails cheaply, before anyone opens a dashboard on
the SQL warehouse. No SQL warehouse is used.
"""
import argparse
import json
import re
import sys
import time
from pathlib import Path

parser = argparse.ArgumentParser()
parser.add_argument("--catalog", required=True)
parser.add_argument("--schema", required=True)
parser.add_argument("--subset", required=True)
parser.add_argument("--fleet-split", required=True)
parser.add_argument("--bundle-root", required=True)
parser.add_argument("--target", required=True)  # bundle target: which Genie spaces it deploys
parser.add_argument("--job-run-id", required=True)
parser.add_argument("--source-root", required=True)
args = parser.parse_args()
# Serverless Python tasks execute via exec(), where __file__ is not defined.
sys.path.insert(0, args.source_root)

import mlflow
import pandas as pd
import yaml
from mlflow import MlflowClient
from pyspark.sql import SparkSession, functions as F

from sentinelops import analytics as a

for identifier in (args.catalog, args.schema):
    if not re.fullmatch(r"[a-zA-Z_][a-zA-Z0-9_]*", identifier):
        raise ValueError("Catalog/schema must be simple SQL identifiers")
if not re.fullmatch(r"FD00[1-4]", args.subset) or args.fleet_split == "train":
    raise ValueError("Report one FD001-FD004 subset; training trajectories are not fleet data")

started = time.monotonic()
spark = SparkSession.builder.getOrCreate()
gold = f"{args.catalog}.gold"


def rebuild(table: str, columns: list, comment: str, key: list[str], frame: pd.DataFrame) -> int:
    """Create the table once (comments, primary key), then atomically replace its rows."""
    spark.sql(a.ddl(table, columns, comment, key))
    names = [name for name, _, _ in columns]
    if spark.table(table).columns != names:
        raise RuntimeError(f"{table} has columns {spark.table(table).columns}, expected {names}; "
                           "drop the table to change its schema")
    schema = ", ".join(f"{name} {kind.replace(' NOT NULL', '')}" for name, kind, _ in columns)
    rows = frame.copy()
    for column in rows.columns:  # nullable Int32/boolean: pandas NA -> SQL NULL
        if isinstance(rows[column].dtype, pd.api.extensions.ExtensionDtype):
            rows[column] = rows[column].astype(object).where(rows[column].notna(), None)
    spark.createDataFrame(rows, schema).createOrReplaceTempView("refreshed_rows")
    spark.sql(f"INSERT OVERWRITE {table} SELECT {', '.join(names)} FROM refreshed_rows")
    return spark.table(table).count()


# OSHA: harmonized categories per report. Truth harmonization needs the whole corpus.
documents = spark.table(f"{gold}.osha_documents").select(*a.INJURY_FACT_INPUTS).orderBy("report_id").toPandas()
facts = a.injury_facts(documents)
facts_rows = rebuild(f"{gold}.osha_injury_facts", a.INJURY_FACT_COLUMNS, a.INJURY_FACTS_COMMENT, ["report_id"], facts)

# Fleet: the champion's prediction at each engine's last observed cycle.
mlflow.set_registry_uri("databricks-uc")
model_name = f"{args.catalog}.{args.schema}.turbofan_rul"
version = int(MlflowClient().get_model_version_by_alias(model_name, "champion").version)
scope = (F.col("subset") == args.subset) & (F.col("split") == args.fleet_split)
last_cycles = (spark.table(f"{gold}.cmapss_features").filter(scope).groupBy("subset", "unit")
               .agg(F.max("cycle").alias("last_cycle")).toPandas())
predictions = (spark.table(f"{gold}.cmapss_predictions").filter(scope & (F.col("model_version") == version))
               .select("subset", "unit", "cycle", "model_name", "model_version", "predicted_rul", "actual_rul",
                       "scored_at").toPandas())
status = a.fleet_status(predictions, last_cycles, version)
status_rows = rebuild(f"{gold}.cmapss_fleet_status", a.FLEET_STATUS_COLUMNS, a.FLEET_STATUS_COMMENT,
                      ["subset", "unit"], status)

# Descriptions Genie reads for the monitoring tables (their jobs create them without comments).
for table, (table_comment, column_comments) in a.MONITORING_COMMENTS.items():
    missing = set(column_comments) - set(spark.table(f"{gold}.{table}").columns)
    if missing:
        raise RuntimeError(f"{gold}.{table} lacks columns {sorted(missing)}")
    for statement in a.comment_statements(f"{gold}.{table}", table_comment, column_comments):
        spark.sql(statement)

# Check every dashboard dataset and Genie example query, as the warehouse would run them.
root = Path(args.bundle_root)
spark.sql(f"USE CATALOG {args.catalog}")  # dashboards name tables as gold.<table>; the bundle sets the catalog
checks = {}


def run(label: str, sql: str, needed: set[str] = frozenset()) -> None:
    query_started = time.monotonic()
    result = spark.sql(sql)
    missing = needed - set(result.columns)
    if missing:
        raise RuntimeError(f"{label}: widgets use columns {sorted(missing)} that the query doesn't return")
    checks[label] = {"rows": result.count(), "seconds": round(time.monotonic() - query_started, 1)}


for path in sorted((root / "dashboards").glob("*.lvdash.json")):
    dashboard = json.loads(path.read_text(encoding="utf-8"))
    used = a.widget_columns(dashboard)
    for dataset in dashboard["datasets"]:
        run(f"{path.name.split('.')[0]}/{dataset['name']}", a.dataset_sql(dataset), used.get(dataset["name"], set()))
spaces = a.genie_spaces(yaml.safe_load((root / "resources" / "analytics.yml").read_text(encoding="utf-8")), args.target)
for space_name, space in spaces.items():
    body = json.loads(json.dumps(space["serialized_space"]).replace("${var.catalog}", args.catalog))
    for table in body["data_sources"]["tables"]:
        columns = set(spark.table(table["identifier"]).columns)
        unknown = {c["column_name"] for c in table.get("column_configs", [])} - columns
        if unknown:
            raise RuntimeError(f"{space_name}: {table['identifier']} has no columns {sorted(unknown)}")
    for example in body["instructions"]["example_question_sqls"]:
        run(f"genie/{space_name}/{example['id']}", "".join(example["sql"]))

by_band = status.risk_band.value_counts().to_dict()
report = {"osha_injury_facts": {"rows": facts_rows, "documents": len(documents),
                                "by_era": facts.coding_era.value_counts().to_dict(),
                                "not_classifiable": {c: int((facts[c] == a.NOT_CLASSIFIABLE).sum())
                                                     for c in ("event_category", "body_part_category",
                                                               "source_category")},
                                "nature_unspecified": int((facts.nature_category == a.UNSPECIFIED).sum())},
          "cmapss_fleet_status": {"rows": status_rows, "champion_version": version, "risk_bands": by_band,
                                  "lowest_predicted_rul": status.nsmallest(5, "predicted_rul")[
                                      ["unit", "last_cycle", "predicted_rul", "actual_rul"]].to_dict("records")},
          "commented_tables": sorted(a.MONITORING_COMMENTS), "genie_spaces": sorted(spaces), "query_checks": checks,
          "seconds": round(time.monotonic() - started, 1)}
print(json.dumps(report, default=str))
