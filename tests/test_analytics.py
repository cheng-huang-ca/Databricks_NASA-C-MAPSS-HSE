import json
import re
from pathlib import Path

import pandas as pd
import pytest
import yaml

from sentinelops import analytics as a

ROOT = Path(__file__).resolve().parents[1]
DASHBOARDS = sorted((ROOT / "dashboards").glob("*.lvdash.json"))
RESOURCES = yaml.safe_load((ROOT / "resources" / "analytics.yml").read_text())["resources"]
CURATED = {"cmapss_fleet_status", "cmapss_predictions", "cmapss_model_performance", "cmapss_feature_drift",
           "cmapss_alerts", "osha_injury_facts"}
# Columns with at most 10 distinct values: 8 event categories, 9 injury types + other, 5 engines, 3 bands.
LOW_CARDINALITY = ("event_category", "nature_group", "engine", "risk_band")


def documents(rows):
    """Gold osha_documents columns the facts read: (id, month, naics, event code/title, body code/title,
    source code/title, nature title, amputation)."""
    frame = pd.DataFrame(rows, columns=["report_id", "event_month", "naics", "event_code", "event_title",
                                        "body_part_code", "body_part_title", "source_code", "source_title",
                                        "nature_title", "amputation"])
    return frame.assign(event_year=frame.event_month.str[:4].astype(int), state="TEXAS",
                        naics_sector=frame.naics.str[:2], hospitalized=1, loss_of_eye=0, inspected=True)


def test_injury_facts_harmonize_and_name_categories_without_narratives():
    facts = a.injury_facts(documents([
        (1, "2019-05", "311612", "6411", "Caught in running equipment", "31", "Hip(s)", "3211", "Meat grinders",
         "Amputations", 1),
        (2, "2024-03", "236220", "6411", "Caught in running equipment", "51", "Hip joint(s)", "8621", "Forklift",
         "Fractures", 0),
        (3, "2016-07", None, "9999", "Nonclassifiable", "9999", "Nonclassifiable", "9999", "Nonclassifiable",
         "Soreness, pain, hurt-nonspecified injury", None),
    ]))
    assert list(facts.columns) == [name for name, _, _ in a.INJURY_FACT_COLUMNS]
    assert not set(facts.columns) & set(a.FORBIDDEN_COLUMNS)
    assert facts.event_category.tolist() == ["Contact with object or equipment"] * 2 + [a.NOT_CLASSIFIABLE]
    # Hips count as lower extremities in both coding eras.
    assert facts.body_part_category.tolist()[:2] == ["Lower extremities"] * 2
    assert facts.nature_category.tolist() == ["Amputation", "Fracture", a.UNSPECIFIED]
    assert facts.source_category.tolist() == ["Machinery", "Vehicles", a.NOT_CLASSIFIABLE]
    assert facts.industry_sector.tolist() == ["Manufacturing", "Construction", a.UNKNOWN_SECTOR]
    assert facts.coding_era.tolist() == ["2015-23", "2024-25", "2015-23"]
    assert str(facts.event_date[1]) == "2024-03-01" and facts.event_year.tolist() == [2019, 2024, 2016]
    assert facts.amputation.isna().tolist() == [False, False, True]  # unknown stays null, not zero


def test_every_label_has_a_display_name():
    from sentinelops import extraction
    for field in ("event", "body_part", "source"):
        assert set(a.CATEGORY_NAMES[field]) == set(extraction.DIVISIONS[field])
    assert set(a.NATURE_NAMES) == set(extraction.NATURE)


@pytest.mark.parametrize("rul, band", [(0, "critical"), (20, "critical"), (20.01, "warning"), (50, "warning"),
                                       (50.5, "healthy"), (130, "healthy")])
def test_risk_bands(rul, band):
    assert a.risk_band(rul) == band


def test_fleet_status_takes_the_given_version_at_each_engines_last_cycle():
    predictions = pd.DataFrame([
        ("FD001", 1, 30, 2, 90.0, 112), ("FD001", 1, 31, 2, 88.0, 111),
        ("FD001", 1, 31, 3, 15.0, 111), ("FD001", 2, 49, 3, 60.0, None), ("FD001", 1, 30, 3, 16.0, 112)],
        columns=["subset", "unit", "cycle", "model_version", "predicted_rul", "actual_rul"]
    ).assign(model_name="m", scored_at=pd.Timestamp("2026-09-24"))
    last = pd.DataFrame({"subset": ["FD001", "FD001"], "unit": [2, 1], "last_cycle": [49, 31]})
    status = a.fleet_status(predictions, last, 3)
    assert list(status.columns) == [name for name, _, _ in a.FLEET_STATUS_COLUMNS]
    assert status.unit.tolist() == [1, 2] and status.predicted_rul.tolist() == [15.0, 60.0]
    assert status.risk_band.tolist() == ["critical", "healthy"] and status.model_version.tolist() == [3, 3]
    assert status.actual_rul.isna().tolist() == [False, True]
    with pytest.raises(ValueError, match="run cmapss_score"):
        a.fleet_status(predictions, last, 2)  # version 2 never scored engine 2


def test_ddl_and_comments_quote_text():
    sql = a.ddl("c.gold.t", [("id", "BIGINT NOT NULL", "OSHA's key")], "It's a table", ["id"])
    assert "id BIGINT NOT NULL COMMENT 'OSHA\\'s key'" in sql and "CONSTRAINT t_pk PRIMARY KEY (id)" in sql
    assert sql.endswith("COMMENT 'It\\'s a table'")
    assert a.comment_statements("c.gold.t", None, {"x": "a'b"}) == ["ALTER TABLE c.gold.t ALTER COLUMN x COMMENT 'a\\'b'"]


@pytest.mark.parametrize("path", DASHBOARDS, ids=lambda p: p.name)
def test_dashboards_are_consistent_and_read_only_curated_gold_tables(path):
    dashboard = json.loads(path.read_text(encoding="utf-8"))
    datasets = {d["name"]: a.dataset_sql(d) for d in dashboard["datasets"]}
    assert len(datasets) == len(dashboard["datasets"])
    for sql in datasets.values():
        assert a.gold_tables(sql) and a.gold_tables(sql) <= CURATED, sql
        assert not re.search(r"\b(" + "|".join(a.FORBIDDEN_COLUMNS) + r")\b", sql, re.IGNORECASE), sql
        assert "sentinelops_dev" not in sql  # the bundle sets the catalog per target
    names, cells = set(), set()
    for page in dashboard["pages"]:
        for item in page["layout"]:
            widget, box = item["widget"], item["position"]
            assert widget["name"] not in names
            names.add(widget["name"])
            assert box["x"] >= 0 and box["width"] >= 1 and box["x"] + box["width"] <= 6
            covered = {(x, y) for x in range(box["x"], box["x"] + box["width"])
                       for y in range(box["y"], box["y"] + box["height"])}
            assert not covered & cells, widget["name"]
            cells |= covered
            queries = {q["name"]: q["query"]["datasetName"] for q in widget.get("queries", [])}
            assert set(queries.values()) <= set(datasets)
            for field in widget.get("spec", {}).get("encodings", {}).get("fields", []):
                assert field["queryName"] in queries
            # The renderer cycles 10 colors, so series must come from columns with at most 10 values.
            color = widget.get("spec", {}).get("encodings", {}).get("color", {}).get("fieldName")
            assert color in (None, *LOW_CARDINALITY), (widget["name"], color)
    # Every column a widget uses appears in its dataset's SQL (the job checks the real result columns).
    for name, columns in a.widget_columns(dashboard).items():
        for column in columns:
            assert re.search(rf"\b{re.escape(column)}\b", datasets[name]), (name, column)


def test_safety_dashboard_credits_the_department_of_labor():
    text = (ROOT / "dashboards" / "safety_incidents.lvdash.json").read_text(encoding="utf-8")
    assert "U.S. Department of Labor" in text and "No endorsement is implied" in text


def test_bundle_dashboards_use_the_lookup_warehouse_and_viewer_credentials():
    for name, board in RESOURCES["dashboards"].items():
        assert (ROOT / "resources" / board["file_path"]).resolve() in [p.resolve() for p in DASHBOARDS], name
        assert board["warehouse_id"] == "${var.warehouse_id}" and board["dataset_catalog"] == "${var.catalog}"
        assert board["embed_credentials"] is False
    config = yaml.safe_load((ROOT / "databricks.yml").read_text())
    assert config["variables"]["warehouse_id"]["lookup"] == {"warehouse": "Serverless Starter Warehouse"}


def test_refresh_checks_the_genie_spaces_of_its_own_target():
    # The space moved under targets.dev with the staging/prod targets; the refresh job must follow it.
    config = yaml.safe_load((ROOT / "resources" / "analytics.yml").read_text())
    assert list(a.genie_spaces(config, "dev")) == ["sentinelops_operations"]
    assert a.genie_spaces(config, "staging") == a.genie_spaces(config, "prod") == {}
    parameters = RESOURCES["jobs"]["analytics_refresh"]["tasks"][0]["spark_python_task"]["parameters"]
    assert parameters[parameters.index("--target") + 1] == "${bundle.target}"


def test_genie_space_follows_the_serialized_format_and_uses_curated_tables():
    # Dev-only: the space needs its tables, which only dev has (resources/analytics.yml).
    (space,) = yaml.safe_load((ROOT / "resources" / "analytics.yml").read_text())["targets"]["dev"]["resources"]["genie_spaces"].values()
    assert space["warehouse_id"] == "${var.warehouse_id}"
    body = space["serialized_space"]
    assert body["version"] == 2
    hex_id = re.compile(r"^[0-9a-f]{32}$")
    samples = body["config"]["sample_questions"]
    instructions = body["instructions"]
    lists = [samples, instructions["text_instructions"], instructions["example_question_sqls"]]
    ids = [item["id"] for items in lists for item in items]
    assert all(hex_id.match(i) for i in ids) and len(ids) == len(set(ids))
    for items in lists:
        assert [item["id"] for item in items] == sorted(item["id"] for item in items)
    assert len(instructions["text_instructions"]) <= 1
    tables = body["data_sources"]["tables"]
    identifiers = [t["identifier"] for t in tables]
    assert identifiers == sorted(identifiers)
    assert {i.split(".")[-1] for i in identifiers} == CURATED
    assert all(i.startswith("${var.catalog}.gold.") for i in identifiers)
    for table in tables:
        columns = [c["column_name"] for c in table.get("column_configs", [])]
        assert columns == sorted(columns)
    for example in instructions["example_question_sqls"]:
        sql = "".join(example["sql"])
        assert a.gold_tables(sql) <= CURATED and "${var.catalog}.gold." in sql
    text = json.dumps(body)
    assert "Department of Labor" in text and "Decline requests to identify employers" in text
