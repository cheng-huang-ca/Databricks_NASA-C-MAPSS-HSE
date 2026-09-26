"""Contracts for training from the keyed Gold medallion tables (pandas side)."""
import hashlib

import pandas as pd

from sentinelops.model import FEATURES

KEYS = ["dataset", "subset", "split", "unit", "cycle"]
ENGINE = KEYS[:-1]


def _one_subset(frame: pd.DataFrame, subset: str, split: str, keys: list[str]) -> pd.DataFrame:
    frame = frame[(frame.subset == subset) & (frame.split == split)]
    if frame.duplicated(keys).any():
        raise ValueError(f"Duplicate {split} keys in {subset}")
    return frame


def training_frame(features: pd.DataFrame, labels: pd.DataFrame, subset: str, columns=FEATURES) -> pd.DataFrame:
    """Every training feature row must have exactly one capped label, and vice versa."""
    x = _one_subset(features, subset, "train", KEYS)
    y = _one_subset(labels, subset, "train", KEYS)
    joined = x.merge(y[[*KEYS, "rul"]], on=KEYS, how="inner", validate="one_to_one")
    if joined.empty or len(joined) != len(x) or len(joined) != len(y):
        raise ValueError(f"{subset} training features and labels do not align")
    if joined[[*columns, "rul"]].isna().any().any():
        raise ValueError(f"Null training feature or label in {subset}")
    # Match the bootstrap row order so engine-disjoint selection is reproducible.
    return joined.sort_values(["unit", "cycle"]).reset_index(drop=True).astype({"cycle": "int64"})


def label_frame(labels: pd.DataFrame, subset: str) -> pd.DataFrame:
    """Training labels in the row order and dtypes that training_frame() produces."""
    y = _one_subset(labels, subset, "train", KEYS)
    return y.sort_values(["unit", "cycle"]).reset_index(drop=True).astype({"cycle": "int64"})


def endpoint_frame(endpoints: pd.DataFrame, subset: str, columns=FEATURES) -> pd.DataFrame:
    """One official, uncapped label per test engine at its last observed cycle."""
    test = _one_subset(endpoints, subset, "test", ENGINE)
    if test.empty or test[[*columns, "rul"]].isna().any().any():
        raise ValueError(f"Missing {subset} test endpoint features or official labels")
    return test.sort_values("unit").reset_index(drop=True).astype({"cycle": "int64"})


def digest(frame: pd.DataFrame) -> str:
    """Content fingerprint of exactly the rows and columns used, independent of index."""
    hashed = pd.util.hash_pandas_object(frame, index=False).to_numpy().tobytes()
    return hashlib.sha256(hashed + ",".join(frame.columns).encode()).hexdigest()[:16]
