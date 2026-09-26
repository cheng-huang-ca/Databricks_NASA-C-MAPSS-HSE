"""Prepare immutable, checksum-verified C-MAPSS landing versions without cloud compute.

v1 holds FD001 (landed first, never changed); v2 adds FD002-FD004. Each version is its own
directory with a manifest, ingested by its own append flow (pipelines/medallion.py).
"""
import hashlib
import json
from pathlib import Path

from sentinelops.data import MD5, URL, download, read_trajectories

# (rows, engines) per split, from the NASA archive; test engines = official endpoint labels.
CARDINALITY = {
    "FD001": {"train": (20631, 100), "test": (13096, 100)},
    "FD002": {"train": (53759, 260), "test": (33991, 259)},
    "FD003": {"train": (24720, 100), "test": (16596, 100)},
    "FD004": {"train": (61249, 249), "test": (41214, 248)},
}
VERSIONS = {"v1": ("FD001",), "v2": ("FD002", "FD003", "FD004")}


def prepare(source: Path, destination: Path, subsets=VERSIONS["v1"]) -> dict:
    download(source)  # Revalidates the cached archive before extracting.
    files = {}
    for subset in subsets:
        for split, (rows, engines) in CARDINALITY[subset].items():
            name = f"{split}_{subset}.txt"
            frame = read_trajectories(source / name)
            if len(frame) != rows or frame.unit.nunique() != engines:
                raise ValueError(f"Unexpected {subset} trajectory cardinality")
            files[f"trajectories/{name}"] = (source / name).read_bytes()
        values = (source / f"RUL_{subset}.txt").read_text().split()
        if len(values) != CARDINALITY[subset]["test"][1] or any(not v.isdigit() for v in values):
            raise ValueError(f"Expected {CARDINALITY[subset]['test'][1]} nonnegative official {subset} endpoint labels")
        files[f"labels/{subset}.json"] = ("\n".join(json.dumps({
            "dataset": "CMAPSS", "subset": subset, "split": "test",
            "unit": i, "rul": int(value),
        }, sort_keys=True) for i, value in enumerate(values, 1)) + "\n").encode()
    manifest = {"source": URL, "archive_md5": MD5, "files": {}}
    for name, payload in files.items():
        path = destination / name
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists() and path.read_bytes() != payload:
            raise ValueError(f"Refusing to overwrite immutable landing file: {name}")
        path.write_bytes(payload)
        manifest["files"][name] = hashlib.sha256(payload).hexdigest()
    # Bytes, so the manifest is identical whether prepared on Windows or Linux.
    (destination / "manifest.json").write_bytes((json.dumps(manifest, indent=2) + "\n").encode())
    return manifest


if __name__ == "__main__":
    for version, subsets in VERSIONS.items():
        prepare(Path("data/cmapss"), Path(f"data/landing/{version}"), subsets)
