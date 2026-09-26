"""mlflow.models.evaluate for RUL predictions, with NASA's asymmetric score as a custom metric.

MLflow's default regressor metrics include MAPE, which is meaningless here: RUL reaches 0.
"""
import mlflow
import pandas as pd

from sentinelops.model import metrics as rul_metrics


def _nasa_score(predictions, targets, metrics):  # MLflow passes arguments by these names.
    return rul_metrics(targets, predictions)["nasa_score"]


NASA_SCORE = mlflow.models.make_metric(eval_fn=_nasa_score, greater_is_better=False, name="nasa_score")


def evaluate_predictions(frame: pd.DataFrame, prefix: str) -> dict:
    """Evaluate `frame` (columns rul, prediction) into the active MLflow run; metric names get `prefix`."""
    result = mlflow.models.evaluate(
        data=frame[["rul", "prediction"]], targets="rul", predictions="prediction", model_type="regressor",
        extra_metrics=[NASA_SCORE], evaluator_config={"log_model_explainability": False, "metric_prefix": prefix})
    return result.metrics
