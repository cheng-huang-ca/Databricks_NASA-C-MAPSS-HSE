"""On-demand integration assertions; writes a compact evidence file to the volume."""
import argparse
import hashlib
import json
from pathlib import Path
import sys

parser = argparse.ArgumentParser()
parser.add_argument("--catalog", required=True)
parser.add_argument("--schema", required=True)
parser.add_argument("--source-root", required=True)
args = parser.parse_args()
sys.path.insert(0, args.source_root)

import numpy as np
import pandas as pd
from pyspark.sql import SparkSession, functions as F
from sentinelops.conditions import CONDITION_FEATURES, STATS, condition_features, condition_stats
from sentinelops.data import read_trajectories
from sentinelops.landing import CARDINALITY, VERSIONS
from sentinelops.model import features, labels

spark = SparkSession.builder.getOrCreate()
base = Path(f"/Volumes/{args.catalog}/{args.schema}/landing/cmapss_ingest")
# The landed versions, in order; the pipeline ingests each one through its own flow.
versions = [v for v in VERSIONS if (base / v / "manifest.json").exists()]
assert versions and versions == list(VERSIONS)[:len(versions)], versions
for version in versions:
    manifest = json.loads((base / version / "manifest.json").read_text())
    for relative, checksum in manifest["files"].items():
        assert hashlib.sha256((base / version / relative).read_bytes()).hexdigest() == checksum, (version, relative)
home = {subset: base / version for version in versions for subset in VERSIONS[version]}
root = base / "v1"
quality_probe = (root / "trajectories/train_FD001_quality.txt").exists()

rows = sum(n for subset in home for n, _ in CARDINALITY[subset].values())
train_rows = sum(CARDINALITY[subset]["train"][0] for subset in home)
test_engines = sum(CARDINALITY[subset]["test"][1] for subset in home)
reference = {subset: {split: read_trajectories(home[subset] / f"trajectories/{split}_{subset}.txt")
                      for split in ("train", "test")} for subset in home}
stats = {subset: condition_stats(reference[subset]["train"]) for subset in home}
counts = {}
for name, expected in {
    "bronze.cmapss_lines": rows + (9 if quality_probe else 0),
    "bronze.cmapss_labels": test_engines,
    "silver.cmapss_observations": rows,
    "silver.cmapss_quarantine": 5 if quality_probe else 0,
    "silver.cmapss_conflicts": 1 if quality_probe else 0,
    "silver.cmapss_endpoint_labels": test_engines,
    "gold.cmapss_features": rows,
    "gold.cmapss_training_labels": train_rows,
    "gold.cmapss_test_endpoints": test_engines,
    "gold.cmapss_condition_stats": sum(len(frame) for frame in stats.values()),
    "gold.cmapss_condition_features": rows,
}.items():
    count = spark.table(f"{args.catalog}.{name}").count()
    assert count == expected, (name, count, expected)
    counts[name] = count

keys = ["dataset", "subset", "split", "unit", "cycle"]
by_subset = {}
for subset in home:
    # One subset at a time keeps the driver's pandas copies small.
    scope = F.col("subset") == subset
    gold = spark.table(f"{args.catalog}.gold.cmapss_features").filter(scope).toPandas()
    condition_gold = spark.table(f"{args.catalog}.gold.cmapss_condition_features").filter(scope).toPandas()
    assert not gold.duplicated(keys).any() and not condition_gold.duplicated(keys).any(), subset
    stats_gold = spark.table(f"{args.catalog}.gold.cmapss_condition_stats").filter(scope).orderBy("condition").toPandas()
    assert stats_gold.condition.tolist() == stats[subset].condition.tolist(), subset
    np.testing.assert_allclose(stats_gold[STATS].astype(float), stats[subset][STATS].astype(float), rtol=1e-10, atol=1e-10)
    for split in ("train", "test"):
        raw = reference[subset][split]
        expected = features(raw)
        actual = gold[gold.split == split].sort_values(["unit", "cycle"]).reset_index(drop=True)
        np.testing.assert_allclose(actual[expected.columns], expected, rtol=1e-10, atol=1e-10)
        # Standardizing divides by Spark's and pandas' own statistics, so allow summation-order noise.
        expected = condition_features(raw, stats[subset]).reset_index(drop=True)
        actual = condition_gold[condition_gold.split == split].sort_values(["unit", "cycle"]).reset_index(drop=True)
        np.testing.assert_allclose(actual[CONDITION_FEATURES].astype(float), expected.astype(float), rtol=1e-9, atol=1e-9)
        if split == "train":
            actual_labels = spark.table(f"{args.catalog}.gold.cmapss_training_labels").filter(scope).orderBy("unit", "cycle").toPandas()
            np.testing.assert_array_equal(actual_labels.rul, labels(raw))
    expected_labels = pd.read_json(home[subset] / f"labels/{subset}.json", lines=True).sort_values("unit")
    actual_labels = spark.table(f"{args.catalog}.gold.cmapss_test_endpoints").filter(scope).orderBy("unit").toPandas()
    np.testing.assert_array_equal(actual_labels.rul, expected_labels.rul)
    by_subset[subset] = {"rows": len(gold), "conditions": len(stats[subset])}
for name in ("cmapss_features", "cmapss_condition_features"):
    pk = spark.sql(f"SELECT column_name FROM {args.catalog}.information_schema.key_column_usage WHERE table_schema = 'gold' AND table_name = '{name}' ORDER BY ordinal_position").collect()
    assert [r.column_name for r in pk] == keys, (name, pk)
evidence_path = root / "verification.json"
previous = json.loads(evidence_path.read_text()) if evidence_path.exists() else None
# A new landing version changes the canonical data once; otherwise counts must hold on reruns.
if previous and previous.get("landing_versions", ["v1"]) == versions:
    stable = [name for name in counts if name not in {
        "bronze.cmapss_lines", "silver.cmapss_quarantine", "silver.cmapss_conflicts"}]
    assert all(previous["counts"].get(name) == counts[name] for name in stable), "Canonical data changed on rerun"
    if previous.get("quality_probe", False) == quality_probe:
        assert previous["counts"] == counts, "Counts changed on no-input rerun"
report = {"counts": counts, "landing_versions": versions, "subsets": by_subset, "feature_parity": True,
          "condition_feature_parity": True, "official_endpoint_labels": True,
          "primary_key": keys, "quality_probe": quality_probe,
          "verified_runs": (previous or {}).get("verified_runs", 0) + 1}
if quality_probe:
    conflict = spark.table(f"{args.catalog}.silver.cmapss_conflicts").first()
    assert conflict.unit == 999 and conflict.payload_versions == 2
    invalid = spark.table(f"{args.catalog}.silver.cmapss_quarantine").toPandas()
    assert invalid.source_file.str.endswith("train_FD001_quality.txt").all()
# Reuse this task's compute for a billing snapshot; it may lag the current run.
try:
    report["billing_snapshot"] = [row.asDict() for row in spark.sql("""
        SELECT u.usage_date, u.sku_name, SUM(u.usage_quantity) AS dbus,
               SUM(u.usage_quantity * p.pricing.default) AS list_cost_usd
        FROM system.billing.usage u JOIN system.billing.list_prices p
          ON u.cloud = p.cloud AND u.sku_name = p.sku_name AND u.usage_unit = p.usage_unit
          AND u.usage_start_time >= p.price_start_time
          AND (u.usage_end_time <= p.price_end_time OR p.price_end_time IS NULL)
        WHERE u.workspace_id = '7405619144539463' AND u.usage_date = current_date()
          AND p.currency_code = 'USD'
        GROUP BY u.usage_date, u.sku_name
    """).collect()]
except Exception as error:
    report["billing_snapshot_error"] = str(error)[:1000]
evidence_path.write_text(json.dumps(report, indent=2, default=str))
print(json.dumps(report, default=str))
