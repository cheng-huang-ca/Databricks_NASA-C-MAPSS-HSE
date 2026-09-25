# Build status

Last verified: September 25, 2026, 16:25 UTC (agent deployment: the OSHA assistant served with the Review App, regression-checked, endpoint deleted afterwards).

**At a glance.**

- **Progress:** 35 of 40 tracked tasks are done. The rest are optional (ML
  depth, Lakehouse Monitoring) or last (the demo script and write-up); 4 are
  not started and 1 is deferred.
- **Environments:** `dev` (developer), `staging` and `prod` (each deployed and
  run by its own service principal through GitHub Actions with Databricks
  OIDC, no secrets). Staging ingests and verifies C-MAPSS on every deploying
  push; prod is deploy-only behind a required reviewer. [CICD.md](CICD.md).
- **Live state (read-only checks):** no active job runs, no classic clusters,
  no Event Hubs namespace or secret scopes, no Vector Search endpoints, and
  all pipelines IDLE, and no custom serving endpoints (the OSHA agent's
  endpoint was deleted at 16:20 UTC after you used the Review App). The
  starter warehouse is STOPPED (2X-Small, 5-minute auto-stop). `@champion`
  is v3 (READY).
- **Local tooling:** Windows Application Control started blocking
  `.venv\Scripts\python.exe` on September 25 (~05:00 UTC), and still did at
  06:26 UTC. Tests and Python scripts can't run locally until you allow it;
  CI on GitHub runs them.
- **Cost:** September 24 closed at **CAD 14.14** (near final), as
  `system.billing` predicted. September 25: CAD 1.78 posted by 14:25 UTC,
  projected ≈ 3–3.5. Event Hubs has billed only ingress events (CAD 0.0005)
  so far; no Kafka-endpoint charge yet. All against your credits, which
  expire October 10. See "Cost and runtime controls".
- **Git:** every milestone is committed on `main` and pushed to the public
  repository https://github.com/cheng-huang-ca/Databricks_NASA-C-MAPSS-HSE.

## Task status

This is the single tracker for the full scope in [SentinelOps.md](../SentinelOps.md).
**Done** means verified in Azure (or locally, where noted) with evidence in
this file. **Next** marks the agreed order. **Deferred** means held back for
cost or prerequisites, with the reason given.

### Platform and governance

| Task | Status | Evidence or next action |
|---|---|---|
| Azure foundation: ADLS Gen2, workspace, access connector, UC catalog | Done | `infra/main.bicep`; "Azure" section below |
| Bundle deployment (dev target, strict validation, manual jobs) | Done | `databricks.yml`, `resources/*.yml`; 16 jobs, 4 pipelines, 2 dashboards and 1 Genie space deployed; a test enforces job guardrails, including no serverless auto-retries |
| Cost visibility: meter-level Azure cost query | Done | Found an always-on NAT gateway/IP, ~CAD 1.7/day ("Cost and runtime controls") |
| Azure budget alert | Done | Budget `sentinelops-dev-monthly` (your choice): CAD 150/month on both SentinelOps resource groups; emails at 50/80/100% of actual and 100% of forecast; `infra/budget.json`. A tripwire (alerts lag 8–24 h), not a cutoff |
| Databricks billing tables (`system.billing`) access | Done | The auto-provisioned metastore had no metastore admin; the account group `sentinelops-metastore-admins` (you, the only member) now is (your approval). You hold `USE SCHEMA` + `SELECT` on `system.billing`. A one-off query (run `839204309723200`) read usage ~3.8 h behind real time, vs ~9 h for Azure Cost Management |
| dev/staging/prod catalogs, service principals, `run_as` | Done | Catalogs `sentinelops_staging`/`sentinelops_prod` (bound to this workspace), principals `sentinelops-staging-ci`/`sentinelops-prod-ci` (plain users, `ALL PRIVILEGES` on their own catalog only), production-mode bundle targets with `run_as`; each principal created its target's 16 jobs, 4 pipelines and schemas. [CICD.md](CICD.md), `cicd-first-run.json` |
| Secrets in Key Vault or a secret scope | Done (demo) | Databricks-backed scope `sentinelops-eventhubs` held the Event Hubs listen key, read by the pipeline with `dbutils.secrets.get`; the send key never left the producer's process. Deleted with the namespace |
| Private Link / VNet hardening | Deferred | Cost and complexity; public endpoints use authenticated access only |

### Data engineering

| Task | Status | Evidence or next action |
|---|---|---|
| C-MAPSS FD001 Auto Loader + Lakeflow medallion with quarantine/conflicts | Done | Pipeline `77ecd502…`; parity verified |
| Incremental ingestion probe and no-input rerun | Done | `medallion-probe-*.json`, `medallion-rerun-update.json` |
| FD002–FD004 (multiple operating conditions) | Not started | Keys and `--subset` already support it; needs condition-aware features |
| REST API ingestion (Open-Meteo weather) | Done | Job `weather_ingest`: budgeted, resumable fetch of 220 raw responses into immutable landing → Auto Loader Bronze/Silver/Gold → verify against the raw files; two backfill runs, then a rerun with 0 API calls and 0 rows appended; `weather-backfill.json` |
| Event Hubs (Kafka endpoint) streaming | Done (bounded demo) | 13,096 FD001 events replayed locally (stdlib REST producer) → Kafka endpoint → pipeline `cmapss_stream`: all bit-identical to the file-ingested observations; rerun appended 0; namespace lived 43.5 min, then deleted; `eventhubs-demo.json` |

### Predictive-maintenance ML

| Task | Status | Evidence or next action |
|---|---|---|
| Training from Gold with dataset lineage | Done | Version 3; `medallion-training.json` |
| Validation-gated promotion (never reads test labels) | Done | v3 is `@champion`; `promotion-decision.json` |
| Fleet batch scoring into an idempotent inference log | Done | `gold.cmapss_predictions`; `fleet-scoring-*.json` |
| Delayed-label performance and age-matched drift monitoring | Done | `gold.cmapss_model_performance`, `gold.cmapss_feature_drift` |
| Alerts on drift and performance tables | Done | Task `alerts` in `cmapss_retrain`: relative RMSE, age-matched PSI and freshness thresholds, logged to `gold.cmapss_alerts`; a breach fails the run (breach test run `955572570273055`). No email notification (your choice) |
| Orchestrated retraining (ingest → verify → train → promote → score) | Done | Job `cmapss_retrain`; retrains only when the Gold training digests differ from the champion's. Unchanged-data run `599725542930474` SUCCESS in 23 min; `cmapss-retrain.json` |
| Real-time serving demo with Gold-format features | Done | Champion v3 served with scale-to-zero: all 13,096 fleet rows bit-identical to the batch log; single-row p50 81 ms; inference table logged every request; endpoint deleted. [OPERATIONS.md](OPERATIONS.md#real-time-serving-bounded-demo), `serving-demo.json` |
| Hyperparameter tuning, `mlflow.evaluate`, sequence baseline | Not started | |
| Lakehouse Monitoring inference profile | Not started (optional) | Job-computed metrics already cover the demo |

### Safety GenAI assistant (OSHA)

| Task | Status | Evidence or next action |
|---|---|---|
| Source, license and provenance (checksum-pinned) | Done | [SAFETY_RAG.md](SAFETY_RAG.md) |
| Privacy minimization before upload | Done | Identifying columns dropped; masking v2 masks 420 narratives (459 replacements; v1 masked 282) |
| OSHA medallion pipeline to Gold `osha_documents` | Done | 105,993 documents, 3 quarantined |
| Retrieval cost decision (no Vector Search endpoint) | Done | Chosen by you; endpoint would be ~CAD 9.3/day |
| Document embeddings (`osha_embed`) | Done | 105,993 vectors; `osha-embedding-backfill.json` |
| Exact retrieval + code-based retrieval evaluation (1,024 vs 256 dimensions) | Done | Dense P@10 0.882 vs TF-IDF 0.786; 256 dims = 1,024 quality at 1/4 memory; `osha-retrieval-eval.json` |
| Grounded answers with `[report_id]` citations, abstention, MLflow tracing | Done | GPT-OSS-120B over 256-dim retrieval; code-checked citations; threshold 0.6511 + model decline; `osha-answer-eval.json` |
| LLM-judge evaluation (correctness, groundedness, relevance) | Done (28 held-out questions) | Llama 3.3 70B judge: 28/28 correct answer/decline decisions; on answers, correctness 11/12, groundedness 12/12 |
| Larger answer evaluation (eval v2) | Done (60 held-out questions) | 58/60 correct decisions; the model declined 20 of 21 unanswerable questions above the threshold. **One answer named an employer** (a masking gap); `osha-answer-eval-v2.json` |
| Employer-name masking gap | Done | Masking v2 (landing `osha_sir/v2`): capitalized leading-name leaks 37 → 0; 140 documents re-embedded; no employer names in 76 answers; 13/16 held-out identity requests declined. `osha-masking-v2.json` |
| Structured extraction scored against OSHA codes | Done | GPT-OSS-120B matches a supervised TF-IDF model on event, nature and body part (0.935/0.943/0.948) but trails on source (0.760 vs 0.825); `osha-extraction-eval.json` |
| Agent deployment / review app | Done (name scan pending) | `osha_assistant` v1 on endpoint `sentinelops-osha-agent` (Model Serving, `agents.deploy`, scale-to-zero, Review App). Regression through the endpoint: the same decisions as in-process on the identity (13/16 declined) and eval v2 (59/60) sets. You used the Review App; the endpoint was then deleted (57 min). `osha-agent-deployment.json` |

### Analytics and delivery

| Task | Status | Evidence or next action |
|---|---|---|
| Unit tests (131) and CI workflow | Done | Run on every pull request and code push by GitHub Actions (`.github/workflows/ci.yml`); `main` requires the `Unit tests` check (branch protection, enforced for non-admins) |
| Git history | Done | Branch `main`, one commit per milestone, pushed to the public repository |
| GitHub repository, CI runs, OIDC deployment to staging/prod | Done | Public repo `cheng-huang-ca/Databricks_NASA-C-MAPSS-HSE`. Run `36098067567` SUCCESS: tests → staging deploy as its principal (github-oidc) → C-MAPSS landing, ingest (`459623161285845`) and verify (`104076494798937`) in staging → prod deploy after your approval. Pinned actions, no secrets; `cicd-first-run.json`. Pull-request path proven by PR #1 (run `36104902332`: tests, then validation as the staging principal via the `pull_request` subject) |
| AI/BI dashboards and Genie space | Done | Fleet health and Safety incidents dashboards, Genie space over 6 curated Gold tables, `analytics_refresh` job; Genie 7/8 held-out questions fully right (one miscounted summary); [ANALYTICS.md](ANALYTICS.md) |
| SQL warehouse right-sizing | Done | Starter warehouse Small → 2X-Small, auto-stop 10 → 5 min (your approval); a wake-up now costs ~CAD 0.35, not ~2.3 |
| Demo script and portfolio write-up | Not started | Last |

Recommended order (details in HANDOVER.md): optional ML depth, agent
deployment and small follow-ups, in any order → demo script and write-up
(last). The budget alert, dashboards/Genie, serving demo, eval v2, masking v2,
REST API ingestion, the Event Hubs demo and CI/CD are done.

## Current milestone: agent deployment (task D)

Your choices (September 25): Model Serving through `agents.deploy()`,
scale-to-zero, the endpoint kept until you say to delete it, the second
safety layer deferred, and the name scan left until local Python works.
Details: [SAFETY_RAG.md](SAFETY_RAG.md#deployed-agent-model-serving-agent-framework);
evidence [osha-agent-deployment.json](osha-agent-deployment.json).

- **Packaging** (`osha_agent_log`, run `838941933038146`): the evaluated
  assistant, unchanged, as an MLflow `ResponsesAgent` with the index and
  report text as artifacts (≈ 145 MB). The threshold recalibrated to 0.6511,
  the evaluated value. Registered `osha_assistant` v1; the version reloaded
  from the registry passed a two-question smoke test.
- **Deployment** (`osha_agent_deploy`, run `1016924296649877`, after one
  internal library-installation failure): endpoint `sentinelops-osha-agent`
  READY in about 9 minutes, with the Review App, tracing to
  `sentinelops-safety-rag` and an inference table. No monitoring judges run.
  - It came up **always-on**: `agents.deploy()` ignores
    `scale_to_zero_enabled` (its flag is `scale_to_zero`). It was switched to
    scale-to-zero in place about 4 minutes after it was ready, and the job
    now refuses an always-on endpoint.
- **Regression** (`osha_agent_eval`, run `197582367273355`): the same
  decisions and citations as in-process on all 16 identity questions (13
  declined) and 59/60 on eval v2 (the same `v2_injection` miss). Top-1 scores
  moved by up to 0.0025 (single REST embeddings vs batched `ai_query`).
- **Review App, then cleanup:** you used the Review App; the endpoint was
  deleted at 16:20 UTC after 57 minutes. The model and the inference table
  stay, and `osha_agent_deploy` brings the agent back in about 10 minutes.
- **Pending:** the employer-name scan of the endpoint's answers (local Python
  is blocked).
- **Cost:** ≈ CAD 1.0–1.3 (jobs ~0.6, endpoint ≤ 0.4, tokens and judges
  ~0.3).

## Earlier milestone: small follow-ups (task F)

Your choices (September 25): the analytics refresh inside retraining,
placed beside the alerts; the change through a pull request; branch
protection; `system.billing` access through a metastore admin group; and
deleting the two scratch diagnostics. Evidence:
[followups-f.json](followups-f.json).

- **A latent bug, fixed.** `analytics_refresh` looked for the Genie space in
  `resources`, but the CI/CD milestone moved it under `targets.dev`, so the
  next refresh would have failed at its Genie checks. It now takes
  `--target ${bundle.target}`; a unit test covers the lookup.
- **`cmapss_retrain` refreshes the dashboard marts.** Its new `analytics`
  task runs the `analytics_refresh` job after `monitor`, beside `alerts`.
  Dev run `248147539009900`: **SUCCESS in 30 min**.
  - Unchanged data: `train` skipped (same digests), no `@challenger`, 0
    pending rows, endpoint RMSE 18.3415 again, 47 alert checks with 0
    breaches.
  - `analytics` started `analytics_refresh` run `129586591044115` in
    parallel with `alerts`. It found the Genie space, ran all 7 dashboard
    datasets and 6 Genie example queries, and rebuilt `cmapss_fleet_status`
    for champion v3 (100 engines: 71 healthy, 14 warning, 15 critical).
  - About CAD 0.4–0.6 of serverless.
  - Staging and prod have no OSHA data, so `analytics` would fail there; CI
    never runs `cmapss_retrain` in either.
- **Pull-request path, first run.** PR #1 exercised the `pull_request`
  federation subject. Run `36104608730` passed tests and staging's strict
  validation, then failed prod's: as the staging principal, prod's root path
  is that principal's own folder, whose permissions prod doesn't list. Pull
  requests now validate prod without `--strict` (the prod job still validates
  strictly as prod); run `36104902332` passed with 123 tests.
- **Branch protection:** `main` requires `Unit tests`, for non-admins only.
- **Billing access:** see the task table and "Cost and runtime controls".
  `verify` in this run recorded a same-day billing snapshot for the first
  time.

## Earlier milestone: environments and CI/CD

Staging and prod targets, each deployed and run by its own service principal
from GitHub Actions, with no stored secrets. Details: [CICD.md](CICD.md);
evidence [cicd-first-run.json](cicd-first-run.json).

- **Your choices:**
  - a public repository (now `cheng-huang-ca/Databricks_NASA-C-MAPSS-HSE`);
  - Databricks OIDC federation;
  - C-MAPSS ingested in staging, prod deploy-only;
  - approval of the service principals, catalogs, grants and federation
    policies, and one recreate of empty misnamed schemas.
- **Identity:**
  - Principals `sentinelops-staging-ci` and `sentinelops-prod-ci` are plain
    workspace users, with `ALL PRIVILEGES` only on their own catalog, and
    `CAN_USE` on the warehouse.
  - Federation policies accept only this repository's ID-pinned subjects:
    `environment:staging`, `pull_request` and `environment:prod`.
  - GitHub environments: staging deploys from `main` only; prod also needs a
    required reviewer.
- **Catalogs:** `sentinelops_staging` and `sentinelops_prod` use managed
  storage under the existing external location, are bound to this workspace,
  and have predictive optimization off. You own them, but you can't read the
  principals' schemas without granting yourself access (least privilege).
- **Workflow:**
  - pull requests: tests, then validation;
  - `main`: tests → deploy staging, land the MD5-verified C-MAPSS files
    without overwriting, run `cmapss_ingest` and `cmapss_verify` → deploy prod
    after approval;
  - docs-only changes skip it;
  - actions are pinned to commit SHAs, and only deploy jobs get
    `id-token: write`.
- **Run `36098067567` SUCCESS** (manual, recreate allowed once):
  - 122 tests passed.
  - Staging deployed as its principal in 18.5 min: 16 jobs, 4 pipelines,
    4 schemas, the landing volume. Ingest `459623161285845` took 8.7 min and
    verify `104076494798937` 8.0 min; verify asserts the benchmark's exact
    counts and feature values.
  - Prod deployed as its principal after your approval.
  - Nothing is running afterwards.
- **Push rerun `36100233863`** (the first code push after the green run):
  - 122 tests passed.
  - Staging redeployed without destructive changes. The landing upload
    found every file identical.
  - Ingest `415902458418397` (7.1 min) appended nothing to Bronze and planned
    every Silver and Gold table as `NO_OP`. Verify `591333246681954` (9.0
    min) passed with the same counts.
  - Prod redeployed after your approval (06:18–06:19 UTC), a no-op because
    the configuration was unchanged.
  - ≈ CAD 0.25 of serverless.
- **Manual rerun `36102190326`** (*Run workflow* on the same commit): the
  same result. Ingest `87485400787907` (7.4 min) appended nothing, with every
  Silver and Gold table `NO_OP`; verify `215185864273674` (7.3 min) passed
  with the same counts; prod redeployed after your approval (06:37 UTC).
- **What the first attempts exposed (all fixed and tested):**
  1. **Subject and audience.** GitHub's OIDC subject embeds immutable owner
     and repository IDs, and the CLI requests the token for the workspace
     endpoint, not the account ID.
  2. **Strict validation in a clean checkout.** Sync patterns for git-ignored
     directories match nothing there, and the principal's own folder
     permission had to be stated.
  3. **Name prefix.** A `name_prefix` preset also renamed the UC schemas, so
     tags are used instead.
  4. **Destructive-deploy guard.** Fixing the names meant recreating the empty
     schemas, which the CLI rightly refused without `--auto-approve`. A
     manual-only, staging-only checkbox allowed it once.
  5. **Accounts.** The browser and Git Credential Manager were signed in as
     other GitHub accounts, and the first owner account was deleted. The
     repository was recreated and the federation policies were re-pointed.
- **Cost:** serverless about 17 min for staging's ingest and verify, plus
  deploys (≈ CAD 0.3). GitHub Actions is free for public repositories.
- **Tests:** 122 (7 new, for CI/CD):
  - one principal and catalog per environment;
  - schemas before pipelines;
  - pinned actions;
  - OIDC permissions only on deploy jobs;
  - prod waits for staging;
  - the upload never overwrites;
  - every sync pattern matches a tracked file;
  - no name prefix;
  - only a manual run can auto-approve;
  - docs-only changes skip CI.

## Earlier milestone: Event Hubs streaming demo (bounded)

Events, the third ingestion style. C-MAPSS FD001 test trajectories were
replayed from this machine into Azure Event Hubs and read back through its
Kafka endpoint. Committed as `1db22df`. Details:
[INGESTION.md](INGESTION.md#streaming-ingestion-event-hubs-kafka-endpoint-bounded-demo);
evidence [eventhubs-demo.json](eventhubs-demo.json).

- **Your choices and approvals:** a local producer using only the standard
  library, via the REST batch API (no new dependency). You approved:
  - registering the `Microsoft.EventHub` provider;
  - the namespace, the secret scope, and the deploy and runs;
  - same-day deletion.
- **Prices checked first** (CAD, `westus2`): throughput unit 0.0416/hour and
  ingress 0.0388 per million events. The price list also has a "Standard
  Kafka Endpoint" meter at 0.1247/hour, although the pricing page lists Kafka
  as included, so the worst case, ~0.17/hour, was budgeted. Confirm with the
  meter-level cost once it posts.
- **Infrastructure** (`infra/eventhubs-demo.bicep`, what-if first):
  - namespace `evhns-sentinelops-7s5fwy`: Standard, 1 TU, TLS 1.2;
  - hub `cmapss-telemetry`: 2 partitions, 1-day retention;
  - SAS policies `cmapss-listen` (Listen only) and `cmapss-send` (Send only).
  - It existed from **02:23:04 to 03:06:31 UTC (43.5 min)**. Deletion was
    confirmed (`ResourceNotFound`).
- **Secrets:**
  - The listen key went from the Azure CLI straight into the
    Databricks-backed scope `sentinelops-eventhubs`, via the SDK.
  - The send key only signed a one-hour SAS token inside the producer.
  - Neither was printed, written or passed on a command line. The scope was
    deleted afterwards.
- **Producer:**
  - 13,096 events (100 engines) in 33 batches, all HTTP 201, in 10 seconds.
  - Each engine went to one partition, in cycle order: 6,253 and 6,843.
  - Event Hubs' metrics agree: 13,096 incoming and 13,096 outgoing messages
    (7.65 MB in).
- **Job `cmapss_stream_ingest`** (`637313705889552`): pipeline `cmapss_stream`
  (`aab883b8-f23a-4ebc-a28f-9bd79f5c6747`) → verify.
  - **Run 1 `568883254693144` SUCCESS.** Update `1bbc4074…`: Bronze
    appended **13,096**, every `event_id` once, and the offsets are
    **contiguous** (0–6252 and 0–6842). All 5 rules passed; 0 quarantined.
    **All 13,096 streamed observations are bit-identical** to the
    file-ingested `silver.cmapss_observations`, with 0 missing and 0 extra.
    0 cycles arrived out of order.
  - **Run 2 `865371030010789` SUCCESS: the rerun proof.** Update
    `25aa0112…`: Bronze appended **0**, Silver was `NO_OP`, and verify passed
    with the same counts.
- **Local rehearsal first:** the JSON round trip of all 314,304 values of the
  real file gave 0 mismatches, and the batch sizes stayed ≤ 262 KB.
- **Cost:** Event Hubs ≤ CAD 0.35 (at most two started hours at the worst-case
  rate; ingress ≈ 0). Serverless: about 33 minutes of task time, ~24 of them
  execution, ≈ CAD 0.3. Nothing is running afterwards.
- **Caveats:**
  - The producer doesn't retry, because a timed-out batch might have been
    accepted. The consumer counts duplicates instead.
  - `cmapss_stream_ingest` can only run while a namespace exists. A new one
    needs `put-secret` and a full refresh of `cmapss_stream`, whose checkpoint
    holds the old offsets.
- **Tests:** 115 pass (7 new):
  - event values round-trip exactly;
  - each engine stays in one partition, in order;
  - batch limits;
  - SAS signing;
  - no retry after a rejected batch;
  - the pipeline's rules and Kafka options against the event shape;
  - names agree across the Bicep, bundle and script.

  The pipeline stand-in loader is now a shared fixture.

## Earlier milestone: REST API ingestion (Open-Meteo weather)

A third ingestion style, next to files: a REST API fetched into immutable
landing, then Auto Loader into Bronze/Silver/Gold. Committed as `abbba3b`.
Details: [INGESTION.md](INGESTION.md#rest-api-ingestion-open-meteo-weather);
evidence [weather-backfill.json](weather-backfill.json).

- **Source (your choice):** the Open-Meteo Historical Weather API. It needs no
  key; data is CC BY 4.0 ("Weather data by Open-Meteo.com", ERA5 from
  Copernicus), and the free API is for non-commercial use.
  - Fetched: daily ERA5 max, mean and apparent max temperature for 2015–2025.
    There is one city per state, for the 20 states with the most OSHA
    heat-illness reports (92% of 2,624).
  - 220 requests, ~5,740 weighted calls. The free limit is 5,000 per hour, so
    the backfill took two runs.
- **Approvals:** the downloads (one local test request, then the cloud
  fetches), the deploy and three runs.
- **Job `weather_ingest`** (`584629930216724`): fetch → pipeline
  `weather_open_meteo` (`8eca1296-fbee-409a-a093-f3d8b82ca7f6`) → verify.
- **Run 1 `795987049449431` SUCCESS (26.7 min).**
  - Fetch: 153 files, 1.60 MB, 3,991.9 weighted calls. It stopped at its
    4,000 budget as designed, with 67 left.
  - Pipeline update `974f3091…`: Bronze appended 153 rows; Silver has 55,887
    days, all 8 rules passed, 0 quarantined; Gold has 1,836 state-months.
  - Verify: 153/153 SHA-256 matches, the **overwrite probe was refused**,
    Silver is bit-identical to the pandas recomputation, and Gold is within
    2.1e-14.
- **Run 2 `722495160937045` SUCCESS (23.5 min), started once run 1's calls
  had left the hourly window.**
  - Fetch: skipped the 153 landed files and fetched the last 67 (694 KB,
    1,748.1 weighted calls; 5,740 in total). The guardrail read run 1's log:
    0 calls in the last hour, 3,991.9 in the last day.
  - Pipeline update `a2097c00…`: Bronze appended exactly **67**, so ingested
    files weren't re-read. Silver has **80,360** days (20 locations × 4,018)
    and Gold **2,640** state-months, all complete, 0 quarantined. Gold
    refreshed incrementally (`GENERIC_AGGREGATE`).
  - Verify: 220/220 SHA-256 matches, the overwrite probe was refused, and
    Silver is bit-identical.
- **Run 3 `899114500995009` SUCCESS: the rerun proof.**
  - Fetch: all 220 files present, so **0 API calls** (0.5 min).
  - Pipeline update `edd01293…`: Bronze appended **0**, and every
    materialized view was `NO_OP`.
  - Verify: passed, with the same counts.
- **Cost:** about 46 minutes of serverless execution (≈ 70 minutes including
  compute startup, three tasks per run), ≈ CAD 0.7–1.1, all on September 24
  (UTC). The API is free. Nothing is running afterwards.
- **Found in the local rehearsal:** responses aren't byte-reproducible.
  `generationtime_ms` varies, and `utc_offset_seconds` is the zone's offset at
  request time. Skipping (never re-fetching) and the logged SHA-256 are what
  make landing immutable.
- **Caveats:**
  - one city stands in for a state;
  - ERA5 is a ~28 km grid cell, cooler at the daily maximum than a city
    station;
  - apparent temperature isn't the NWS heat index;
  - no join to OSHA yet; the marts share `state` and `month`.
- **Tests:** 108 pass (16 new): the frozen spec, file names, call weights,
  budget and resume, 429/5xx/4xx handling, pacing, never overwriting (local
  and Files API), the pandas reference rules, and the pipeline/reference
  contract (the pipeline file runs against Spark stand-ins).

## Earlier milestone: employer-name masking v2 (OSHA)

Eval v2 found an answer naming the report's own employer: masking v1 had
missed shortened employer names. Masking v2 closes that gap. Details:
[SAFETY_RAG.md](SAFETY_RAG.md#masking-v2-shortened-employer-names); evidence
[osha-masking-v2.json](osha-masking-v2.json).

- **Rules.** Also mask leading word sequences of the employer name and of
  "dba" trading names, plus distinctive single words (not business words,
  not ordinary vocabulary, not states). Every shortened variant is masked
  only when capitalized as a proper noun.
- **Development** on the full local corpus (no names printed or uploaded):
  - capitalized leading-name leaks fell from 37 to **0**, and distinctive-word
    leaks from 112 to 7;
  - a case-insensitive first attempt over-masked 289–1,104 ordinary words, so
    shortened variants are proper-noun only;
  - state names inside employer names are left alone.
- **Landing `osha_sir/v2`** (your approval): 105,996 rows, 84,168,207 bytes.
  Only `narrative` differs from v1, in 140 reports; v1 is untouched.
- **Ingest run `929857370324250`.** A new append flow `osha_sir_v2` appended
  105,996 rows, and the v1 flow appended 0, so its checkpoint was untouched.
  Silver and Gold hold 105,993 reports, with v2 copies winning.
- **Embed run `771297237993579`:** exactly **140** changed documents were
  re-embedded (matching the local count), with 0 stale.
- **Identity check, run `731577238433197`.** 16 new held-out identity
  requests: 12 about incidents that leaked under v1, 3 generic, and 1 the
  eval v2 leak paraphrased.
  - **No employer names in any of 76 answers**, per
    `scripts/scan_answer_names.py`, run locally against the raw archive. The
    scanner is validated: it flags the original leak.
  - 13 of 16 were declined. The other 3 were answered, but only as
    "[EMPLOYER] Company, LLC" or "the Illinois plant": masking, not the
    model's decline, is the control that holds.
  - The eval v2 rerun (regression) made 59/60 correct decisions.
- **Residual risk:** initials such as "G&H [EMPLOYER]", and other companies'
  names (contractors), are not masked. The model still answers some
  incident-style identity questions (without a name).
- **Cost:** about CAD 0.8 (ingest 11 min, embed 6 min, eval 13 min, 228 judge
  calls). Nothing is running afterwards.
- **Tests:** 92 pass (2 new: masking v2 variants, the ordinary-word
  vocabulary; the identity-set hygiene checks were extended).

## Earlier milestone: real-time serving demo and answer evaluation v2

Both approved on September 24 (your credits last until October 10).

**Real-time serving (bounded demo).** Details:
[OPERATIONS.md](OPERATIONS.md#real-time-serving-bounded-demo); evidence
[serving-demo.json](serving-demo.json).

- **Endpoint** `sentinelops-rul-demo` served champion v3: Small CPU,
  scale-to-zero, AI Gateway inference table. It was created at 17:16 UTC,
  READY about 10 minutes later, and **deleted at 18:28**. It isn't a bundle
  resource; its definition is `infra/serving-endpoint.json`.
- **Parity job** `cmapss_serving_check` (`782318629064026`), run
  `1018787913287976` **SUCCESS**:
  - all **13,096** fleet rows of the Spark-computed Gold features, sent as
    JSON, came back **bit-identical** to `gold.cmapss_predictions` for v3;
  - latency: batches of 400 rows p50 250 ms; single rows p50 81 ms, p95 85
    ms.
- **Mistake, caught:** the first run (`959774648252926`) selected `cycle`
  twice (it's a key and a feature). That shifted every request row by one
  column, and the endpoint scored the misaligned rows without complaint. The
  job then failed. It's fixed; `request_body` now rejects duplicate columns,
  and a test covers it.
- **Inference table** `sentinelops_dev.sentinelops_dev.turbofan_rul_demo_payload`:
  every request was logged (86 rows, all status 200). Delivery took 5–40
  minutes (standard, not fast-path), so the job's 3-minute check was too
  short. The table is kept.

**Answer evaluation v2.** Details: [SAFETY_RAG.md](SAFETY_RAG.md#larger-answer-evaluation-eval-v2);
evidence [osha-answer-eval-v2.json](osha-answer-eval-v2.json).

- **Scope:** 60 new held-out questions (24 answerable, 24 in-domain
  unanswerable, 12 off-topic or adversarial). Prompt and threshold are
  unchanged. Run `425541794390409` **SUCCESS** in 14.2 min.
- **58/60 correct decisions.** 21 of 24 unanswerable questions scored above
  the 0.6511 threshold, and the model declined 20 of them. All 4 in-domain
  injection prompts passed the threshold, and the model declined each. On
  answers, correctness was 22/23 and groundedness 23/23.
- **Privacy failure:** "Which meatpacking plant had the ammonia leak ...?"
  was answered with the report's own employer name. Masking had missed a
  shortened form of the name; about 74–101 narratives are affected. The name
  is redacted in the evidence. **Deployment stays blocked** until masking is
  strengthened (the next task).
- **Other misses:**
  - one false decline, where retrieval found explosions for an
    injection-injury question;
  - one correctness miss, where the robbery reports retrieved were assaults,
    not shootings.
- **EVAL_V1 rerun (regression):** 28/28 decisions, correctness 12/12.
- **Cost:**
  - generation 0.262 DBU (≈ CAD 0.03);
  - about 264 judge calls (≈ CAD 0.3–0.5);
  - 14.2 min of serverless (≈ CAD 0.22).
- **Tests:** 90 pass (8 new):
  - serving: request exactness, duplicate columns, retries, parity, the
    endpoint definition;
  - question-set hygiene across v1 and v2, decline routes, results per set.

## Earlier milestone: AI/BI dashboards, Genie and a budget alert

Two AI/BI dashboards and a Genie space are now bundle resources. They are backed
by two new Gold marts and checked on serverless compute before any warehouse
use. Details: [ANALYTICS.md](ANALYTICS.md).

- **Budget alert (Task 0).** Budget `sentinelops-dev-monthly`, CAD 150/month
  (your choice), scoped to `rg-sentinelops-dev` and
  `rg-sentinelops-dev-managed`. It emails cheng.huang.ca@outlook.com at 50%,
  80% and 100% of actual spend and at 100% of forecast. It is free, and the
  definition is in `infra/budget.json`.
- **Cost finding.** CAD 2.42 of serverless SQL posted for September 24 came
  from browsing sample data in Catalog Explorer (05:44 UTC), which starts the
  starter warehouse. The warehouse was Small (12 DBU/h) with a 10-minute
  auto-stop. With your approval it is now **2X-Small (4 DBU/h), 5-minute
  auto-stop**.
- **`analytics_refresh`** (`847239470140874`), run `429694746857912`
  **SUCCESS** in 7.5 min, no SQL warehouse:
  - `gold.osha_injury_facts`: 105,993 rows with harmonized categories and no
    narratives;
  - `gold.cmapss_fleet_status`: 100 engines scored by v3: 15 critical, 14
    warning, 71 healthy;
  - comments on the monitoring tables for Genie;
  - all 7 dashboard datasets and all 5 Genie example queries executed, with
    widget columns checked.
  - The facts matched a local rehearsal exactly, including the extraction
    evaluation's harmonization counts (69 / 3,932 / 371).
- **Dashboards** Fleet health (17 widgets) and Safety incidents (14 widgets)
  are published with viewer credentials. The render check in the browser
  (after your sign-in) found three problems, all fixed:
  - spec-v1 tables that showed "no fields selected";
  - more than 10 color series, which reuse colors;
  - scrolling title boxes.
- **Genie** (`01f1b8347de912dc8d94fcb07a9144ec`): on 8 held-out questions,
  6 answers were fully correct and the employer-identification probe was
  correctly declined. The eighth answer ran the right SQL, but its summary
  miscounted breaches as 21; the true count is 20. After I added an example
  that counts in SQL, a dev paraphrase returned 20. Evidence:
  [genie-evaluation.json](genie-evaluation.json).
- **Cost:**
  - about 23 billed warehouse minutes (≈ 1.55 DBU ≈ CAD 1.5), in two sessions;
  - about 7.5 minutes of serverless job time (≈ CAD 0.12);
  - Genie's LLM use is free through January 31, 2027.
- **Day total, September 24 (UTC).** By 16:55 UTC, CAD 8.05 had posted,
  covering usage to about 11:00 UTC:
  - serverless jobs 2.60;
  - serverless SQL 2.42 (the Catalog Explorer browse);
  - model calls 2.23 (the morning's evaluations);
  - NAT and IP 0.76.
  - Adding the remaining fixed cost (about 0.9) and this milestone (about 1.6),
    the **projected total is about CAD 10.6**. That is slightly over the limit
    if $10/day means CAD; it is within USD 10. The monthly budget alert won't
    fire on one day, so no further billable work was started on September 24.
- **Known quirk:** every deploy "updates" `cmapss_ingest`, `osha_ingest` and
  `cmapss_retrain` with identical settings, because the Jobs API doesn't echo
  `disable_auto_optimization` on pipeline tasks. It's harmless.
- Tests: 82 pass (15 new): facts and harmonized names, risk bands, fleet status,
  DDL quoting, dashboard consistency and tables, color-series cardinality, DOL
  attribution, and the Genie serialized-space format rules.

## Earlier milestone: orchestrated retraining and alerts (C-MAPSS)

One manual job, `cmapss_retrain` (`1037945637149770`), now runs the whole loop:
ingest → verify → train → promote → score → monitor → alerts. Details:
[OPERATIONS.md](OPERATIONS.md#orchestrated-retraining-cmapss_retrain).

- **Retrain only on change.** `train --only-if-changed` compares the Gold
  training digests with the `@champion` run's digests. Retraining unchanged
  data would register an identical version, which the tie rule promotes. The
  `force_retrain` job parameter overrides this. `promote --only-pending`
  evaluates only an undecided `@challenger`.
- **A linear chain, not an If/else task.** Task values can only be set from
  notebooks, and a notebook's different environment could change the digests.
- **Unchanged-data run `599725542930474`: SUCCESS in 23.3 min.**
  - The ingest update appended 0 rows, and every flow was `NO_OP`.
  - `verify` passed.
  - `train` recomputed v3's digests exactly and skipped.
  - `promote` found no challenger, and `score` had 0 pending rows.
  - `monitor` reproduced endpoint RMSE 18.3415 bit-for-bit.
  - `alerts` ran 47 checks with 0 breaches.
  - After `verify`, the tasks reused warm compute (about 0.5 min each).
- **Alerts:**
  - Thresholds: RMSE +20% vs the version's first snapshot, age-matched PSI
    0.2, and snapshots under 24 h old.
  - Every check is appended to `gold.cmapss_alerts`, and a breach fails the
    run.
  - Breach test (alerts only, `max_psi=0.05`): 20 breaches, the task failed,
    one attempt.
  - No email notifications (your choice), and no SQL warehouse.
- **Found and fixed:** serverless auto-optimization had retried a failed task
  despite `max_retries: 0`, so the zero-retry guardrail was not fully in
  effect. All 19 tasks in all 12 jobs now set `disable_auto_optimization:
  true`, and a test enforces it.
- **Not run:** the retrain branch (your choice, to avoid an identical v4).
  `verify` asserts the benchmark's exact counts, so genuinely new data would
  need growth rules there.
- Cost: about 40 min of serverless in total (≈ CAD 0.65); no model calls.
  Champion is still v3, and nothing is running afterwards.
- Tests: 67 pass (3 new: retrain decision, alert checks, chain wiring;
  guardrail test extended).

## Earlier milestone: Safety GenAI structured extraction (OSHA)

LLMs now code OSHA narratives into event, nature of injury, body part and
source. The results are scored against OSHA's own coding, next to a majority
baseline and a supervised baseline. Details:
[SAFETY_RAG.md](SAFETY_RAG.md#structured-extraction-osha_extraction_eval).

- **The answer key was fixed first.** OSHA changed its codes in 2024 (for
  example, "Fractures" 111 → 124), so raw code prefixes aren't consistent
  labels. Truth uses the OIICS division for event, body part and source,
  harmonized to the current scheme: hips now count as lower extremities, which
  changed 3,932 body-part labels. Nature uses ordered title rules for 12 injury
  types. Nonclassifiable and nonspecific truth isn't scored.
- **Job `osha_extraction_eval`** (`804198192778755`), run `570236144351626`
  **SUCCESS** (13.5 min, 3.3 of them setup), MLflow run
  `840d418d57f64dc894892467a09641f0`.
  - `ai_query` with a strict JSON schema, medium reasoning, 1,051 held-out
    reports per model, 0 failed calls (221 s for 120B, 63 s for 20B).
  - Raw outputs are stored once in the new `gold.osha_extractions`, with
    primary key `(report_id, model, prompt_version)`; code validates them.
- **Test accuracy** (majority / supervised TF-IDF / GPT-OSS-120B / 20B):

  | Field | Majority | Supervised | 120B | 20B |
  |---|---|---|---|---|
  | Event | 0.492 | 0.943 | 0.935 | 0.887 |
  | Nature | 0.388 | 0.943 | 0.943 | 0.921 |
  | Body part | 0.439 | 0.939 | 0.948 | 0.939 |
  | Source | 0.278 | 0.825 | 0.760 | 0.695 |

  - 120B minus supervised: event −0.009 [−0.025, 0.008], nature 0.000, body
    part +0.009 (confidence intervals include 0), source −0.065 [−0.093,
    −0.038].
  - 120B's main source errors: machinery coded as tools or as parts.
  - 20B is significantly worse on event, nature and source, with 12 empty
    responses.
- **Prompt development was confined to 114 dev reports** (v1 → v3, plus a
  low vs medium effort comparison). The test split wasn't run before the job.
- Cost: ≈ CAD 1.1 of model calls (estimated; `ai_query` returns no token
  counts) plus ≈ CAD 0.2 of serverless and ≈ CAD 0.35 of local dev calls.
  Nothing is running afterwards.
- Tests: 64 pass (7 new: split, harmonization, nature rules, schema/prompt
  labels, parsing, scoring, bootstrap).

## Earlier milestone: Safety GenAI grounded answers (OSHA)

The assistant now answers from retrieved OSHA reports. It cites report IDs,
code checks the citations, and it declines when the reports can't answer.
Every question is traced in MLflow. Details:
[SAFETY_RAG.md](SAFETY_RAG.md#grounded-answers-osha_answer_eval).

- **Job `osha_answer_eval`** (`1029765841933443`), run `745084593826476`
  **SUCCESS** (8.1 min, 3.1 of them setup). MLflow run
  `17406be7875d4a2387785faf3ea9f083` in `sentinelops-safety-rag` holds 28
  traces with the expected spans: 12 answers ran retrieve → generate → check,
  6 model declines ran retrieve → generate, and 10 threshold declines ran
  retrieve only.
- **Held-out questions** (none run before the job): 12 answerable, 11
  in-domain unanswerable, 5 off-topic. **28/28 correct decisions.** Every draft
  answer's citations were valid and every sentence was cited. The Llama 3.3
  judge passed correctness 11/12 (the miss is a judge false negative, kept as
  a failure), groundedness 12/12 and relevance 12/12.
- **Decline rule:**
  - The threshold is 0.6511, the midpoint between the lowest on-topic score
    (0.7095) and the highest off-topic score (0.5927) on 40 questions that
    aren't evaluated.
  - The model declines numbers and trends, advice, penalties, standards text
    and identities.
  - Caveat: the threshold caught 5 of the 11 in-domain unanswerable questions,
    one only 0.0016 below it. The model's decline was tested on 6 held-out and
    8 dev questions.
- **Prompt development was confined to 16 dev questions**, run locally on real
  reports:
  - v1 used `【id】` brackets, which the code check rejected.
  - v1 and v2 answered "how many … in 2022" by counting the retrieved sample.
  - v3 fixed both.
  - The dev run also exposed judge literalism, so the answer key uses single
    general facts and generic declines.
- Cost: generation 0.075 DBU (≈ CAD 0.01); about 84 judge calls
  (≈ CAD 0.15, estimated); about 8 min of serverless; local development
  < CAD 0.2. Nothing is running afterwards.
- Tests: 57 pass (21 new: citations, decline paths, the chat client's
  visible retries, trace shape, question-set hygiene, calibration, summaries,
  and bundle job guardrails).

## Earlier milestone: Safety GenAI retrieval evaluation (OSHA)

Exact dense search is validated against OSHA-code relevance and beats a
keyword baseline. 256 dimensions are chosen for the assistant. Details:
[SAFETY_RAG.md](SAFETY_RAG.md#retrieval-evaluation-osha_retrieval_eval).

- **Job `osha_retrieval_eval`** (`383639217735446`), eval v2, run
  `603274434690806` **SUCCESS**, MLflow run `5ee4a80e430c431e932504222d041bbe`
  (experiment `sentinelops-safety-rag`). The questions are 28 paraphrases whose
  relevance comes from OSHA code rules, plus 4 off-topic ones, searched
  exactly over all 105,993 documents.
- **Precision@10:** dense-1,024 **0.882**, dense-256 **0.882**, TF-IDF 0.786.
  Dense minus TF-IDF is +0.096, 95% CI [0.011, 0.196]. 256 minus 1,024 is
  0.000, CI [−0.036, 0.043]. The 256-dimension index uses 104 MB instead of
  414 MB, at ~2.7 ms per query.
- **Off-topic separation** holds at both sizes (lowest on-topic top-1 0.665
  and 0.711; highest off-topic 0.431 and 0.512). The negatives are easy, so
  abstention still needs calibration.
- **Honest caveats:** v1 → v2 fixed one answer-key vocabulary bug (OSHA's
  reversed title `Stationary saws  table`); v1 run `133374292493436` is kept.
  Two weak questions reflect code-label limits (`truck_dock_pinned`) or a
  genuine retrieval weakness (`toe_amputation`). Headers repeat the code
  titles, so absolute scores are optimistic.
- The code path was rehearsed locally before cloud runs: TF-IDF on the real
  corpus (cloud results matched it exactly), and the reporting path with
  random vectors. 36 local tests pass (7 new).
- Cost: two ~6-minute serverless runs; question embeddings were negligible.

## Earlier milestone: Safety GenAI embeddings (OSHA)

All 105,993 Gold OSHA documents now have embeddings for exact retrieval. No
answers are generated yet. Details: [SAFETY_RAG.md](SAFETY_RAG.md#embedding-job-osha_embed).

- **Job `osha_embed`** (`379147303783128`) uses `ai_query` against the
  pay-per-token `databricks-qwen3-embedding-0-6b` endpoint. Results are staged
  once, and only validated vectors are merged. Run `942266880002897`
  **SUCCESS**: 81,417 embedded, 0 failed, 3.9 min of execution. `gold.osha_embeddings`
  stores **105,993** vectors, equal to the Gold document count, with 0 stale
  and 0 wrong-size. Rerun `206063629553445` **SUCCESS**: 0 pending, no model
  calls. Evidence: [osha-embedding-backfill.json](osha-embedding-backfill.json).
- **Measured:** direct REST calls to the endpoint are throttled by input count
  (16 per request accepted, 32 rejected; ~24–33 inputs/s), far below the
  published hourly limit. `ai_query` ran at ~470 docs/s without errors.
  Stored 1,024-dimension vectors can be truncated to 256 exactly.
- **Mistake, and its cost:** the first attempt used paced REST calls (run
  `501653035205112`). I cancelled it after 25 minutes because UC table
  metadata showed no commits, but it was in fact progressing (24,576 rows
  stored, kept). That cost ~25 minutes of compute. Lesson: count rows or log
  progress instead. Two one-minute diagnostic runs found the real behavior and
  led to `ai_query`.
- Cost (September 24): ~40 min of serverless and ~9–10M embedding tokens
  (~CAD 0.27), on top of the fixed ~CAD 1.7/day. Nothing is running afterwards.
- Tests: 29 pass (five new, for batching, pacing, throttling, isolation and
  validation).

## Earlier milestone: Safety GenAI data foundation (OSHA)

Source, license, privacy and cost checks are done. Minimized OSHA reports are
in Bronze/Silver/Gold. No embeddings or model calls yet. Design:
[SAFETY_RAG.md](SAFETY_RAG.md).

- **Source:** with the user's approval, downloaded OSHA's
  `January2015toNovember2025.zip` (16,224,511 bytes, matching the server's
  `Content-Length`). SHA-256 `a3f7f434…46bb0` is pinned. It holds one UTF-8 CSV
  with 105,996 reports (2015-01 to 2025-11). The data is a federal
  public-domain work; attribution to DOL is recorded in the manifest. `ID` is
  not unique (5 IDs cover two incidents each); `UPA` is unique and is used as
  `report_id`.
- **Privacy:** employer, address, city, ZIP, coordinates and inspection numbers
  are dropped locally before upload, and event dates are coarsened to month. In
  narratives, the report's own employer and address, numbered streets and
  state+ZIP are masked: 282 narratives, 300 replacements. Scans found no
  emails, phones, SSNs or personal names. The residual risks (dates, cities,
  other companies) are documented.
- **Cost decision (user-approved):** exact retrieval over Delta-stored
  embeddings, no Vector Search endpoint. A Standard endpoint costs ~CAD 9.3/day
  and keeps billing for 24 h after the last index is deleted, which would
  exceed the $10/day budget. Qwen3 embeddings for the full corpus are estimated
  at ~CAD 0.15 in tokens.
- **Pipeline `osha_safety`** (`7d53a0fb-d724-4628-a854-23dc1d0e283a`), job
  `osha_ingest` (`1070808576153729`):
  - Run `1007139224127625` **SUCCESS**. Bronze 105,996 rows, schema-conformant
    (nothing rescued). 6 rows quarantined: 3 near-empty narratives, 2 blank
    industry codes, and 1 sector range `48-49`.
  - The industry-code rule was then relaxed, because industry is optional
    metadata. Run `197582638913248` **SUCCESS**: Bronze appended 0 rows (the
    checkpoint held), quarantine is **3** (only narratives under 20 characters),
    and Silver `osha_incidents` and Gold `osha_documents` (primary key
    `report_id`) each have **105,993** rows.
  - Evidence: [osha-first-update.json](osha-first-update.json),
    [osha-rule-update.json](osha-rule-update.json).
- **Tests:** 24 local tests pass (four new OSHA tests: masking, minimization,
  checksum, immutability). Real data exposed two issues before upload, both
  fixed: blank severity counts, and ZIP codes and numbered streets in narratives.

## Previous milestone: batch operational ML (promote, score, monitor)

A champion now scores the simulated fleet into an idempotent inference log.
Delayed labels are merged, and performance and drift snapshots are recorded.
See [OPERATIONS.md](OPERATIONS.md) for the design. No Azure resources were created.

- **Promotion gate:** job `cmapss_promote` (`714826690927619`), run
  `268300947742291` **SUCCESS**. v3 passed every gate: Gold lineage, feature
  signature, and label digest unchanged. Validation RMSE **14.9309** vs a
  constant baseline of **41.7208** on the same 20 held-out engines (ratio 0.358;
  the limit is 0.5). No official test labels were read. **`@champion` → v3**;
  `@challenger` removed; v3 tagged `promotion_decision=promoted`. Confirmed
  independently via the UC API. Evidence: [promotion-decision.json](promotion-decision.json).
- **Fleet scoring + monitoring:** job `cmapss_score` (`362250970463849`), run
  `232258023004472` **SUCCESS** (9.3 min, both tasks). Scored **13,096** fleet
  (test-split) observations into `gold.cmapss_predictions`, then merged 13,096
  delayed labels. Endpoint-segment RMSE is **18.341479192292436**, bit-identical
  to v3's training-run test RMSE. That confirms scoring uses exactly the
  evaluated model and features. Other segments: true RUL ≤ 125, RMSE 19.17
  (bias +5.1); true RUL > 125, bias −60, expected from the 125-cycle label cap.
  Drift: raw PSI > 0.2 for 26 of 43 features, **0 of 43** after age matching
  (max 0.102). Raw PSI mainly reflects younger fleet engines, not sensor drift.
  Evidence: [fleet-scoring-first-run.json](fleet-scoring-first-run.json).
- **Idempotent rerun:** run `94693603475272` **SUCCESS** (7.3 min). 0 pending
  rows, no scoring merge, 0 labels updated, the log still has 13,096 rows for
  v3, and performance and drift values are identical to the first run. Each run
  appends one snapshot to the performance and drift tables by design.
  Evidence: [fleet-scoring-rerun.json](fleet-scoring-rerun.json).
- All values match a local rehearsal on real FD001 data with Spark-emulated
  features, run before any cloud compute.
- Twenty local tests pass (eight new: gate, baseline, label digest, performance
  segments, PSI, age matching). The age-matching test caught an open-ended bin
  bug before deployment. Strict validation passed; deploy created only
  `cmapss_promote` and `cmapss_score`.
- Not done: no real-time serving endpoint or inference tables; no schedules
  or alerts; no Lakehouse Monitoring monitor (metrics are computed by the job).

## Earlier milestone: incremental checks passed; training consumes Gold

All outstanding medallion checks have run in the cloud, and training now reads
the Gold tables. No Azure resources were created; one bundle job was added.

- **Quality probe (incremental ingestion):** uploaded the nine-line
  `train_FD001_quality.txt`. Ingestion run `452160343690169` **SUCCESS**
  (pipeline update `def8a926-30c5-46a4-88f7-d4e3acb64c2c`). Bronze appended
  exactly **9** lines; consumed files were not re-read. `valid_trajectory` passed
  33,731 and dropped 5; quarantine **5**, conflicts **1** (unit 999, two
  payloads); equal-payload repeats collapsed; Silver observations and all Gold
  counts unchanged. Gold features and labels refreshed incrementally
  (WINDOW_FUNCTION), quarantine APPEND_ONLY; Silver observations/conflicts fully
  recomputed because expectations prevent incrementalization.
  Evidence: [medallion-probe-update.json](medallion-probe-update.json).
- **Probe verification:** run `205718877304121` **SUCCESS**: Bronze 33,736
  lines; every canonical count and all 33,727 feature values unchanged (rtol
  1e-10); quarantine rows all from the probe file; composite key confirmed.
  Evidence: [medallion-probe-verification.json](medallion-probe-verification.json).
- **No-input rerun:** ingestion run `1108975989071922` **SUCCESS** (update
  `fa19089a-4199-412b-b539-be38fd20a16e`). Both Bronze streams appended 0 rows,
  and the planner chose **NO_OP for every materialized view**, so nothing was
  rewritten. Verified from the event log rather than a third verification run,
  to save compute; the training run below then re-read all Gold tables.
  Evidence: [medallion-rerun-update.json](medallion-rerun-update.json).
- **Training from Gold:** new job `cmapss_train` (`477632929595835`), run
  `174998841420766` **SUCCESS** (6.5 min). MLflow run
  `02b4dd86f955493ab75c6a53ef685e3f` in experiment `2331823695746663` logged
  the three Gold tables as dataset inputs with content digests, upstream quality
  counts (5 quarantined, 1 conflict), metrics and predictions. Registered
  **version 3** (READY, tagged `training_source=gold_medallion`); `challenger`
  now points to v3. Version 2 remains READY without an alias.
  Evidence: [medallion-training.json](medallion-training.json).
- **Gold v3 vs bootstrap v2:** 20,631 rows, 100 engines, 7 leaves, identical
  labels (bitwise-equal digest) and constant baseline RMSE 43.0670. Test RMSE
  **18.3415** vs 18.1586, MAE **13.1701** vs 13.2354, NASA **650.81** vs 629.03,
  validation RMSE 14.9309 vs 14.8517. Root cause: Spark `avg` over the 10-row
  window sums sequentially while pandas rolling mean uses a different algorithm;
  the means differ by at most 3.6e-12 in 36% of cells. That shifts gradient
  boosting's bin thresholds. Locally emulating Spark's
  sequential-sum mean reproduces the cloud metrics **bit-for-bit**. With exact
  pandas features, the Gold code path reproduces v2 bit-for-bit (local check).
  So the ±0.2 RMSE gap is numerical sensitivity, not a data or logic error.
  Choosing between v2 and v3 by test RMSE would be tuning on the test set.
- Twelve local tests pass (five new Gold-contract tests). Strict bundle
  validation passed; deploy created only `cmapss_train`, other resources unchanged.
- STANDARD mode spent ~7 minutes waiting for resources before the probe update;
  ingestion runs took 11–12 minutes of the 15-minute limit. Margin is thin.
- Final inventory: pipeline **IDLE**, no active runs, no classic clusters,
  starter warehouse **STOPPED**, no custom serving endpoints.

## Earlier milestone: medallion ingestion and feature parity verified

- Added `pipelines/medallion.py`: raw TEXT/JSON Auto Loader, quality expectations,
  malformed-row quarantine, conflicting-key exclusion, numeric deduplication,
  causal features, training labels, and official test endpoint joins.
- Reused the existing UC volume and ADLS managed storage. Added catalog schemas
  `bronze`, `silver`, `gold`; no new Azure infrastructure or external grants.
- Pipeline ID: `77ecd502-9283-4528-83e3-7ab8666ada1e`.
- Ingestion job ID: `264959928637120`; first run `103102387857859`.
- That combined run ended **TIMEDOUT**: ingestion succeeded, but verification
  was cancelled during its separate standard-mode startup, before executing any
  assertions. Do not treat the parent run as a full verification success.
- Verification is now a separate manual job `366011234786265` (`cmapss_verify`),
  with a 600-second timeout. Run `448846224282372` **SUCCESS**.
  [Verification run](https://adb-7405619144539463.3.azuredatabricks.net/jobs/366011234786265/runs/448846224282372).
- Pipeline update `2daec6e4-74bd-4a8b-b6f1-a37e40a9f5f7` **COMPLETED**.
  Event metrics confirm 33,727 canonical observations/features, 20,631 training
  labels, 100 test endpoints, zero quarantine/conflicts, and zero failed
  expectations on the original data. UC API confirms the five-column Gold
  primary key. Independent assertions passed for every feature value across
  train/test, all table counts, both label contracts, and key uniqueness/metadata.
- Strict bundle validation and deployment passed. Existing baseline resource was
  unchanged. All **seven** local tests passed after the ingestion additions.
- Deployed pipeline is triggered, serverless, development **false**. Bundle dev
  presets otherwise override the resource setting, so the preset is explicitly
  disabled. Job performance is verified STANDARD, max concurrency one, no retries,
  900-second total timeout, no schedule.
- First-update event evidence: `docs/medallion-first-update.json`.
- Independent assertion evidence: `docs/medallion-verification.json`.
- Incremental duplicate/quarantine/conflict probe is prepared by
  `scripts/prepare_quality_probe.py`. (Superseded: the probe and a no-input
  rerun have since passed; see the current milestone above.)
- Databricks billing snapshot query was denied: current user lacks USE SCHEMA on
  `system.billing`. No privileges were changed. With Azure posting delayed costs,
  further discretionary compute was deferred to preserve the $10/day limit.
  Recheck posted costs and obtain a current usage view before more cloud tests.
- Initial cost review: Azure posted **CAD 0.434912152** for September 23, 2026,
  across both SentinelOps resource groups. This is delayed posted usage, not a
  final daily total. No classic clusters; starter SQL warehouse STOPPED.
- See [INGESTION.md](INGESTION.md) for the data contract and runbook.
- Final inventory: pipeline **IDLE**, verification run **TERMINATED/SUCCESS**,
  no classic clusters, starter SQL warehouse **STOPPED** with zero clusters.
  Final Azure posted cost query remained CAD 0.434912152.

## Bootstrap verification retained

The first cloud path has succeeded: verified FD001 download → causal features
→ model selection/training → Delta tables → MLflow tracking → Unity Catalog
model registration. This is the initial predictive-maintenance milestone, not
completion of the full SentinelOps platform.

- [Successful cloud run](https://adb-7405619144539463.3.azuredatabricks.net/jobs/446848359557450/runs/99784281689509)
  completed in approximately 8 minutes 9 seconds including startup.
- Registered model: `sentinelops_dev.sentinelops_dev.turbofan_rul`, version **2**,
  status **READY**, alias **challenger** (independently verified through the API).
- Final cloud metrics exactly match local: RMSE **18.1586**, MAE **13.2354**,
  NASA score **629.0335**. Constant baseline RMSE: **43.0670**.
- No champion promotion or serving endpoint has been created.

## Verified locally

- NASA FD001 archive downloaded and MD5 verified against the Zenodo record.
- Training: 20,631 rows / 100 engines. Test: 13,096 rows / 100 endpoints.
- Engine-disjoint 80/20 selection; random seed 42; 7-leaf gradient boosting selected.
- Official endpoint RMSE: **18.1586 cycles**; constant baseline: **43.0670**.
- MAE: **13.2354 cycles**; NASA asymmetric score: **629.0335**.
- Five tests passed (causal features, engine boundaries, labels/metric,
  whitespace/schema/duplicate checks, MLflow/skops save-and-reload equivalence).
- Azure Bicep compilation and Azure what-if validation succeeded.

## Azure

- Account: cheng.huang.ca@outlook.com
- Subscription: Azure subscription 1
- Subscription ID: b1026367-46bf-43e0-93b5-bbfcc45a2291
- Resource group: rg-sentinelops-dev
- Region: westus2
- User-approved operating budget: up to $10/day for the full demo.
- Foundation deployment **Succeeded**: workspace, ADLS, five containers,
  access connector and storage-scoped RBAC.
- Workspace: https://adb-7405619144539463.3.azuredatabricks.net
- Storage: stsent7s5fwynthfd64
- UC credential: sentinelops_adls; external location: sentinelops_metastore.
- Catalog: sentinelops_dev, managed storage on the project's ADLS account.
- Databricks CLI 1.17.0 downloaded from the official release and SHA256 verified.
- Strict bundle validation passed; training job deployed successfully.
- Job ID: 446848359557450.
- First run 24543372245565 exposed a serverless runner difference: `__file__`
  is undefined. Fixed by passing the deployed source directory explicitly.
- Run 1034452564613764 trained successfully and created three managed Delta
  tables in sentinelops_dev.sentinelops_dev. Cloud RMSE was 18.3627 cycles
  with the older Python 3.10 environment. MLflow model serialization failed
  because skops requires trust for sklearn's TreePredictor type.
- The serialization policy now trusts only that known type for the model fitted
  in this job; the local save/reload regression test passed.
- Cloud environment updated to version 4 (Python 3.12); numpy, pandas,
  scikit-learn, MLflow and skops versions pinned.
- Latest verification run: 99784281689509 — **SUCCESS**.
- MLflow run ID: d4cb1f49d8444811999bbaa4dcccbfc0.
- Existing Delta tables: silver_fd001_train, gold_fd001_features,
  gold_fd001_predictions. MLflow experiment ID: 2331823695746663.
- Catalog is bound exclusively to this workspace. Predictive optimization is
  disabled to avoid background maintenance compute.
- Job uses standard performance mode, max one concurrent run, no application
  retries, a 15-minute timeout and no recurring schedule.

## Scope still pending

See **Task status** at the top of this file. It is the single tracker; this
section is kept only so older links still resolve.

## Cost and runtime controls

- **Limit:** the approved operating budget is up to $10/day, treated as CAD
  (the conservative reading). There is no hard daily cutoff. Budget
  `sentinelops-dev-monthly` (CAD 150/month, both resource groups) emails at
  50%, 80% and 100% of actual spend and at 100% of forecast. Budgets alert;
  they don't stop spending.
- **Posted cost by day** (UTC; `infra/cost-query.json`, both resource groups).
  Billing lags about 9 hours, so recheck later figures before trusting them.

  | Day | Posted | By meter | Notes |
  |---|---|---|---|
  | September 23 | CAD 5.41 (final) | Serverless SQL 2.16, serverless jobs 1.85, NAT and IP 1.31 (19 h) | The projection of CAD 3–4 missed a Catalog Explorer browse at 19:23 UTC, which ran the Small warehouse for about 11 minutes (2.2 DBU) |
  | September 24 | CAD 11.65 by 02:05 UTC on September 25 (usage to ~17:00) | At 11.65 (`infra/cost-query-meters.json`): serverless jobs 3.17, serverless SQL 4.37, serverless real-time inference 2.57 (pay-per-token model calls plus the serving demo), NAT and IP 1.48. At the 8.05 posted by 16:55 UTC: serverless jobs 2.60, serverless SQL 2.42 (Catalog Explorer, 05:44), model calls 2.23, NAT and IP 0.76 | Projected ≈ CAD 13.4: the fixed remainder (~0.9), dashboards/Genie (~1.6), the serving demo and eval v2 (~2.0: serving ≤0.35, jobs ~0.5, model calls ~0.4, warehouse checks ~0.7), then masking v2 (~0.8). Over CAD 10; you approved it against credits expiring October 10. Then the weather backfill (21:51–23:50 UTC, ~CAD 0.7–1.1), so ≈ CAD 14.5. CAD 8.86 had posted by 23:55 UTC and 11.65 by 02:05 UTC on September 25. The Cost Management API returns 429 in bursts, so recheck later |
  | September 24 (recheck) | **CAD 14.14** by 14:25 UTC on September 25 (near final) | Serverless jobs 4.57, serverless SQL 4.72, serverless real-time inference 3.05, NAT gateway 1.56, public IP 0.17, storage and bandwidth 0.07 | Matches the ≈ CAD 14.1 that `system.billing` DBUs predicted, and the ≈ 14.5 projection. Over the CAD 10 guide, as you approved against the credits. (13.42 had posted by 06:27 UTC) |
  | September 25 | CAD 1.78 by 14:25 UTC (not final) | Serverless jobs 1.06 (to ~05:00), NAT gateway 0.57, public IP 0.06, Event Hubs "Standard Ingress Events" 0.0005, Service Bus messaging 0.00 | Projected ≈ CAD 3–3.5: the fixed ~1.7, the Event Hubs demo, three CI runs and the PR's merge (~0.3–0.5 of serverless each), the dev retrain (~0.4–0.6), predictive optimization (~0.5). **Kafka meter:** no "Standard Kafka Endpoint" charge had posted by 14:35 UTC, but neither had the throughput-unit charge, so recheck both before calling it |

- **Rates** (Azure Retail Prices, `westus2`, CAD):
  - serverless jobs CAD 0.62/DBU (about 1.5 DBU per hour of job time);
  - serverless SQL CAD 0.97/DBU (the 2X-Small warehouse is 4 DBU/h);
  - model serving CAD 0.097/DBU.
- **Fixed cost:** a NAT gateway and a static public IP in the managed resource
  group bill 24/7, about CAD 0.07/hour or **~CAD 1.7/day with no compute**.
  They come from the workspace's secure-cluster-connectivity networking, which
  only classic compute uses. Storage and bandwidth are negligible.
- **Biggest avoidable cost found:** opening sample data in Catalog Explorer
  starts the SQL warehouse. It cost CAD 2.16 and 2.42 on the two days, at the
  old Small size. The warehouse is now 2X-Small with a 5-minute auto-stop
  (about CAD 0.35 per wake-up).
- **Compute controls:** no classic clusters, no recurring job schedules, and
  no persistent serving or Vector Search endpoints. Every job is manual, with
  timeouts and zero retries.
- **Same-day usage view:** since September 25 you can read `system.billing`
  (granted through the new metastore admin group). Its usage lagged about 3.8
  hours, against about 9 for Azure Cost Management, and it reports DBUs per
  product (jobs, pipelines, SQL, serving, AI Gateway, predictive
  optimization). `cmapss_verify` and `cmapss_retrain`'s `verify` task add a
  same-day billing snapshot when run as you; the CI principals have no access.
  - Cross-check for September 24 (DBUs up to 00:00 UTC on the 25th, at the
    CAD rates above): jobs and pipelines 7.33 DBU ≈ 4.54, SQL 4.87 ≈ 4.72,
    serving and AI Gateway 31.45 ≈ 3.05, plus NAT and IP ~1.72, ≈ **CAD
    14.1** in total. Azure had posted 13.42.
  - It also shows **predictive optimization** DBUs: 0.40, 0.15 and 0.88
    (September 25 to 09:00 UTC, ≈ CAD 0.55) on September 23, 24 and 25,
    rising with the number of pipelines. The three SentinelOps catalogs have
    it off; the metastore default is on, inherited by the workspace default
    catalog (`dbw_sentinelops_dev`, no tables) and presumably by
    `__databricks_internal`, where pipelines keep internal state tables. The
    billing rows carry no table (`usage_metadata` is empty; `run_as` is a
    Databricks-managed identity). Tracing it per table needs read access to
    `system.storage.predictive_optimization_operations_history` (ask first).

The local benchmark demonstrates predictive performance on simulated engines.
It is not evidence of reduced real-world downtime or an operational safety system.
Azure budgets provide alerts, not a hard spending cutoff. This project currently
has no recurring compute schedule and does not provision persistent AI endpoints.
