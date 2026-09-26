# Auto Loader and Lakeflow ingestion

This milestone reuses `sentinelops_dev`, its managed ADLS storage, and the existing
`sentinelops_dev.sentinelops_dev.landing` volume. No new Azure infrastructure or
external-location grants are needed. Bronze, Silver and Gold have separate UC
schemas. The original bootstrap tables, job and model version 2 remain available.
The `cmapss_train` job trains from the Gold tables; see "Train from Gold" below.

## Data contract

`python -m sentinelops.landing` rechecks the archive MD5, validates both FD001
files, copies their original bytes, and creates explicit engine-keyed JSON from
the official RUL file's ordered lines. A SHA256 manifest records landed content.
The versioned landing prefix is immutable: never overwrite a consumed file or
reset a pipeline checkpoint to introduce a correction.

- Bronze `cmapss_lines`: Auto Loader TEXT, original line and file provenance.
  TEXT has a fixed schema; malformed numeric content stays in the original line
  rather than a synthetic `_rescued_data` column.
- Bronze `cmapss_labels`: Auto Loader JSON, explicit schema and rescued-data field.
- Silver `cmapss_quarantine`: invalid width, filename, keys or numeric values.
- Silver `cmapss_conflicts`: keys with more than one distinct numeric payload.
  The original payloads and source paths remain in Bronze.
- Silver `cmapss_observations`: valid rows, equal-payload duplicates collapsed,
  conflicting keys excluded. Expectations record quality outcomes.
- Silver `cmapss_endpoint_labels`: fail on rescued, invalid or duplicate labels.
- Gold `cmapss_features`: current sensors, trailing ten observations and five-row
  differences, partitioned by dataset/subset/split/unit. Composite UC primary key:
  `(dataset, subset, split, unit, cycle)`. Keys are informational; the graph and
  integration assertions enforce uniqueness.
- Gold `cmapss_training_labels`: capped remaining life from completed training
  trajectories only. This benchmark input is sealed run-to-failure data; do not
  land unfinished fleet trajectories as `train`.
- Gold `cmapss_test_endpoints`: last observed test cycle joined to the official,
  uncapped endpoint label. No test labels enter features.

- Gold `cmapss_condition_stats` and `cmapss_condition_features` (task E): see
  "FD002–FD004 and operating conditions" below.

Bronze is checkpointed incremental ingestion. Silver and Gold are materialized
views with batch semantics: Lakeflow chooses incremental refresh or recomputation.
This permits global conflict detection and correct historical windows without an
unbounded streaming deduplication state store. For this small benchmark,
recomputation is acceptable; larger fleet workloads need separate design/testing.
Late earlier cycles can legitimately change later features on refresh. Windows
are past-only at each cycle, not a guarantee that historical revisions are frozen.

## Run and verify

```powershell
. ./scripts/Use-SentinelOps.ps1
.venv/Scripts/python.exe -m sentinelops.landing
# First upload only. Do not use --overwrite for consumed inputs.
.tools/databricks/databricks.exe fs cp data/landing/v1 dbfs:/Volumes/sentinelops_dev/sentinelops_dev/landing/cmapss_ingest/v1 --recursive
.tools/databricks/databricks.exe bundle validate --strict -t dev
.tools/databricks/databricks.exe bundle deploy -t dev
# Inspect current costs/compute and allow for billing delay before any run.
.tools/databricks/databricks.exe bundle run -t dev cmapss_ingest --no-wait
# After ingestion succeeds, run independent parity/billing checks:
.tools/databricks/databricks.exe bundle run -t dev cmapss_verify --no-wait
```

The separate integration job verifies every table count, all feature values against the
bootstrap implementation, official test labels, training labels, and the declared
primary key. Evidence is saved to `cmapss_ingest/v1/verification.json` in the volume.
Do not run `--full-refresh` for normal ingestion; it resets ingestion state.

## Train from Gold

```powershell
# After a completed cmapss_ingest update; never concurrently with one.
.tools/databricks/databricks.exe bundle run -t dev cmapss_train
```

`jobs/train_medallion.py` reads one subset (`FD001`) from `gold.cmapss_features`,
`gold.cmapss_training_labels` and `gold.cmapss_test_endpoints`. It fails unless
every training feature key has exactly one label (and vice versa), labels respect
the 125-cycle cap, and every test engine has exactly one official label. Rows are
restored to bootstrap `(unit, cycle)` order, so engine-disjoint selection is
reproducible. The run logs content digests of the exact rows used, MLflow dataset
inputs naming the Gold tables, upstream quarantine/conflict counts, metrics and
predictions, then registers a tagged UC version and moves `challenger` to it.
Quarantined and conflicting rows never reach training.

Since task E, both training jobs pass `--tuning cv`: a randomized search scored
by engine-grouped cross-validation on the fit engines only, then one score on the
held-out validation engines, logged with `mlflow.models.evaluate` (see
[OPERATIONS.md](OPERATIONS.md#promotion-gate-cmapss_promote)). `--tuning leaves`
is the original three-candidate selection.

## Cost controls

The ingestion job is manual, standard performance, one concurrent run, no
application/flow/update retries, and a 1,500-second overall timeout (900 until
FD002–FD004 landed; it covers the STANDARD-mode wait plus the larger first
update). The pipeline
is triggered and has development mode explicitly disabled at the bundle preset
level so that it does not retain development compute. There is no recurring
schedule. These controls reduce exposure but are not a $10 hard cutoff: serverless
scaling and delayed billing prevent a strict dollar guarantee. Review posted Azure
costs and available Databricks usage before adding runs; stop discretionary tests
if headroom cannot be established. The starter warehouse stays stopped.
Verification has its own 900-second limit (600 before FD002–FD004) and does not
refresh the pipeline.
The first combined run completed ingestion but timed out during the second
compute startup; separating the jobs avoids repeating successful ingestion.
STANDARD mode can wait ~7 minutes for resources; ingestion runs have taken
11–12 minutes of their 15-minute limit. If one times out while waiting,
rerun it before raising the limit.

The optional `scripts/prepare_quality_probe.py` prepares a nine-line file outside
the normal upload directory: two repeated canonical observations, five malformed
rows, and two conflicting payloads for a synthetic unit 999. Upload it only for
an intentional quality test. The verifier recognizes this file and requires
exactly five quarantined rows, one conflicting key, and unchanged canonical
counts/features. This tests incremental ingestion and duplicate resistance;
a subsequent no-input run separately tests checkpoint replay behavior.

The incremental test ran on September 23, 2026 using these steps. Don't upload
the probe again:

```powershell
.venv/Scripts/python.exe scripts/prepare_quality_probe.py
.tools/databricks/databricks.exe fs cp data/quality_probe/train_FD001_quality.txt dbfs:/Volumes/sentinelops_dev/sentinelops_dev/landing/cmapss_ingest/v1/trajectories/train_FD001_quality.txt
.tools/databricks/databricks.exe bundle run -t dev cmapss_ingest
.tools/databricks/databricks.exe bundle run -t dev cmapss_verify
# Recheck costs before a further no-input ingestion + verification run.
```

The probe has now been uploaded and has passed. The volume therefore
permanently contains it, and the verifier expects its counts on every later
run. Bronze appended only the 9 probe lines, and the expected 5 quarantined
rows and 1 conflict appeared with canonical counts unchanged. A later no-input
update appended nothing and planned every materialized view as NO_OP.
Summarize any update's flow metrics and refresh techniques with
`scripts/pipeline_update_evidence.py`. The CLI's `list-pipeline-events`
leaves out event details, so use the REST events API as that script's
docstring shows. See STATUS.md for run IDs and evidence files.

## FD002–FD004 and operating conditions (task E)

**Landing v2.** `python -m sentinelops.landing` now writes two immutable
versions: `data/landing/v1` (FD001, unchanged byte for byte) and
`data/landing/v2` (FD002–FD004: six trajectory files, three label files, 39 MB).
Each subset's row and engine counts are pinned (`landing.CARDINALITY`), and
manifests are written as LF bytes so Windows and CI produce identical files.
`v2` was uploaded to dev once, with `scripts/upload_landing.py` (never
overwrites), and CI uploads both versions to staging.

**Ingestion without touching FD001's flows.** The pipeline setting
`sentinelops.landing_later` lists later versions. Each gets its own append flows
into the two Bronze tables (`cmapss_lines_v2`, `cmapss_labels_v2`), the masking
v2 pattern, so the v1 flows and their checkpoints are untouched. The Silver
endpoint-label expectation now checks each subset's engine range (100, 259, 100,
248 test engines). FD001's Gold rows are unchanged, so its training digests are
too: adding subsets doesn't trigger a retrain.

**Operating conditions.** FD002 and FD004 mix six flight conditions, which shift
the sensors far more than wear does. A condition is the altitude setting rounded
half up (0, 10, 20, 25, 35 or 42 thousand feet). Within-condition spread is at
most 0.02, and FD001 and FD003 fly only at 0.
- Gold `cmapss_condition_stats`: per subset and condition, each model sensor's
  mean and sample standard deviation over **training rows only** (no test rows,
  no labels).
- Gold `cmapss_condition_features`: each sensor standardized with those
  statistics, then the same trailing 10-cycle means and 5-cycle differences as
  `cmapss_features`, plus the condition. A test row in a condition with no
  training statistics fails the update instead of being standardized with
  nothing.
- The statistics use all training engines, including the ones later held out
  for validation. They're unsupervised, and this is a common simplification;
  the validation score is marginally less independent than a per-fold
  normalization would make it.

**Verification.** `cmapss_verify` reads the landed manifests (`v1`, then `v2`),
rechecks every checksum and asserts exact per-subset counts. For each subset it
compares both Gold feature tables and the statistics with the pandas references
(`sentinelops.model.features`, `sentinelops.conditions`), plus the training and
official test labels. The standardized features allow 1e-9 for Spark and pandas
summing in different orders. The rerun check compares counts only between runs
with the same landing versions.

## REST API ingestion: Open-Meteo weather

A third ingestion style, next to files (C-MAPSS, OSHA): a REST API is fetched
into immutable landing, then Auto Loader takes it through Bronze, Silver and
Gold. The weather can be joined to OSHA heat-illness reports by state and
month.

**Source and terms** (checked September 24, 2026):
- Open-Meteo Historical Weather API, `archive-api.open-meteo.com/v1/archive`.
  It needs no key. The free API is for non-commercial use only, which covers
  this portfolio project.
- The data is CC BY 4.0: credit "Weather data by Open-Meteo.com" with a link
  to https://open-meteo.com/. ERA5 is from the Copernicus Climate Change
  Service. Table comments and fetch logs carry the attribution.
- Free limits are 600 calls/minute, 5,000/hour and 10,000/day. A call is one
  location, up to 10 variables and up to 14 days, and longer requests count
  fractionally more. One location-year of daily data counts as ~26 calls.

**What is fetched (landing v1, frozen).**
- Daily ERA5 `temperature_2m_max`, `temperature_2m_mean` and
  `apparent_temperature_max` in °C, over each location's local day.
- One point per state for the 20 states with the most OSHA heat-illness
  reports (92% of 2,624). Each point is the state's largest city.
- One request per location and calendar year, 2015–2025: 220 requests,
  ~5,740 weighted calls. `sentinelops.open_meteo.LOCATIONS_V1` holds the
  locations, and a test pins the spec's digest. Changing locations, variables
  or the model means a new landing version, never an edit of v1.

**Fetch task** (`jobs/fetch_open_meteo.py`, stdlib HTTP, no new dependency):
- **File names come from the request.** Example:
  `era5_texas--houston_2015-01-01_2015-12-31.json`. The pipeline parses the
  location and window back out of the name.
- **Raw bytes, never overwritten.** Each response lands byte for byte through
  the Files API with `overwrite=False`, a single PUT, so a file appears whole
  or not at all. Files already present are skipped, so reruns resume.
- **Budgeted.** A run spends at most `--max-weighted-calls` (4,000). The
  hourly and daily limits, at 80%, are reduced by the calls recorded in
  earlier runs' fetch logs. The limits are per IP and serverless IPs are
  shared, so this is a self-imposed guardrail. Requests are paced under 480
  weighted calls per rolling minute.
- **Failures.** HTTP 429 stops the run at once, with no retry. 5xx and network
  errors get 2 retries with backoff. Any other 4xx, or a body that isn't a
  JSON object, is rejected and not landed.
- **Log.** Every request (file, weight, bytes, SHA-256, time) is written to
  `landing/open_meteo/_fetch_log/<job run id>.json`, outside the Auto Loader
  path.
- **Responses aren't reproducible byte for byte.** `generationtime_ms`
  varies, and `utc_offset_seconds` is the zone's offset *at request time*
  (−18000 for a whole 2015 Houston series fetched in September). Re-fetching
  would therefore never match; the SHA-256 in the log proves what was landed.
  Silver ignores the offset.

**Pipeline** `weather_open_meteo` (`pipelines/weather.py`):
- Bronze `open_meteo_daily`: Auto Loader JSON, one row per file, with an
  explicit schema covering every response field and `_rescued_data`.
- Silver `weather_daily` (key `location_id, date`): one row per day. Days that
  fail a rule go to `weather_quarantine` with the names of the rules they
  failed:
  - schema conforms;
  - known file name;
  - °C units;
  - complete series (every array as long as the window);
  - each date at its position;
  - values present, plausible, and the mean not above the max.

  The latest landed copy of a day wins.
- Gold `weather_state_monthly` (key `state, month`): monthly means and
  extremes, hot-day counts (max ≥ 90 °F and 95 °F; apparent max ≥ 90 °F and
  103 °F) and a completeness flag. `state` and `month` match OSHA's `state`
  and `event_month`.

**Verify task** (`jobs/verify_weather.py`):
- Every landed file still matches its logged SHA-256.
- The Files API refuses an overwrite (a probe).
- Bronze has exactly one row per file.
- Quarantine, Silver and Gold equal a pandas recomputation from the raw files
  (`sentinelops.weather`): Silver bit for bit, Gold averages to 1e-9.

```powershell
. ./scripts/Use-SentinelOps.ps1
.tools/databricks/databricks.exe bundle run -t dev weather_ingest --no-wait
# A backfill above the hourly budget needs another run an hour or more later;
# once everything has landed, a rerun makes no API calls and appends nothing.
```

**Caveats.**
- One city is a proxy for a whole state; Houston is not El Paso.
- ERA5 is a ~28 km grid cell, smoother and usually cooler at the daily
  maximum than a city station.
- Apparent temperature is Open-Meteo's feels-like measure, not the NWS heat
  index.
- OSHA's reports cover federal-jurisdiction workplaces only. Any
  weather/injury association is descriptive, not causal.

## Streaming ingestion: Event Hubs Kafka endpoint (bounded demo)

The third ingestion style: events. C-MAPSS FD001 test trajectories are
replayed from a local machine (standing in for an edge gateway) into Azure
Event Hubs. The `cmapss_stream` pipeline reads them back through Event Hubs'
Kafka-compatible endpoint. The namespace bills by the hour, so it exists only
during a demo.

**Infrastructure** (`infra/eventhubs-demo.bicep`; deploy with what-if first,
delete the same day):
- namespace `evhns-sentinelops-7s5fwy`: Standard, 1 TU, TLS 1.2, no
  auto-inflate. Standard is the lowest tier with the Kafka endpoint.
- hub `cmapss-telemetry`: 2 partitions, 1-day retention;
- two namespace-level SAS policies, `cmapss-listen` (Listen only) and
  `cmapss-send` (Send only).

**Prices** (Azure Retail Prices, `westus2`, CAD, checked September 25, 2026):
- throughput unit CAD 0.0416/hour;
- ingress CAD 0.0388 per million events (one event per 64 KB);
- the price list also carries a "Standard Kafka Endpoint" meter at CAD
  0.1247/hour, although the pricing page lists Kafka as included in Standard.
  The worst case, ~CAD 0.17/hour, is what's budgeted; check the meter-level
  cost afterwards.

**Secrets:**
- The listen key goes in the Databricks-backed secret scope
  `sentinelops-eventhubs` (key `cmapss-listen`). The pipeline reads it with
  `dbutils.secrets.get`.
- The send key is read with the signed-in Azure CLI inside the producer's
  process and is used only to sign a one-hour SAS token.
- Neither key is printed, written to disk or passed on a command line
  (`scripts/eventhubs_demo.py put-secret` / `produce`).

**Producer** (`sentinelops.stream`, standard library only):
- One JSON event per trajectory row, with a deterministic `event_id`
  (`CMAPSS-FD001-test-001-001`). Values stay JSON numbers: Python's shortest
  round-trip repr parses back to the same doubles Spark casts from the text.
- Each engine goes to one partition (`(unit − 1) mod 2`), in cycle order.
- Events are posted with the REST batch API (`/partitions/{p}/messages`,
  ≤ 256 KB and 500 events per batch).
- Nothing is retried, because a timed-out batch may still have been accepted.
  The local send log (`artifacts/eventhubs/`) makes a second send refuse to
  run.

**Pipeline** `cmapss_stream` (`pipelines/stream.py`, triggered):
- Bronze `cmapss_stream_events`: the Kafka source over `SASL_SSL`/`PLAIN` on
  port 9093 (user `$ConnectionString`), `startingOffsets=earliest` and
  `failOnDataLoss=true`, since events expire after the retention period. It
  keeps the payload text plus partition, offset and enqueued time.
- Silver `cmapss_stream_observations`: `from_json` with an explicit schema,
  and 5 rules:
  - a JSON object;
  - exactly the expected fields (`json_object_keys`);
  - valid keys;
  - an `event_id` that matches the keys;
  - finite values.

  Failures go to `cmapss_stream_quarantine` with the rules they failed. Silver
  keeps one row per key (the first-enqueued copy) and counts `copies`.

**Verify** (`jobs/verify_stream.py`):
- Bronze holds one event per replayed observation, and every `event_id`
  once.
- Quarantine is empty, and Silver has no duplicates.
- Every streamed observation equals the file-ingested `silver.cmapss_observations`
  row for the same key, bit for bit, with no key missing on either side.
- It also reports whether offsets are contiguous and each engine's cycles
  arrived in order.

```powershell
. ./scripts/Use-SentinelOps.ps1
az provider register --namespace Microsoft.EventHub --wait   # once per subscription
az deployment group what-if --resource-group rg-sentinelops-dev --template-file infra/eventhubs-demo.bicep
az deployment group create --resource-group rg-sentinelops-dev --name eventhubs-demo --template-file infra/eventhubs-demo.bicep
.venv/Scripts/python.exe scripts/eventhubs_demo.py put-secret
.tools/databricks/databricks.exe bundle deploy -t dev
.venv/Scripts/python.exe scripts/eventhubs_demo.py produce
.tools/databricks/databricks.exe bundle run -t dev cmapss_stream_ingest --no-wait
# Afterwards, the same day:
az eventhubs namespace delete --resource-group rg-sentinelops-dev --name evhns-sentinelops-7s5fwy
.tools/databricks/databricks.exe secrets delete-scope sentinelops-eventhubs
```

Without the namespace, `cmapss_stream_ingest` can't connect, so run it only
during a demo. A recreated namespace gets new keys, so rerun `put-secret`. Its
empty hub starts at offset 0, while the pipeline's checkpoint remembers the
old offsets, so reset the stream (a full refresh of `cmapss_stream`) before
reusing it.

## Official references checked September 23, 2026

- [Auto Loader schema behavior](https://learn.microsoft.com/en-us/azure/databricks/ingestion/cloud-object-storage/auto-loader/schema)
- [Lakeflow Python API and declaration restrictions](https://learn.microsoft.com/en-us/azure/databricks/ldp/developer/python-ref)
- [Serverless pipeline modes](https://learn.microsoft.com/en-us/azure/databricks/ldp/serverless)
- [Unity Catalog feature table keys](https://learn.microsoft.com/en-us/azure/databricks/machine-learning/feature-store/uc/feature-tables-uc)
