import numpy as np
import pandas as pd
import pytest
from sentinelops.medallion import KEYS, digest, endpoint_frame, training_frame
from sentinelops.model import FEATURES, SEARCH_SPACE, SENSORS, features, fit, labels, select, tune


def trajectories(units=6, cycles=15):
    rng = np.random.default_rng(0)
    frame = pd.DataFrame({"unit": np.repeat(np.arange(1, units + 1), cycles), "cycle": np.tile(np.arange(1, cycles + 1), units)})
    for sensor in SENSORS:
        frame[sensor] = rng.normal(size=len(frame)) + frame.cycle
    return frame


def gold(frame, split="train", subset="FD001"):
    """Gold-shaped tables as the pipeline writes them: keyed, shuffled, INT cycles."""
    x = features(frame).assign(dataset="CMAPSS", subset=subset, split=split, unit=frame.unit)
    x = x.astype({"cycle": "int32"})
    y = x[KEYS].assign(rul=labels(frame).astype("int32"))
    return x.sample(frac=1, random_state=1), y.sample(frac=1, random_state=2)


def test_feature_contract_matches_bootstrap_columns():
    assert list(features(trajectories()).columns) == FEATURES


def test_gold_training_reproduces_bootstrap_selection():
    raw = trajectories()
    x, y = gold(raw)
    other_x, other_y = gold(raw, subset="FD002")
    train = training_frame(pd.concat([x, other_x]), pd.concat([y, other_y]), "FD001")
    assert len(train) == len(raw) and train.cycle.dtype == "int64"
    expected_model, expected = fit(raw)
    model, actual = select(train[FEATURES], train.rul, train.unit)
    assert actual == expected
    np.testing.assert_array_equal(model.predict(train[FEATURES]), expected_model.predict(features(raw)))


def test_training_rejects_missing_or_duplicate_labels():
    x, y = gold(trajectories())
    with pytest.raises(ValueError, match="align"):
        training_frame(x, y.iloc[1:], "FD001")
    with pytest.raises(ValueError, match="Duplicate"):
        training_frame(x, pd.concat([y, y.iloc[:1]]), "FD001")
    with pytest.raises(ValueError, match="align"):
        training_frame(x, y, "FD003")


def test_endpoints_require_one_official_label_per_engine():
    x, _ = gold(trajectories(), split="test")
    last = x[x.cycle == x.cycle.max()].assign(rul=7)
    assert endpoint_frame(last, "FD001").unit.tolist() == list(range(1, 7))
    with pytest.raises(ValueError, match="official"):
        endpoint_frame(last.assign(rul=np.nan), "FD001")
    with pytest.raises(ValueError, match="Duplicate"):
        endpoint_frame(pd.concat([last, last.iloc[:1]]), "FD001")


def test_digest_depends_on_content_not_index():
    frame = pd.DataFrame({"unit": [1, 2], "value": [0.5, 1.5]})
    assert digest(frame) == digest(frame.set_axis([10, 20]))
    assert digest(frame) != digest(frame.assign(value=[0.5, 1.6]))


def test_tuning_keeps_select_validation_engines_out_of_the_search():
    raw = trajectories(units=10, cycles=20)
    x, y = features(raw), labels(raw)
    _, selected = select(x, y, raw.unit)
    model, tuned, validation = tune(x, y, raw.unit, candidates=2, folds=2)
    # The gate compares like with like: the same held-out engines as select().
    assert (tuned["fit_units"], tuned["validation_units"]) == (selected["fit_units"], selected["validation_units"])
    assert sorted(validation.unit.unique()) == tuned["validation_units"] and len(validation) == 2 * 20
    assert tuned["validation_rmse"] == pytest.approx(np.sqrt(((validation.prediction - validation.rul) ** 2).mean()))
    assert set(tuned["params"]) == set(SEARCH_SPACE) and tuned["max_leaf_nodes"] == tuned["params"]["max_leaf_nodes"]
    assert model.n_features_in_ == len(FEATURES)
    # The search is deterministic.
    assert tune(x, y, raw.unit, candidates=2, folds=2)[1] == tuned


def test_frames_check_the_columns_they_are_given():
    x, y = gold(trajectories())
    other = x.assign(extra=1.0)
    assert list(training_frame(other, y, "FD001", [*FEATURES, "extra"]).extra.unique()) == [1.0]
    with pytest.raises(ValueError, match="Null"):
        training_frame(other.assign(extra=np.nan), y, "FD001", [*FEATURES, "extra"])
