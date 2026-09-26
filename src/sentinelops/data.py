"""Verified public-data download and strict whitespace parsing."""
import hashlib
import io
from pathlib import Path
import urllib.request
import zipfile

import numpy as np
import pandas as pd

URL = "https://zenodo.org/records/15346912/files/CMAPSSData.zip?download=1"
MD5 = "79a22f36e80606c69d0e9e4da5bb2b7a"
COLUMNS = ["unit", "cycle"] + [f"setting_{i}" for i in range(1, 4)] + [f"sensor_{i}" for i in range(1, 22)]
SUBSETS = ("FD001", "FD002", "FD003", "FD004")
MEMBERS = [f"{kind}_{subset}.txt" for subset in SUBSETS for kind in ("train", "test", "RUL")] + ["readme.txt"]


def download(destination: Path) -> Path:
    destination.mkdir(parents=True, exist_ok=True)
    archive = destination / "CMAPSSData.zip"
    if not archive.exists():
        request = urllib.request.Request(URL, headers={"User-Agent": "SentinelOps/0.1"})
        with urllib.request.urlopen(request, timeout=120) as response:
            payload = response.read()
        if hashlib.md5(payload).hexdigest() != MD5:
            raise ValueError("Dataset checksum mismatch")
        archive.write_bytes(payload)
    payload = archive.read_bytes()
    if hashlib.md5(payload).hexdigest() != MD5:
        raise ValueError("Cached dataset checksum mismatch")
    with zipfile.ZipFile(io.BytesIO(payload)) as zipped:
        for name in MEMBERS:
            matches = [n for n in zipped.namelist() if Path(n).name.lower() == name.lower()]
            if len(matches) != 1:
                raise ValueError(f"Missing or ambiguous archive member: {name}")
            (destination / name).write_bytes(zipped.read(matches[0]))
    return destination


def read_trajectories(path: Path) -> pd.DataFrame:
    frame = pd.read_csv(path, sep=r"\s+", header=None)
    if frame.shape[1] != 26:
        raise ValueError("C-MAPSS must have exactly 26 columns")
    frame.columns = COLUMNS
    if not np.isfinite(frame.to_numpy()).all():
        raise ValueError("Non-finite sensor values")
    for key in ("unit", "cycle"):
        if (frame[key] < 1).any() or (frame[key] % 1 != 0).any():
            raise ValueError(f"Invalid {key}")
        frame[key] = frame[key].astype(int)
    if frame.duplicated(["unit", "cycle"]).any():
        raise ValueError("Duplicate unit/cycle")
    return frame.sort_values(["unit", "cycle"]).reset_index(drop=True)
