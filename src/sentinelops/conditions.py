"""Operating-condition features for C-MAPSS (pandas reference for gold.cmapss_condition_features).

FD002 and FD004 mix six flight conditions (altitude, Mach number, throttle), which move every
sensor far more than wear does. A condition is the altitude setting rounded to the nearest
thousand feet (0, 10, 20, 25, 35 or 42; FD001 and FD003 fly only at 0). Each sensor is
standardized with the mean and sample standard deviation of its subset's training trajectories
in that condition (never test rows, never labels), and the rolling features of
sentinelops.model are computed on the standardized values. pipelines/medallion.py computes the
same in Spark; jobs/verify_medallion.py compares the two.
"""
import numpy as np
import pandas as pd

from sentinelops.model import SENSORS

CONDITION_FEATURES = ["cycle", "condition", *[f"{s}_z" for s in SENSORS],
                      *[c for s in SENSORS for c in (f"{s}_z_mean_10", f"{s}_z_delta_5")]]
STATS = [*[f"{s}_mean" for s in SENSORS], *[f"{s}_std" for s in SENSORS], "rows"]


def condition(altitude: pd.Series) -> pd.Series:
    # floor(x + 0.5), as in Spark: pandas' round() rounds halves to even.
    return np.floor(altitude + 0.5).astype("int64")


def condition_stats(train: pd.DataFrame) -> pd.DataFrame:
    """Per condition: each sensor's mean and sample standard deviation over training rows."""
    grouped = train.assign(condition=condition(train.setting_1)).groupby("condition")[SENSORS]
    stats = pd.concat([grouped.mean().add_suffix("_mean"), grouped.std().add_suffix("_std"),
                       grouped.size().rename("rows")], axis=1)
    return stats[STATS].reset_index()


def condition_features(frame: pd.DataFrame, stats: pd.DataFrame) -> pd.DataFrame:
    """Rows in `frame` order; fails on a condition the training rows never visited."""
    ordered = frame.sort_values(["unit", "cycle"])
    conditions = condition(ordered.setting_1)
    matched = stats.set_index("condition").reindex(conditions.to_numpy())
    if matched.rows.isna().any():
        raise ValueError("A condition has no training statistics")
    out = pd.DataFrame({"cycle": ordered.cycle.to_numpy(), "condition": conditions.to_numpy()}, index=ordered.index)
    for sensor in SENSORS:
        mean, std = matched[f"{sensor}_mean"].to_numpy(), matched[f"{sensor}_std"].to_numpy()
        out[f"{sensor}_z"] = np.where(std > 0, (ordered[sensor].to_numpy() - mean) / np.where(std > 0, std, 1), 0.0)
    for sensor in SENSORS:
        grouped = out.groupby(ordered.unit.to_numpy())[f"{sensor}_z"]
        out[f"{sensor}_z_mean_10"] = grouped.transform(lambda x: x.rolling(10, min_periods=1).mean())
        out[f"{sensor}_z_delta_5"] = grouped.diff(5).fillna(0)
    return out[CONDITION_FEATURES].sort_index()
