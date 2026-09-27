import hashlib
import io
import json
import zipfile

import pandas as pd
import pytest

from sentinelops import osha


def test_mask_employer_core_name_and_addresses():
    text, count = osha.mask("An employee of ACME Tools, Inc. at 12 Main Street fell.\nACME TOOLS paid.",
                            "Acme Tools, Inc.", ["12 Main Street", ""])
    assert text == "An employee of [EMPLOYER] at [ADDRESS] fell. [EMPLOYER] paid."
    assert count == 3
    text, count = osha.mask("The worker used a 510 Fairview Road ramp near Oak Street.", "Zed", ["", ""])
    assert text == "The worker used a [ADDRESS] ramp near Oak Street." and count == 1
    text, count = osha.mask("Struck at 252 East 57 Street, New York, NY, 10022-1234 today.", "Zed", ["", ""])
    assert text == "Struck at [ADDRESS], New York, NY [ZIP] today." and count == 2


def test_mask_leaves_short_names_and_partial_words_alone():
    assert osha.mask("An employee at the ABC plant was injured.", "ABC", ["", ""]) == (
        "An employee at the ABC plant was injured.", 0)
    assert osha.mask("Walmartian goods fell on Walmart staff.", "Walmart #1234", ["", ""]) == (
        "Walmartian goods fell on [EMPLOYER] staff.", 1)


def reports(**changes):
    row = {"ID": "2015010015", "UPA": "931176", "EventDate": "1/19/2015", "Employer": "Acme Tools LLC",
           "Address1": "12 Main Street", "Address2": "", "City": "TOWN", "State": "TEXAS", "Zip": "75001",
           "Latitude": "32.1", "Longitude": "-96.1", "Primary NAICS": "332510", "Hospitalized": "1.00",
           "Amputation": "0.00", "Loss of Eye": "0.00", "Inspection": "", "Final Narrative": "Acme Tools crushed a hand.",
           "Nature": "111", "NatureTitle": "Fractures", "Part of Body": "4429", "Part of Body Title": "Hand",
           "Event": "6411", "EventTitle": "Caught", "Source": "3", "SourceTitle": "Press",
           "Secondary Source": "", "Secondary Source Title": "", "FederalState": "1"}
    return pd.DataFrame([{**row, **changes}])


def test_minimize_drops_identifiers_and_coarsens_dates():
    out, stats = osha.minimize(reports())
    record = out.iloc[0].to_dict()
    assert not set(osha.DROPPED) & set(out.columns)
    assert record["report_id"] == 931176 and record["event_month"] == "2015-01"
    assert record["narrative"] == "[EMPLOYER] crushed a hand." and record["inspected"] is False
    assert record["hospitalized"] == 1 and record["secondary_source_code"] is None
    assert stats["masked_narratives"] == 1
    with pytest.raises(ValueError, match="UPA"):
        osha.minimize(pd.concat([reports(), reports()]))
    with pytest.raises(ValueError, match="whole"):
        osha.minimize(reports(Amputation="0.50"))
    assert osha.minimize(reports(Amputation=""))[0].amputation.iloc[0] is None


def test_prepare_is_checksummed_and_immutable(tmp_path, monkeypatch):
    buffer = io.BytesIO()
    frame = pd.concat([reports(), reports(UPA="2", EventDate="3/1/2016", Inspection="999")])
    with zipfile.ZipFile(buffer, "w") as zipped:
        zipped.writestr(osha.MEMBER, frame.to_csv(index=False))
    source = tmp_path / "source"
    source.mkdir()
    (source / osha.ARCHIVE).write_bytes(buffer.getvalue())
    monkeypatch.setattr(osha, "SHA256", hashlib.sha256(buffer.getvalue()).hexdigest())
    manifest = osha.prepare(source, tmp_path / "landing")
    assert sorted(manifest["files"]) == ["reports/sir_2015.jsonl", "reports/sir_2016.jsonl"]
    line = json.loads((tmp_path / "landing/reports/sir_2016.jsonl").read_text().splitlines()[0])
    assert line["report_id"] == 2 and line["inspected"] is True and "Employer" not in line
    assert osha.prepare(source, tmp_path / "landing") == manifest
    (tmp_path / "landing/reports/sir_2015.jsonl").write_text("tampered")
    with pytest.raises(ValueError, match="immutable"):
        osha.prepare(source, tmp_path / "landing")
    (source / osha.ARCHIVE).write_bytes(b"not the archive")
    with pytest.raises(ValueError, match="checksum"):
        osha.prepare(source, tmp_path / "other")


def test_masking_v2_catches_shortened_employer_names_without_masking_ordinary_words():
    common = frozenset({"power", "line", "beef", "packing", "cooler", "crew"})
    # Eval v2's leak: the narrative used the first words of a longer legal name (fictional names here).
    narrative = "An employee of Prairie Beef was hurt near the beef cooler."
    employer = "Prairie Beef Packing Company, LLC"
    assert osha.mask(narrative, employer, [])[0] == narrative  # masking v1 missed it
    assert osha.mask(narrative, employer, [], common=common)[0] == \
        "An employee of [EMPLOYER] was hurt near the beef cooler."
    # Ordinary words in a name are masked only as a capitalized name, never as plain words.
    assert osha.mask("The power line fell on a Power Line crew.", "Power Line Services Inc", [], common=common)[0] == \
        "The power line fell on a [EMPLOYER] crew."
    # A distinctive single word, a dba trading name, and a state name left alone.
    assert osha.mask("A Zorbex worker fell.", "Zorbex Fresh Meats", [], common=common)[0] == "A [EMPLOYER] worker fell."
    assert osha.mask("A cook at Joe's Diner was burned.", "Smith Holdings dba Joe's Diner", [], common=common)[0] == \
        "A cook at [EMPLOYER] was burned."
    assert osha.mask("In Millbrook, Georgia, a Georgia Summit lineman fell.", "Georgia Summit Power Company", [],
                     common=common)[0] == "In Millbrook, Georgia, a [EMPLOYER] lineman fell."


def test_common_words_count_lowercase_uses_only():
    narratives = ["The auger caught him.", "An auger turned.", "Zorbex auger.", "Zorbex hired him."]
    assert osha.common_words(narratives, 3) == frozenset({"auger"})  # "Zorbex" is never lowercase
    assert osha.minimize(reports())[1]["masking_version"] == "v3"
    assert osha.minimize(reports(), masking_version="v2")[1]["masking_version"] == "v2"


def test_masking_v3_absorbs_only_own_employer_fragments_next_to_the_mask():
    common = frozenset({"worker", "crew"})
    # Fictional names. v2 masks "Zorbex" but leaves the spaced initials before it.
    employer = "G&H Zorbex Machining, Inc."
    narrative = "An employee of G & H Zorbex fell."
    assert osha.mask(narrative, employer, [], common=common)[0] == "An employee of G & H [EMPLOYER] fell."
    assert osha.mask(narrative, employer, [], common=common, fragments=True)[0] == "An employee of [EMPLOYER] fell."
    assert osha.absorb_fragments("J.B. [EMPLOYER] crew", "J B Zorbex LLC") == ("[EMPLOYER] crew", 1)
    assert osha.absorb_fragments("[EMPLOYER] Machining staff", employer) == ("[EMPLOYER] staff", 1)
    # Repeats until nothing changes: both fragments go.
    assert osha.absorb_fragments("G&H Machining [EMPLOYER]", employer) == ("[EMPLOYER]", 2)
    # Left alone: other letters, other companies' words, lowercase words, business words and states.
    for text, name in [("OSHA [EMPLOYER] inspected", "J B Zorbex LLC"), ("Acme [EMPLOYER] crew", employer),
                       ("the [EMPLOYER] crew", "The Zorbex Group"), ("[EMPLOYER] Company staff", "Zorbex Company"),
                       ("Workers in Texas [EMPLOYER] fell", "Texas Zorbex"), ("A [EMPLOYER] crew", "A Zorbex")]:
        assert osha.absorb_fragments(text, name) == (text, 0), text


def test_prepare_delta_lands_only_changed_reports(tmp_path, monkeypatch):
    buffer = io.BytesIO()
    frame = pd.concat([reports(UPA="1", Employer="G&H Zorbex Machining, Inc.",
                               **{"Final Narrative": "An employee of G & H Zorbex fell."}),
                       reports(UPA="2", EventDate="3/1/2016")])
    with zipfile.ZipFile(buffer, "w") as zipped:
        zipped.writestr(osha.MEMBER, frame.to_csv(index=False))
    source = tmp_path / "source"
    source.mkdir()
    (source / osha.ARCHIVE).write_bytes(buffer.getvalue())
    monkeypatch.setattr(osha, "SHA256", hashlib.sha256(buffer.getvalue()).hexdigest())
    monkeypatch.setattr(osha, "COMMON_MIN_DOCUMENTS", 50)
    osha.prepare(source, tmp_path / "v2", masking_version="v2")
    manifest = osha.prepare_delta(source, tmp_path / "v2", tmp_path / "v3")
    assert list(manifest["files"]) == ["reports/changed.jsonl"]
    assert (manifest["rows"], manifest["snapshot_rows"], manifest["delta_of"], manifest["masking_version"]) == (1, 2, "v2", "v3")
    changed = [json.loads(line) for line in (tmp_path / "v3/reports/changed.jsonl").read_text().splitlines()]
    assert [(r["report_id"], r["narrative"]) for r in changed] == [(1, "An employee of [EMPLOYER] fell.")]
    assert b"\r" not in (tmp_path / "v3/manifest.json").read_bytes()
    assert osha.prepare_delta(source, tmp_path / "v2", tmp_path / "v3") == manifest
    (tmp_path / "v3/reports/changed.jsonl").write_text("tampered")
    with pytest.raises(ValueError, match="immutable"):
        osha.prepare_delta(source, tmp_path / "v2", tmp_path / "v3")
