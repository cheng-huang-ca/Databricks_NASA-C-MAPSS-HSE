from pathlib import Path

import yaml

from sentinelops import genie_eval as g

ROOT = Path(__file__).resolve().parents[1]


def test_benchmarks_come_from_the_bundle_with_the_catalog_resolved():
    items = g.benchmarks(yaml.safe_load((ROOT / "resources" / "analytics.yml").read_text(encoding="utf-8")), "cat")
    assert len(items) == 12 and all("${" not in i["sql"] and "cat.gold." in i["sql"] for i in items)
    assert items[0]["question"].startswith("What is the average predicted remaining life")


def test_numbers_match_at_genie_precision_and_strings_ignore_case():
    assert g.same_value("36.4213", "36.42") and g.same_value("36.4213", "36") and not g.same_value("36.4213", "36.43")
    assert g.same_value("1543", "1543.0") and not g.same_value("1543", "1544")
    assert g.same_value("Upper extremities", "upper extremities ") and not g.same_value("TEXAS", "OHIO")
    assert g.same_value(None, None) and not g.same_value(None, "0")


def test_compare_allows_extra_columns_and_longer_lists_but_not_wrong_values():
    reference = [["34", "31", "6.61"], ["81", "240", "8.02"]]
    genie = [["34", "engine 34", "31", "6.6", "critical"], ["81", "engine 81", "240", "8.0", "critical"], ["x", "", "", "", ""]]
    assert g.compare(reference, genie) == {"verdict": "match", "matched_columns": [0, 1, 2]}
    assert g.compare(reference, [["34", "31", "6.9"], ["81", "240", "8.0"]])["verdict"] == "mismatch"
    assert g.compare(reference, [["34", "31", "6.61"]])["verdict"] == "mismatch"
    assert g.compare(reference, None)["verdict"] == "no_result"
    # Order matters for a top-k reference.
    assert g.compare(reference, [reference[1], reference[0]])["verdict"] == "mismatch"
