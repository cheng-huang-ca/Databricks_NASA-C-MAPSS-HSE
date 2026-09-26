"""Per-subset RUL benchmark over FD001-FD004 (pandas side; jobs/benchmark_subsets.py logs it).

For each subset, both feature sets are tuned with engine-grouped CV on the fit engines
(sentinelops.model.tune). The set with the lower CV RMSE is chosen before any test row is
touched; only the chosen set's final model is scored on the official test endpoints, once.
"""
import numpy as np
import pandas as pd

from sentinelops.conditions import CONDITION_FEATURES
from sentinelops.model import FEATURES, LABEL_CAP, metrics, tune

FEATURE_SETS = {"raw": FEATURES, "condition": CONDITION_FEATURES}


def run_subset(train: dict[str, pd.DataFrame], test: dict[str, pd.DataFrame], candidates: int, folds: int):
    """`train[name]` / `test[name]`: training rows and test endpoints per feature set, as
    sentinelops.medallion.training_frame / endpoint_frame return them. Returns the report, the
    chosen set's validation predictions, its final model and its test predictions."""
    tuned = {name: tune(train[name][columns], train[name].rul, train[name].unit, candidates, folds)
             for name, columns in FEATURE_SETS.items()}
    chosen = min(tuned, key=lambda name: tuned[name][1]["cv_rmse"])
    model, selection, validation = tuned[chosen]
    endpoints = test[chosen]
    prediction = model.predict(endpoints[FEATURE_SETS[chosen]])
    report = {
        "train_rows": len(train[chosen]), "train_units": int(train[chosen].unit.nunique()),
        "test_units": len(endpoints), "training_label_cap": LABEL_CAP,
        "feature_sets": {name: {key: result[1][key] for key in ("cv_rmse", "validation_rmse", "params")}
                         for name, result in tuned.items()},
        "chosen_by_cv": chosen, "validation_units": selection["validation_units"],
        "test": metrics(endpoints.rul, prediction),
        "constant_baseline": metrics(endpoints.rul, np.repeat(train[chosen].rul.mean(), len(endpoints))),
    }
    return report, validation, model, pd.DataFrame({"unit": endpoints.unit.to_numpy(), "last_cycle": endpoints.cycle.to_numpy(),
                                                    "actual_rul": endpoints.rul.to_numpy(), "predicted_rul": prediction})
