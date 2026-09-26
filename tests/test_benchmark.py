import mlflow
import numpy as np
import pandas as pd
import pytest

from sentinelops.benchmark import FEATURE_SETS, run_subset
from sentinelops.conditions import condition_features, condition_stats
from sentinelops.model import features, labels, metrics
from sentinelops.rul_evaluation import evaluate_predictions
from test_conditions import flights


@pytest.fixture(scope="module")
def local_tracking(tmp_path_factory):
    """Evaluations go to a throwaway store; the experiment's artifacts stay out of the working tree."""
    root = tmp_path_factory.mktemp("mlflow")
    previous = mlflow.get_tracking_uri()
    mlflow.set_tracking_uri(f"sqlite:///{(root / 'mlflow.db').as_posix()}")
    mlflow.set_experiment(experiment_id=mlflow.create_experiment("benchmark", artifact_location=root.as_uri()))
    yield
    mlflow.set_tracking_uri(previous)


def frames(units=10, cycles=20):
    train, test = flights(units, cycles), flights(4, cycles - 5)
    stats = condition_stats(train)
    y = labels(train).rename("rul")
    built = {"raw": (features(train), features(test)),
             "condition": (condition_features(train, stats), condition_features(test, stats))}
    out_train, out_test = {}, {}
    for name, (x_train, x_test) in built.items():
        out_train[name] = pd.concat([train[["unit"]], x_train, y], axis=1)
        last = pd.concat([test[["unit"]], x_test], axis=1).groupby("unit").tail(1)
        out_test[name] = last.assign(rul=np.arange(len(last)) + 3).reset_index(drop=True)
    return out_train, out_test


def test_benchmark_scores_test_endpoints_only_for_the_set_cv_chose():
    train, test = frames()
    report, validation, _, predictions = run_subset(train, test, candidates=2, folds=2)
    chosen = report["chosen_by_cv"]
    assert chosen == min(report["feature_sets"], key=lambda n: report["feature_sets"][n]["cv_rmse"])
    assert set(report["feature_sets"]) == set(FEATURE_SETS) and len(predictions) == len(test[chosen])
    assert report["test"] == metrics(predictions.actual_rul, predictions.predicted_rul)
    assert sorted(validation.unit.unique()) == report["validation_units"]
    # The other set's test endpoints are never read: an unusable frame there changes nothing.
    other = next(name for name in FEATURE_SETS if name != chosen)
    again = run_subset(train, {**test, other: pd.DataFrame()}, candidates=2, folds=2)[0]
    assert again == report


def test_evaluate_logs_prefixed_regression_metrics_and_the_nasa_score(local_tracking):
    frame = pd.DataFrame({"rul": [0, 10, 50, 120], "prediction": [5.0, 8.0, 60.0, 100.0]})
    with mlflow.start_run() as run:
        result = evaluate_predictions(frame, prefix="validation_")
    expected = metrics(frame.rul, frame.prediction)
    assert result["validation_nasa_score"] == pytest.approx(expected["nasa_score"])
    assert result["validation_root_mean_squared_error"] == pytest.approx(expected["rmse"])
    logged = mlflow.get_run(run.info.run_id).data.metrics
    assert logged["validation_nasa_score"] == pytest.approx(expected["nasa_score"])
