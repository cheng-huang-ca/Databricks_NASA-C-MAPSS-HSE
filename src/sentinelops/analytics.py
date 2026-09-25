"""Analytics marts behind the AI/BI dashboards and the Genie space (pandas side).

- OSHA injury facts: one row per Gold report with harmonized categories from
  sentinelops.extraction.truth, so trends are comparable across OSHA's 2024 coding change.
  No narrative, document text or employer data; categories only.
- Fleet status: the champion's prediction at each fleet engine's last observed cycle, with an
  illustrative risk band.
The column comments below are applied in Unity Catalog, where Genie and dashboard authors read them.
"""
import re

import numpy as np
import pandas as pd

from sentinelops import extraction

EVENT_NAMES = {"violence_or_animal": "Violence or animal", "transportation": "Transportation",
               "fire_or_explosion": "Fire or explosion", "fall_slip_trip": "Fall, slip or trip",
               "harmful_exposure": "Harmful substance or environment",
               "contact_with_object": "Contact with object or equipment",
               "overexertion": "Overexertion or bodily reaction"}
BODY_PART_NAMES = {"head": "Head", "neck": "Neck", "trunk": "Trunk", "upper_extremities": "Upper extremities",
                   "lower_extremities": "Lower extremities", "body_systems": "Body systems",
                   "multiple_body_parts": "Multiple body parts"}
SOURCE_NAMES = {"chemicals": "Chemicals", "containers_furniture_fixtures": "Containers, furniture, fixtures",
                "machinery": "Machinery", "parts_and_materials": "Parts and materials",
                "persons_plants_animals_minerals": "Persons, plants, animals, minerals",
                "structures_and_surfaces": "Structures and surfaces",
                "tools_instruments_equipment": "Tools, instruments, equipment", "vehicles": "Vehicles",
                "other_sources": "Other sources"}
NATURE_NAMES = {"amputation": "Amputation", "fracture": "Fracture", "burn": "Burn", "electric_shock": "Electric shock",
                "heat_illness": "Heat illness", "head_brain_injury": "Concussion or brain injury",
                "cut_or_puncture": "Cut or puncture", "crushing": "Crushing injury",
                "internal_injury": "Internal injury", "poisoning_or_respiratory": "Poisoning or respiratory",
                "sprain_strain_or_bruise": "Sprain, strain or bruise", "other": "Other specific injury"}
CATEGORY_NAMES = {"event": EVENT_NAMES, "nature": NATURE_NAMES, "body_part": BODY_PART_NAMES, "source": SOURCE_NAMES}
# Truth that no rule can place: nonclassifiable OIICS titles, or a nonspecific nature title.
NOT_CLASSIFIABLE, UNSPECIFIED = "Not classifiable", "Unspecified"
NAICS_SECTORS = {"11": "Agriculture, forestry, fishing and hunting", "21": "Mining, quarrying, and oil and gas",
                 "22": "Utilities", "23": "Construction", "31": "Manufacturing", "32": "Manufacturing",
                 "33": "Manufacturing", "42": "Wholesale trade", "44": "Retail trade", "45": "Retail trade",
                 "48": "Transportation and warehousing", "49": "Transportation and warehousing",
                 "51": "Information", "52": "Finance and insurance", "53": "Real estate and rental and leasing",
                 "54": "Professional, scientific, and technical services",
                 "55": "Management of companies and enterprises",
                 "56": "Administrative, support and waste management services", "61": "Educational services",
                 "62": "Health care and social assistance", "71": "Arts, entertainment, and recreation",
                 "72": "Accommodation and food services", "81": "Other services (except public administration)",
                 "92": "Public administration"}
UNKNOWN_SECTOR = "Unknown"

# (name, Spark type, comment); the order is the table's column order.
INJURY_FACT_COLUMNS = [
    ("report_id", "BIGINT NOT NULL", "OSHA Severe Injury Report key (UPA), also the assistant's citation key. One row per "
                                     "report, i.e. per incident, not per injured worker."),
    ("event_month", "STRING", "Incident month, YYYY-MM. Dates were coarsened to the month before upload."),
    ("event_date", "DATE", "First day of the incident month, for time series."),
    ("event_year", "INT", "Incident year. 2025 covers January to November only."),
    ("coding_era", "STRING", "OSHA coding scheme in effect: 2015-23 or 2024-25. The category columns are already "
                             "harmonized across the change; never compare raw OIICS codes across it."),
    ("state", "STRING", "U.S. state or territory of the incident, upper case."),
    ("naics", "STRING", "Employer industry code (NAICS, 2 to 6 digits); null when blank. Food manufacturing is 311."),
    ("naics_sector", "STRING", "First two NAICS digits."),
    ("industry_sector", "STRING", "NAICS sector name; Unknown when the code is blank or unrecognized."),
    ("event_category", "STRING", "How the injury happened: OIICS event division, harmonized to the 2024 scheme."),
    ("nature_category", "STRING", "Injury type mapped from OSHA's nature title by ordered rules (amputation before "
                                  "fracture before burn ...); Unspecified when OSHA's title is nonspecific."),
    ("body_part_category", "STRING", "Body part: OIICS division, harmonized (hips count as lower extremities)."),
    ("source_category", "STRING", "What produced the injury: OIICS source division, harmonized to the 2024 scheme."),
    ("hospitalized", "INT", "Employees hospitalized in the incident, as reported."),
    ("amputation", "INT", "Employees with an amputation, as reported; null means unknown."),
    ("loss_of_eye", "INT", "Employees who lost an eye, as reported; null means unknown."),
    ("inspected", "BOOLEAN", "Whether OSHA linked an inspection to the report (the inspection number was dropped)."),
]
INJURY_FACTS_COMMENT = ("OSHA Severe Injury Reports (federal OSHA, Jan 2015 to Nov 2025), one row per report with "
                        "harmonized injury categories. Source: U.S. Department of Labor, OSHA; no endorsement implied. "
                        "No narratives, employer names or addresses.")
# Inputs read from gold.osha_documents: never the narrative or document text.
INJURY_FACT_INPUTS = ["report_id", "event_month", "event_year", "state", "naics", "naics_sector", "hospitalized",
                      "amputation", "loss_of_eye", "inspected", "nature_title"] + \
                     [f"{field}_{kind}" for field in ("event", "body_part", "source") for kind in ("code", "title")]

RISK_BANDS = (("critical", 20), ("warning", 50))  # predicted RUL <= limit, checked in order; else healthy
FLEET_STATUS_COLUMNS = [
    ("subset", "STRING NOT NULL", "C-MAPSS subset, e.g. FD001."),
    ("unit", "INT NOT NULL", "Fleet engine number. NASA's test engines play the in-service fleet in this simulation."),
    ("last_cycle", "INT", "Last observed operating cycle of the engine."),
    ("model_name", "STRING", "Unity Catalog model that produced the prediction."),
    ("model_version", "INT", "The @champion version when this table was refreshed."),
    ("predicted_rul", "DOUBLE", "Predicted remaining useful life at the last cycle, in cycles. The model was trained "
                                "on labels capped at 125, so healthy engines are underestimated by design."),
    ("risk_band", "STRING", "critical: predicted RUL <= 20 cycles; warning: <= 50; healthy: > 50. Illustrative "
                            "planning thresholds, not a validated maintenance policy."),
    ("actual_rul", "INT", "Official NASA endpoint RUL once ground truth has arrived; null before. For monitoring "
                          "only, never for choosing models."),
    ("scored_at", "TIMESTAMP", "When the prediction was logged."),
]
FLEET_STATUS_COMMENT = ("Current RUL outlook per fleet engine: the @champion prediction at each engine's last "
                        "observed cycle. Rebuilt by the analytics_refresh job; rerun it after cmapss_retrain.")

# Comments for the monitoring tables, which their jobs create without descriptions.
MONITORING_COMMENTS = {
    "cmapss_predictions": (None, {
        "unit": "Fleet engine number.", "cycle": "Operating cycle of the observation.",
        "model_version": "Model version that made the prediction; a newly promoted champion scores the whole fleet.",
        "predicted_rul": "Predicted remaining useful life in cycles.",
        "actual_rul": "True RUL from the official endpoint label (endpoint RUL + cycles to the last observation); "
                      "null until ground truth arrives."}),
    "cmapss_model_performance": (
        "Append-only snapshots of the fleet model's accuracy once delayed labels arrive, per model version and "
        "segment. Use the latest computed_at per model_version. Operational evidence only; never used to choose "
        "or promote models.", {
            "segment": "all; endpoint (last cycle per engine, the official benchmark); actual_le_125 (true RUL <= "
                       "125); rul_000_024 ... rul_126_plus (true-RUL buckets; 126+ is underestimated by design).",
            "rows": "Labelled predictions in the segment.",
            "rmse": "Root mean squared error in cycles.", "mae": "Mean absolute error in cycles.",
            "nasa_score": "NASA asymmetric score (late predictions cost more); not meaningful for segment all.",
            "bias": "Mean predicted minus actual RUL; positive means overestimated, the unsafe direction.",
            "computed_at": "Snapshot time."}),
    "cmapss_feature_drift": (
        "Append-only drift snapshots: PSI of fleet features vs training features. Fleet engines are observed "
        "earlier in life, so raw psi mostly measures age; judge drift by psi_age_matched.", {
            "feature": "Model input feature.",
            "psi": "Population stability index vs all training rows (mostly reflects the fleet's younger ages).",
            "psi_age_matched": "PSI vs training rows reweighted to the fleet's cycle mix; above 0.2 is an alert.",
            "computed_at": "Snapshot time."}),
    "cmapss_alerts": (
        "Audit trail of monitoring threshold checks, one row per check per alerts run (breached or not). Includes "
        "deliberate breach tests run with a lowered PSI threshold.", {
            "check": "rmse_increase (vs the version's first snapshot), psi_age_matched (per feature) or "
                     "snapshot_age_hours.",
            "subject": "Segment, feature or snapshot table the check applies to.",
            "value": "Observed value.", "threshold": "Threshold in force for that run.",
            "breached": "True when value exceeded threshold; any breach fails the run.",
            "alert_job_run": "Job run that recorded the check.", "checked_at": "Check time."}),
}


def category(label, field: str) -> str:
    """Display name for a harmonized label; None (unscoreable truth) becomes Not classifiable / Unspecified."""
    if label is None or (isinstance(label, float) and np.isnan(label)):
        return UNSPECIFIED if field == "nature" else NOT_CLASSIFIABLE
    return CATEGORY_NAMES[field][label]


def sector(naics_sector) -> str:
    return NAICS_SECTORS.get(str(naics_sector or "")[:2], UNKNOWN_SECTOR)


def injury_facts(frame: pd.DataFrame) -> pd.DataFrame:
    """Harmonized per-report facts from Gold document columns (INJURY_FACT_INPUTS), in table column order."""
    truth, _ = extraction.truth(frame)
    out = pd.DataFrame({
        "report_id": frame.report_id.astype("int64").to_numpy(),
        "event_month": frame.event_month.to_numpy(),
        "event_date": pd.to_datetime(frame.event_month + "-01", format="%Y-%m-%d").dt.date.to_numpy(),
        "event_year": frame.event_year.astype("int32").to_numpy(),
        "coding_era": truth.era.to_numpy(),
        "state": frame.state.to_numpy(),
        "naics": frame.naics.to_numpy(),
        "naics_sector": frame.naics_sector.to_numpy(),
        "industry_sector": frame.naics_sector.map(sector).to_numpy()})
    for field in ("event", "nature", "body_part", "source"):
        out[f"{field}_category"] = [category(label, field) for label in truth[field]]
    for column in ("hospitalized", "amputation", "loss_of_eye"):  # null = unknown, never zero
        out[column] = frame[column].astype("Int32").reset_index(drop=True)
    out["inspected"] = frame.inspected.astype("boolean").reset_index(drop=True)
    return out[[name for name, _, _ in INJURY_FACT_COLUMNS]]


def risk_band(predicted_rul: float) -> str:
    for band, limit in RISK_BANDS:
        if predicted_rul <= limit:
            return band
    return "healthy"


def fleet_status(predictions: pd.DataFrame, last_cycles: pd.DataFrame, version: int) -> pd.DataFrame:
    """One row per fleet engine: the given version's prediction at the engine's last observed cycle.

    `predictions` is the inference log (subset, unit, cycle, model_name, model_version, predicted_rul,
    actual_rul, scored_at); `last_cycles` has subset, unit and last_cycle for every fleet engine.
    """
    scored = predictions[predictions.model_version == version]
    out = last_cycles.merge(scored, left_on=["subset", "unit", "last_cycle"], right_on=["subset", "unit", "cycle"],
                            how="left", validate="one_to_one")
    if out.predicted_rul.isna().any():
        missing = out.loc[out.predicted_rul.isna(), "unit"].tolist()[:5]
        raise ValueError(f"Version {version} has not scored the last cycle of engines {missing}; run cmapss_score")
    out = out.assign(risk_band=out.predicted_rul.map(risk_band), model_version=int(version),
                     actual_rul=out.actual_rul.astype("Int32"))  # null until ground truth arrives
    return out[[name for name, _, _ in FLEET_STATUS_COLUMNS]].sort_values(["subset", "unit"]).reset_index(drop=True)


def ddl(table: str, columns: list, comment: str, primary_key: list[str]) -> str:
    """CREATE TABLE IF NOT EXISTS with column comments and an informational primary key."""
    def quote(text):
        return "'" + text.replace("\\", "\\\\").replace("'", "\\'") + "'"
    body = ",\n  ".join(f"{name} {kind} COMMENT {quote(text)}" for name, kind, text in columns)
    key = f"{table.split('.')[-1]}_pk"
    return (f"CREATE TABLE IF NOT EXISTS {table} (\n  {body},\n  CONSTRAINT {key} PRIMARY KEY ({', '.join(primary_key)}))"
            f"\nCOMMENT {quote(comment)}")


def comment_statements(table: str, table_comment: str | None, columns: dict[str, str]) -> list[str]:
    """Idempotent COMMENT ON TABLE / ALTER COLUMN statements."""
    def quote(text):
        return "'" + text.replace("\\", "\\\\").replace("'", "\\'") + "'"
    out = [f"COMMENT ON TABLE {table} IS {quote(table_comment)}"] if table_comment else []
    return out + [f"ALTER TABLE {table} ALTER COLUMN {name} COMMENT {quote(text)}" for name, text in columns.items()]


# --- Dashboard and Genie definitions: checks that need no warehouse -------------------------------
FORBIDDEN_COLUMNS = ("narrative", "document", "osha_id", "employer", "address")
_BACKTICKED = re.compile(r"`([^`]+)`")


def gold_tables(sql: str) -> set[str]:
    """Gold tables a query reads, as bare names (catalog-qualified or not)."""
    return set(re.findall(r"\bgold\.([a-z_][a-z0-9_]*)", sql, flags=re.IGNORECASE))


def widget_columns(dashboard: dict) -> dict[str, set[str]]:
    """Dataset name -> columns referenced by widget field expressions (so they can be checked
    against the dataset's real columns)."""
    used: dict[str, set[str]] = {}
    for page in dashboard["pages"]:
        for item in page["layout"]:
            for query in item["widget"].get("queries", []):
                names = used.setdefault(query["query"]["datasetName"], set())
                for field in query["query"]["fields"]:
                    names |= set(_BACKTICKED.findall(field["expression"])) - {"*", "associative_filter_predicate_group"}
    return used


def dataset_sql(dataset: dict) -> str:
    return "".join(dataset["queryLines"]) if "queryLines" in dataset else dataset["query"]


def genie_spaces(config: dict, target: str) -> dict:
    """Genie spaces a bundle target deploys, from a resources file: shared ones plus the target's own
    (the space is dev-only, under `targets.dev`)."""
    shared = (config.get("resources") or {}).get("genie_spaces") or {}
    own = (((config.get("targets") or {}).get(target) or {}).get("resources") or {}).get("genie_spaces") or {}
    return {**shared, **own}
