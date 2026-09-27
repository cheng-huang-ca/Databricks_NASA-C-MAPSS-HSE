"""Held-out Genie evaluation: the space's benchmark questions, each asked once through the
Conversation API and compared with its reference SQL's result (run by scripts/genie_eval.py).

The comparison is lenient where Genie is legitimately free and strict where it matters:
- Genie may add columns and name them freely; every reference column must match one of them.
- Genie may round: a number matches when it equals the reference rounded to Genie's decimals.
- Genie may list more rows than a top-k reference; its first k rows must match, in order.
Genie's prose summary is recorded for review; its SQL result is the source of truth.
"""
import re

NUMBER = re.compile(r"^-?\d+(?:\.(\d+))?(?:[eE][-+]?\d+)?$")


def benchmarks(config: dict, catalog: str) -> list[dict]:
    """(id, question, sql) of every benchmark in the bundle's Genie spaces, with the catalog resolved."""
    out = []
    for target in (config.get("targets") or {}).values():
        for space in ((target or {}).get("resources") or {}).get("genie_spaces", {}).values():
            for item in space["serialized_space"].get("benchmarks", {}).get("questions", []):
                (answer,) = item["answer"]
                out.append({"id": item["id"], "question": item["question"][0],
                            "sql": "".join(answer["content"]).replace("${var.catalog}", catalog)})
    return out


def same_value(reference, genie) -> bool:
    if reference is None or genie is None:
        return reference is None and genie is None
    reference, genie = str(reference).strip(), str(genie).strip()
    number = NUMBER.match(genie)
    if number and NUMBER.match(reference):
        decimals = len(number.group(1) or "") if "e" not in genie.lower() else 12
        return round(float(reference), decimals) == round(float(genie), decimals)
    return reference.lower() == genie.lower()


def compare(reference_rows: list[list], genie_rows: list[list] | None) -> dict:
    """Verdict "match", "mismatch" or "no_result", with which reference columns found a match."""
    if not genie_rows:
        return {"verdict": "no_result", "matched_columns": []}
    k = len(reference_rows)
    if len(genie_rows) < k:
        return {"verdict": "mismatch", "matched_columns": [], "why": f"{len(genie_rows)} rows, reference {k}"}
    rows = genie_rows[:k]
    width = max(len(r) for r in rows)
    matched = []
    for j in range(len(reference_rows[0]) if k else 0):
        expected = [r[j] for r in reference_rows]
        if any(all(same_value(e, row[c] if c < len(row) else None) for e, row in zip(expected, rows))
               for c in range(width)):
            matched.append(j)
    verdict = "match" if k and len(matched) == len(reference_rows[0]) else "mismatch"
    return {"verdict": verdict, "matched_columns": matched}
