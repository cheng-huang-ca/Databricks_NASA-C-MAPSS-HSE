import numpy as np
import pandas as pd
import pytest

from sentinelops.conditions import CONDITION_FEATURES, STATS, condition, condition_features, condition_stats
from sentinelops.landing import CARDINALITY
from sentinelops.model import SENSORS


def flights(units=4, cycles=12, altitudes=(0.0021, 10.0046, 41.9982)):
    """Engines cycling through conditions; each condition shifts every sensor by 100 x its index."""
    rng = np.random.default_rng(0)
    frame = pd.DataFrame({"unit": np.repeat(np.arange(1, units + 1), cycles), "cycle": np.tile(np.arange(1, cycles + 1), units)})
    index = np.arange(len(frame)) % len(altitudes)
    frame["setting_1"] = np.asarray(altitudes)[index]
    for sensor in SENSORS:
        frame[sensor] = 100.0 * index + rng.normal(size=len(frame)) + 0.1 * frame.cycle
    return frame


def test_conditions_round_altitude_half_up():
    assert condition(pd.Series([-0.0017, 0.4999, 0.5, 9.9987, 24.9978, 42.0049])).tolist() == [0, 0, 1, 10, 25, 42]


def test_statistics_come_from_training_rows_per_condition():
    train = flights()
    stats = condition_stats(train)
    assert stats.condition.tolist() == [0, 10, 42] and list(stats.columns) == ["condition", *STATS]
    assert stats.rows.sum() == len(train)
    at_42 = train[train.setting_1 > 40]
    assert stats.sensor_2_mean.iloc[-1] == pytest.approx(at_42.sensor_2.mean())
    assert stats.sensor_2_std.iloc[-1] == pytest.approx(at_42.sensor_2.std())


def test_standardizing_removes_the_condition_shift():
    train = flights()
    out = condition_features(train, condition_stats(train))
    assert list(out.columns) == CONDITION_FEATURES
    raw_spread = train.groupby(condition(train.setting_1)).sensor_2.mean()
    z_spread = out.groupby("condition").sensor_2_z.mean()
    assert raw_spread.max() - raw_spread.min() > 150 and np.allclose(z_spread, 0)


def test_features_use_training_statistics_and_never_the_future():
    train, test = flights(), flights(units=2)
    stats = condition_stats(train)
    expected = condition_features(test, stats)
    shifted = test.copy()
    shifted.loc[8:, SENSORS] = 999  # Later cycles of engine 1, and all of engine 2.
    pd.testing.assert_frame_equal(expected.iloc[:8], condition_features(shifted, stats).iloc[:8])
    # Test rows are standardized with the training statistics, not their own.
    row = test.iloc[0]
    first = stats[stats.condition == 0].iloc[0]
    assert expected.sensor_2_z.iloc[0] == pytest.approx((row.sensor_2 - first.sensor_2_mean) / first.sensor_2_std)


def test_unseen_condition_fails_and_constant_sensor_is_zero():
    train = flights(altitudes=(0.0, 10.0))
    with pytest.raises(ValueError, match="no training statistics"):
        condition_features(flights(altitudes=(0.0, 35.0)), condition_stats(train))
    train["sensor_2"] = 5.0
    assert (condition_features(train, condition_stats(train)).sensor_2_z == 0).all()


def test_pipeline_matches_the_pandas_contract(load_pipeline):
    pipeline = load_pipeline("pipelines/medallion.py", {
        "sentinelops.catalog": "c", "sentinelops.landing": "/v/cmapss_ingest/v1",
        "sentinelops.landing_later": "/v/cmapss_ingest/v2"})
    assert pipeline["SENSORS"] == SENSORS
    assert pipeline["TEST_ENGINES"] == {subset: splits["test"][1] for subset, splits in CARDINALITY.items()}
    assert ["cycle", *pipeline["condition_feature_columns"]] == CONDITION_FEATURES
    assert pipeline["stat_columns"] == STATS[:-1]
    declared = pipeline["declared"]
    # Each later version gets its own flows; the v1 tables keep their original flows.
    flows = [(kwargs["name"], kwargs["target"]) for kind, _, kwargs, _ in declared if kind == "append_flow"]
    assert flows == [("cmapss_lines_v2", "c.bronze.cmapss_lines"), ("cmapss_labels_v2", "c.bronze.cmapss_labels")]
    rules = {name: rule for kind, args, _, _ in declared if kind == "expect_all_or_fail" for name, rule in args[0].items()}
    for subset, engines in pipeline["TEST_ENGINES"].items():
        assert f"WHEN '{subset}' THEN {engines}" in rules["official_label"]
    assert all(f"{s}_z IS NOT NULL" in rules["standardized_with_training_statistics"] for s in SENSORS)
    views = {kwargs["name"] for kind, _, kwargs, _ in declared if kind == "materialized_view"}
    assert {"c.gold.cmapss_condition_stats", "c.gold.cmapss_condition_features"} <= views


def test_pipeline_without_later_versions_declares_no_extra_flows(load_pipeline):
    pipeline = load_pipeline("pipelines/medallion.py", {"sentinelops.catalog": "c", "sentinelops.landing": "/v1"})
    assert not [d for d in pipeline["declared"] if d[0] == "append_flow"]
