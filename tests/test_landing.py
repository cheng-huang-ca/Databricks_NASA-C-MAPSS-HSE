import hashlib
import json

import pytest

from sentinelops import landing


def test_verified_landing_preserves_raw_and_endpoint_order(tmp_path, monkeypatch):
    source = tmp_path / "source"
    source.mkdir()
    # Isolate archive validation from the small cardinality-independent test.
    def extract(_):
        for split in ("train", "test"):
            (source / f"{split}_FD001.txt").write_text("1 1 " + "0 " * 24 + "\n")
        (source / "RUL_FD001.txt").write_text("\n".join(str(i) for i in range(100)))
    class Frame:
        class Unit:
            def nunique(self): return 100
        unit = Unit()
        def __init__(self, n): self.n = n
        def __len__(self): return self.n
    monkeypatch.setattr(landing, "download", extract)
    monkeypatch.setattr(landing, "read_trajectories", lambda p: Frame(20631 if p.name.startswith("train") else 13096))
    destination = tmp_path / "landing"
    manifest = landing.prepare(source, destination)
    labels = [json.loads(line) for line in (destination / "labels/FD001.json").read_text().splitlines()]
    assert labels[0]["unit"] == 1 and labels[0]["rul"] == 0
    assert labels[-1]["unit"] == 100 and labels[-1]["rul"] == 99
    assert labels[0]["split"] == "test"
    raw = destination / "trajectories/train_FD001.txt"
    assert raw.read_bytes() == (source / "train_FD001.txt").read_bytes()
    assert manifest["files"]["trajectories/train_FD001.txt"] == hashlib.sha256(raw.read_bytes()).hexdigest()
    assert landing.prepare(source, destination) == manifest
    raw.write_text("corrupted")
    with pytest.raises(ValueError, match="immutable"):
        landing.prepare(source, destination)


def test_cached_archive_must_pass_checksum(tmp_path):
    (tmp_path / "CMAPSSData.zip").write_bytes(b"not the NASA archive")
    with pytest.raises(ValueError, match="checksum"):
        landing.prepare(tmp_path, tmp_path / "output")


def test_v2_lands_fd002_to_fd004_with_their_own_labels_and_an_lf_manifest(tmp_path, monkeypatch):
    source, destination = tmp_path / "source", tmp_path / "v2"
    source.mkdir()

    def extract(_):
        for subset in landing.VERSIONS["v2"]:
            for split in ("train", "test"):
                (source / f"{split}_{subset}.txt").write_text(f"{subset} {split}\n")
            (source / f"RUL_{subset}.txt").write_text("\n".join("7" for _ in range(landing.CARDINALITY[subset]["test"][1])))

    class Frame:
        def __init__(self, rows, engines):
            self.rows, self.unit = rows, type("Unit", (), {"nunique": lambda _: engines})()
        def __len__(self): return self.rows

    def read(path):
        split, subset = path.stem.split("_")
        return Frame(*landing.CARDINALITY[subset][split])

    monkeypatch.setattr(landing, "download", extract)
    monkeypatch.setattr(landing, "read_trajectories", read)
    manifest = landing.prepare(source, destination, landing.VERSIONS["v2"])
    assert sorted(manifest["files"]) == sorted(
        [f"trajectories/{split}_{s}.txt" for s in ("FD002", "FD003", "FD004") for split in ("train", "test")]
        + [f"labels/{s}.json" for s in ("FD002", "FD003", "FD004")])
    fd004 = [json.loads(line) for line in (destination / "labels/FD004.json").read_text().splitlines()]
    assert len(fd004) == 248 and {row["subset"] for row in fd004} == {"FD004"} and fd004[-1]["unit"] == 248
    assert b"\r" not in (destination / "manifest.json").read_bytes()
    monkeypatch.setattr(landing, "download", lambda _: None)
    (source / "RUL_FD003.txt").write_text("7\n" * 99)
    with pytest.raises(ValueError, match="100 nonnegative official FD003"):
        landing.prepare(source, tmp_path / "other", ("FD003",))


def test_versions_partition_the_four_subsets():
    assert [s for subsets in landing.VERSIONS.values() for s in subsets] == list(landing.CARDINALITY)
    assert landing.VERSIONS["v1"] == ("FD001",)
