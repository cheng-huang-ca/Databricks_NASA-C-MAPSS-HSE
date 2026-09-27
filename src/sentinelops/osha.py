"""Verified OSHA Severe Injury Reports acquisition and data minimization (no cloud compute).

Source: U.S. Department of Labor, OSHA Severe Injury Reports, federal jurisdiction,
January 2015 - November 2025. Federal government work: public domain; DOL requests
attribution and prohibits implying endorsement. Employer identities, street addresses,
city, ZIP, coordinates and inspection numbers are dropped before landing, and
employer names/addresses inside narratives are masked. The raw archive stays local.
"""
import hashlib
import io
import json
from pathlib import Path
import re
import urllib.request
import zipfile

import pandas as pd

URL = "https://www.osha.gov/sites/default/files/January2015toNovember2025.zip"
SHA256 = "a3f7f434e200fb956131f12277378e592993a25db3f328716fbece106f846bb0"
ARCHIVE = "January2015toNovember2025.zip"
MEMBER = "January2015toNovember2025.csv"
ATTRIBUTION = "U.S. Department of Labor, Occupational Safety and Health Administration (osha.gov)"
DROPPED = ["Employer", "Address1", "Address2", "City", "Zip", "Latitude", "Longitude", "Inspection", "EventDate"]
CODES = {"Nature": "nature", "Part of Body": "body_part", "Event": "event", "Source": "source",
         "Secondary Source": "secondary_source"}
TITLES = {"Nature": "NatureTitle", "Part of Body": "Part of Body Title", "Event": "EventTitle",
          "Source": "SourceTitle", "Secondary Source": "Secondary Source Title"}
SUFFIX = re.compile(r"(?:[\s,]+(?:inc|llc|l\.l\.c|corp|corporation|co|company|ltd|lp|llp|pllc|pc|dba)\.?)+$"
                    r"|\s*#\s*\d+$", re.IGNORECASE)
STREET = re.compile(r"\b\d{1,6}(?:\s+(?:[A-Z][A-Za-z]*\.?|\d+(?:st|nd|rd|th)?)){1,4}\s+(?:Street|St|Avenue|Ave"
                    r"|Road|Rd|Drive|Dr|Boulevard|Blvd|Lane|Ln|Highway|Hwy|Parkway|Pkwy|Court|Ct|Way|Place|Pl)\b\.?")
ZIP = re.compile(r"\b([A-Z]{2}),?\s+\d{5}(?:-\d{4})?\b")
# Masking v2 (landing osha_sir/v2): narratives also use shortened employer names (the first words
# of a longer legal name, a trading name after "dba", or one distinctive word). Eval v2 found the
# assistant repeating one that v1 masking missed.
# Masking v3 (landing osha_sir/v3, a delta of the reports it changes): a review found fragments of
# a report's own employer name left right next to [EMPLOYER], such as initials ("G&H [EMPLOYER]")
# or a name word no variant covered. Only fragments of that report's employer are absorbed.
MASKING_VERSION = "v3"
DBA = re.compile(r"\s+(?:d\s*/\s*b\s*/\s*a|d\.?b\.?a\.?|doing business as)\s+", re.IGNORECASE)
NAME_WORD = re.compile(r"[A-Za-z][A-Za-z'&-]+")
US_STATES = frozenset(
    "alabama alaska arizona arkansas california colorado connecticut delaware florida georgia hawaii idaho illinois "
    "indiana iowa kansas kentucky louisiana maine maryland massachusetts michigan minnesota mississippi missouri "
    "montana nebraska nevada ohio oklahoma oregon pennsylvania tennessee texas utah vermont virginia washington "
    "wisconsin wyoming".split())
# Business words that never identify an employer on their own.
BUSINESS_WORDS = frozenset(
    "the a an and of for inc llc co corp company corporation ltd lp llp services service group construction "
    "industries industry enterprises enterprise usa us america american national international united general city "
    "county state department farms farm systems solutions holdings holding partners associates contractors "
    "contracting manufacturing products company's inc's".split())


def common_words(narratives, min_documents: int = 50) -> frozenset:
    """Words that appear in lowercase in at least `min_documents` narratives: ordinary vocabulary.
    Counting lowercase occurrences keeps proper nouns (always capitalized) out of the list."""
    counts = {}
    for text in narratives:
        for word in {w for w in NAME_WORD.findall(text) if w.islower()}:
            counts[word] = counts.get(word, 0) + 1
    return frozenset(w for w, c in counts.items() if c >= min_documents)


def name_variants(employer: str, common: frozenset, single_min: int = 4) -> list[tuple[str, bool]]:
    """Shortened forms of an employer name, as (text, proper_noun_only), longest first.

    Leading word sequences (2+ words) of each name and of any "dba" trading name, and each
    distinctive single word (not a business word, not ordinary vocabulary). Every variant is
    masked only when capitalized as a proper noun: a lowercase "auger" in a narrative is the
    machine, not "Auger Services" (case-insensitive matching over-masked ~290-1,100 such words).
    """
    variants = []
    for name in DBA.split(employer):
        words = NAME_WORD.findall(SUFFIX.sub("", name.strip()))
        ordinary = [w.lower() in BUSINESS_WORDS or w.lower() in common for w in words]
        for k in range(len(words), 1, -1):
            if not all(w.lower() in BUSINESS_WORDS for w in words[:k]):
                variants.append((" ".join(words[:k]), True))
        # A state name alone doesn't identify an employer, and the state is a structured field anyway.
        variants += [(w, True) for w, plain in zip(words, ordinary)
                     if not plain and len(w) >= single_min and w.lower() not in US_STATES]
    return sorted(dict.fromkeys(variants), key=lambda v: -len(v[0]))


INITIALS = re.compile(r"(?:[A-Z]{1,3}\s*&\s*)+[A-Z]{1,3}|(?:[A-Z]\.){2,4}|[A-Z]{2,5}")
FRAGMENT = r"(?:[A-Z]{1,3}\s*&\s*)+[A-Z]{1,3}|(?:[A-Z]\.){2,4}|[A-Za-z0-9][A-Za-z0-9'&-]*"


def own_fragment(token: str, employer: str) -> bool:
    """Initials of consecutive words of this employer's name ("G&H", "J.B."), or a capitalized word
    of the name that isn't a business word or a state."""
    parts = re.findall(r"[A-Za-z0-9]+", employer)
    if INITIALS.fullmatch(token):
        letters = re.sub(r"[^A-Z]", "", token)
        if len(letters) >= 2 and letters in "".join(p[0].upper() for p in parts):
            return True
    word = token.lower()
    return (token[:1].isupper() and word in {p.lower() for p in parts}
            and word not in BUSINESS_WORDS and word not in US_STATES)


BEFORE_MASK = re.compile(r"(?<![\w&.])(" + FRAGMENT + r")\s+\[EMPLOYER\]")
AFTER_MASK = re.compile(r"\[EMPLOYER\]\s+(" + FRAGMENT + r")(?![\w&])")


def absorb_fragments(text: str, employer: str) -> tuple[str, int]:
    """Merge own-employer fragments directly before or after [EMPLOYER] into the mask (masking v3).
    Repeats until nothing changes, so "G&H Tool [EMPLOYER]" loses both fragments."""
    absorbed = 0

    def merge(match):
        nonlocal absorbed
        if own_fragment(match.group(1), employer):
            absorbed += 1
            return "[EMPLOYER]"
        return match.group(0)

    while True:
        start = absorbed
        text = AFTER_MASK.sub(merge, BEFORE_MASK.sub(merge, text))
        if absorbed == start:
            return text, absorbed


def download(destination: Path) -> Path:
    destination.mkdir(parents=True, exist_ok=True)
    archive = destination / ARCHIVE
    if not archive.exists():
        request = urllib.request.Request(URL, headers={"User-Agent": "Mozilla/5.0 SentinelOps/0.1"})
        with urllib.request.urlopen(request, timeout=300) as response:
            payload = response.read()
        if hashlib.sha256(payload).hexdigest() != SHA256:
            raise ValueError("OSHA archive checksum mismatch")
        archive.write_bytes(payload)
    if hashlib.sha256(archive.read_bytes()).hexdigest() != SHA256:
        raise ValueError("Cached OSHA archive checksum mismatch")
    return archive


def read_reports(archive: Path) -> pd.DataFrame:
    with zipfile.ZipFile(archive) as zipped:
        if zipped.namelist() != [MEMBER]:
            raise ValueError("Unexpected OSHA archive members")
        return pd.read_csv(io.BytesIO(zipped.read(MEMBER)), dtype=str, keep_default_na=False, encoding="utf-8")


def _literal(text: str, value: str, token: str, proper_noun_only: bool = False, min_length: int = 5,
             matched: list | None = None) -> tuple[str, int]:
    value = " ".join(value.split())
    if len(value) < min_length:  # Too short to mask without clobbering ordinary words.
        return text, 0
    if proper_noun_only:  # each word capitalized, the rest in any case: "Power Line", "POWER LINE"
        parts = [re.escape(p[0].upper()) + "(?i:" + re.escape(p[1:]) + ")" for p in value.split()]
        pattern, flags = r"(?<!\w)" + r"\s+".join(parts) + r"(?!\w)", 0
    else:
        pattern, flags = r"(?<!\w)" + r"\s+".join(re.escape(part) for part in value.split()) + r"(?!\w)", re.IGNORECASE
    if matched is not None:
        matched += re.findall(pattern, text, flags=flags)
    return re.subn(pattern, token, text, flags=flags)


def mask(narrative: str, employer: str, addresses: list[str], common: frozenset | None = None,
         single_min: int = 4, detail: bool = False, fragments: bool = False):
    """Mask the report's employer and addresses, street addresses and state+ZIP; normalize whitespace.

    Without `common` this is masking v1 (full and suffix-free employer names), which produced
    landing osha_sir/v1. With `common` (see common_words) it also masks shortened names (v2), and
    with `fragments` it also absorbs own-employer fragments next to the mask (v3).
    With `detail`, also returns counts of the kinds of matches, for measuring over-masking.
    """
    text, total, matched = " ".join(narrative.split()), 0, []
    core = SUFFIX.sub("", employer.strip())
    names = [(employer, False, 5), (core, False, 5)]
    if common is not None:
        names += [(v, proper, single_min if " " not in v else 5) for v, proper in name_variants(employer, common, single_min)]
    for value, proper, min_length in names:
        text, count = _literal(text, value, "[EMPLOYER]", proper, min_length, matched)
        total += count
    if fragments:
        text, count = absorb_fragments(text, employer)
        total += count
    for address in addresses:
        text, count = _literal(text, address, "[ADDRESS]")
        total += count
    text, streets = STREET.subn("[ADDRESS]", text)
    text, zips = ZIP.subn(r"\1 [ZIP]", text)
    if not detail:
        return text, total + streets + zips
    info = {"employer_matches": len(matched), "lowercase_matches": sum(m.islower() for m in matched),
            "single_word_matches": sum(" " not in m.strip() for m in matched),
            "state_name_matches": sum(m.strip().lower() in US_STATES for m in matched)}
    return text, total + streets + zips, info


def _count(values: pd.Series) -> pd.Series:
    """Whole, non-negative counts; blank means unknown and stays null."""
    values = values.str.strip()
    numbers = pd.to_numeric(values.where(values != ""), errors="raise")
    known = numbers.dropna()
    if (known < 0).any() or (known % 1 != 0).any():
        raise ValueError("Severity counts must be non-negative whole numbers")
    return pd.Series([None if pd.isna(n) else int(n) for n in numbers], index=values.index, dtype=object)


def _blank_to_none(values: pd.Series) -> pd.Series:
    values = values.str.strip().astype(object)
    return values.where(values != "", None)


COMMON_MIN_DOCUMENTS = 50  # chosen on the full corpus: v1 -> v2 capitalized leaks 37 -> 0 (first two name words)


def minimize(reports: pd.DataFrame, common_min_documents: int = COMMON_MIN_DOCUMENTS,
             masking_version: str = MASKING_VERSION) -> tuple[pd.DataFrame, dict]:
    """Keep only fields the assistant and analytics need; one row per unique UPA.
    masking_version "v2" reproduces landing osha_sir/v2; "v3" also absorbs own-employer fragments."""
    if masking_version not in ("v2", "v3"):
        raise ValueError("minimize() produces masking v2 or v3")
    if reports.UPA.duplicated().any() or not reports.UPA.str.fullmatch(r"\d+").all():
        raise ValueError("UPA must be a unique numeric report key")
    dates = pd.to_datetime(reports.EventDate, format="%m/%d/%Y", errors="raise")
    common = common_words(reports["Final Narrative"], common_min_documents)
    masked = [mask(n, e, [a1, a2], common=common, fragments=masking_version == "v3") for n, e, a1, a2 in
              zip(reports["Final Narrative"], reports.Employer, reports.Address1, reports.Address2)]
    out = pd.DataFrame({
        "report_id": reports.UPA.astype("int64"),
        "osha_id": reports.ID,
        "event_month": dates.dt.strftime("%Y-%m"),
        "state": reports.State.str.strip(),
        "naics": reports["Primary NAICS"].str.strip(),
        "federal_state": reports.FederalState.astype("int64"),
        "hospitalized": _count(reports.Hospitalized),
        "amputation": _count(reports.Amputation),
        "loss_of_eye": _count(reports["Loss of Eye"]),
        "inspected": reports.Inspection.str.strip() != "",
        "narrative": [text for text, _ in masked],
    })
    for column, name in CODES.items():
        out[f"{name}_code"] = _blank_to_none(reports[column])
        out[f"{name}_title"] = _blank_to_none(reports[TITLES[column]])
    stats = {"rows": len(out), "masked_narratives": sum(1 for _, n in masked if n),
             "mask_replacements": sum(n for _, n in masked), "dropped_columns": DROPPED,
             "masking_version": masking_version, "common_word_min_documents": common_min_documents}
    return out, stats


def _manifest(stats: dict) -> dict:
    return {"source": URL, "archive_sha256": SHA256, "member": MEMBER, "attribution": ATTRIBUTION,
            "license": "U.S. federal government work (public domain); attribution requested; no endorsement implied",
            "coverage": "Severe injury reports (hospitalization, amputation, loss of an eye) with event dates "
                        "2015-01-01 to 2025-11-30 as published; OSHA's page says State Plan reports are "
                        "excluded from its dashboard dataset. FederalState flag kept as published.",
            **stats, "files": {}}


def _write_immutable(destination: Path, files: dict, manifest: dict) -> dict:
    for name, payload in files.items():
        path = destination / name
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists() and path.read_bytes() != payload:
            raise ValueError(f"Refusing to overwrite immutable landing file: {name}")
        path.write_bytes(payload)
        manifest["files"][name] = hashlib.sha256(payload).hexdigest()
    (destination / "manifest.json").write_bytes((json.dumps(manifest, indent=2) + "\n").encode())
    return manifest


def _jsonl(records) -> bytes:
    return ("\n".join(json.dumps(r, sort_keys=True, ensure_ascii=False) for r in records) + "\n").encode("utf-8")


def prepare_delta(source: Path, previous: Path, destination: Path, masking_version: str = MASKING_VERSION) -> dict:
    """A later masking version as a delta: only the reports whose minimized record differs from the
    previous landing version (complete snapshot), in one file. Silver keeps each report's latest
    landed copy, so the delta supersedes exactly those reports."""
    reports, stats = minimize(read_reports(download(source)), masking_version=masking_version)
    before = {r["report_id"]: r for f in sorted((previous / "reports").glob("*.jsonl"))
              for r in map(json.loads, f.read_text(encoding="utf-8").splitlines())}
    records = reports.to_dict("records")
    if {r["report_id"] for r in records} != set(before):
        raise ValueError("The previous landing version covers different reports")
    changed = [r for r in records if r != before[r["report_id"]]]
    if any({k for k in r if r[k] != before[r["report_id"]][k]} != {"narrative"} for r in changed):
        raise ValueError("A later masking version may only change narratives")
    stats = {**stats, "rows": len(changed), "delta_of": previous.name, "snapshot_rows": len(records)}
    return _write_immutable(destination, {"reports/changed.jsonl": _jsonl(changed)}, _manifest(stats))


def prepare(source: Path, destination: Path, masking_version: str = "v2") -> dict:
    """Write immutable, per-year minimized JSONL files plus a SHA-256 manifest (a complete snapshot)."""
    reports, stats = minimize(read_reports(download(source)), masking_version=masking_version)
    files = {f"reports/sir_{year}.jsonl": _jsonl(part.to_dict("records"))
             for year, part in reports.groupby(reports.event_month.str[:4], sort=True)}
    return _write_immutable(destination, files, _manifest(stats))


if __name__ == "__main__":
    # osha_v1 and osha_v2 are immutable and already landed; masking v3 lands as a delta of v2.
    print(json.dumps({k: v for k, v in prepare_delta(Path("data/osha"), Path("data/landing/osha_v2"),
                                                     Path("data/landing/osha_v3")).items() if k != "files"}, indent=2))
