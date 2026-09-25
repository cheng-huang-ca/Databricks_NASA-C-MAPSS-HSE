# Environments and CI/CD

Repository: https://github.com/cheng-huang-ca/Databricks_NASA-C-MAPSS-HSE (public). It moved here
from a deleted account; the federation policies were re-pointed to the new owner and repository
IDs, so the old subjects match nothing.

Three bundle targets share one workspace and one Unity Catalog metastore:

| Target | Catalog | Deployed and run by | How |
|---|---|---|---|
| `dev` | `sentinelops_dev` | the developer (development mode) | `bundle deploy -t dev` from a laptop |
| `staging` | `sentinelops_staging` | service principal `sentinelops-staging-ci` | GitHub Actions on every push to `main` |
| `prod` | `sentinelops_prod` | service principal `sentinelops-prod-ci` | GitHub Actions after staging, once a reviewer approves |

## Identity: OIDC, no secrets

GitHub Actions requests a short-lived OIDC token for the job and exchanges it for a Databricks
OAuth token (`DATABRICKS_AUTH_TYPE=github-oidc`). Databricks accepts the exchange only if a
**federation policy** on the service principal matches the token.

| Service principal | Application (client) ID | Federation subjects (issuer `https://token.actions.githubusercontent.com`) |
|---|---|---|
| `sentinelops-staging-ci` | `02bece01-ccd7-4130-8e8d-c8de43583e41` | `repo:cheng-huang-ca@333634208/Databricks_NASA-C-MAPSS-HSE@1386801570:environment:staging`, `repo:cheng-huang-ca@333634208/Databricks_NASA-C-MAPSS-HSE@1386801570:pull_request` |
| `sentinelops-prod-ci` | `5d729946-2748-48f4-9300-ba9a559bf93d` | `repo:cheng-huang-ca@333634208/Databricks_NASA-C-MAPSS-HSE@1386801570:environment:prod` |

- **Two details the first CI run exposed** (from Databricks' own error message):
  - **Subject:** GitHub puts immutable numeric IDs in the subject,
    `repo:<owner>@<owner id>/<repo>@<repo id>:...`, not `repo:<owner>/<repo>:...`. A renamed
    or deleted-and-recreated repository therefore can't inherit access.
  - **Audience:** the Databricks CLI requests the GitHub token for the workspace endpoint
    `https://adb-7405619144539463.3.azuredatabricks.net/oidc/v1/token`, not the account ID.
    The policies accept both.
- **No credentials anywhere.** There are no tokens, client secrets or keys in the repository, the
  workflow or GitHub secrets. Client IDs aren't secrets: without a matching federated token,
  they grant nothing.
- **Pull requests** validate the bundle as the staging principal. Pull requests from forks get no
  OIDC token, so they run only the unit tests.
- **Least privilege:**
  - Each principal is a plain workspace user (`workspace-access`, `databricks-sql-access`; not an
    admin).
  - Each has `ALL PRIVILEGES` on **its own catalog only**, and `CAN_USE` on the starter warehouse
    (for dashboards).
  - The catalogs belong to the developer, who keeps control.
- **Setup scripts:** the catalogs (managed storage under the project's external location, bound to
  this workspace only, predictive optimization off) come from
  `scripts/setup_environment_catalogs.py`. The service principals, their workspace assignment and
  their federation policies were created with the account API (the commands are below).

## What each target contains

- **The same bundle** (16 jobs, 4 pipelines, 2 dashboards), with `${var.catalog}` switching
  every table and volume path.
- **Target-only resources** (`resources/environments.yml`): the schemas `sentinelops`, `bronze`,
  `silver` and `gold`, plus the `landing` volume, created by the principal on deploy.
  Pipelines refer to the bronze schema resource, so the first deploy creates it first.
- **Dev-only:** the Genie space, because it can't be created until its tables exist.
- **Staging runs C-MAPSS end to end on every push:**
  - The workflow downloads the MD5-verified NASA archive (cached) and prepares the same landing
    files as dev (`python -m sentinelops.landing`).
  - It uploads them without overwriting (`scripts/upload_landing.py`: an existing file must be
    identical), then runs `cmapss_ingest` and `cmapss_verify` as the staging principal.
  - Reruns upload nothing and append nothing.
- **Prod is deploy-only:** the jobs and pipelines exist, but no data is landed and nothing runs.

## The workflow (`.github/workflows/ci.yml`)

| Event | Jobs |
|---|---|
| Pull request | Unit tests → `bundle validate --strict` for staging and prod |
| Push to `main` | Unit tests → deploy staging, land C-MAPSS, ingest, verify → **wait for approval** → deploy prod |
| Manual (`workflow_dispatch`) on `main` | Same as a push; optionally allows a destructive staging deploy (below) |
| Only `docs/**` or Markdown changed | Nothing runs (`paths-ignore`) |

- **Destructive deploys are opt-in.** If a change would delete or recreate schemas or volumes,
  `bundle deploy` refuses without `--auto-approve`. The **allow_staging_recreate** checkbox
  on a manual run (default off) passes it to the staging deploy only. Pushes can't set it, and
  prod never uses it. Read the refused plan in the failed run first.
- **Reading staging or prod data as a human.** The principals own their schemas. Owning the
  catalog lets the developer *grant* access, not read, so grant yourself `USE SCHEMA` and
  `SELECT` when needed.

- **Pinned actions:** every action is pinned to a commit SHA (checkout v7.0.1, setup-python
  v7.0.0, cache v6.1.0, Databricks setup-cli v1.17.0, the same CLI version as local
  development). Only the deploy and validate jobs get `id-token: write`.
- **No cancellation:** deployments never cancel each other; `concurrency` queues them.
- **GitHub environments:**
  - `staging`: deployments from `main` only.
  - `prod`: a required reviewer, deployments from `main` only.
  - They must exist **before** the first push. A job that names a missing environment creates it
    with no protection.

## First successful run

Run `36098067567` (manual, `allow_staging_recreate` ticked once): all 122 tests passed.

- **Staging**, deployed as `sentinelops-staging-ci` in 18.5 minutes:
  - It created 16 jobs, 4 pipelines, the schemas and the landing volume, recreating four empty
    schemas an earlier misconfiguration had misnamed.
  - It landed C-MAPSS. `cmapss_ingest` (run `459623161285845`, 8.7 min) and `cmapss_verify`
    (run `104076494798937`, 8.0 min) both succeeded.
- **Prod** was deployed as `sentinelops-prod-ci` after the required reviewer approved.

The first code push after it (run `36100233863`) proved the pipeline is idempotent under the
principal:
- Staging redeployed with no destructive changes, and the upload found every file identical.
- `cmapss_ingest` (`415902458418397`) appended nothing, with every Silver and Gold table planned
  as `NO_OP`.
- `cmapss_verify` (`591333246681954`) passed with the same counts.

The failed attempts before it are recorded in `cicd-first-run.json`: the OIDC subject and
audience, CI-only strict warnings, a schema-renaming name prefix, the destructive-deploy guard,
and the GitHub account changes.

## Costs

- **GitHub Actions:** free for public repositories.
- **Deploys:** free.
- **Staging ingest and verify:** about 17–30 minutes of serverless job time per code push to
  `main` (≈ CAD 0.3–0.5). It's the main recurring cost; docs-only pushes skip it.

## Commands used for setup (account admin, run once)

```powershell
# Account API (Azure CLI auth needs the tenant for the account host):
$env:ARM_TENANT_ID='<tenant id>'; $env:DATABRICKS_HOST='https://accounts.azuredatabricks.net'
$env:DATABRICKS_ACCOUNT_ID='5b731cd2-ed03-4635-963e-154fc8b4f034'; $env:DATABRICKS_AUTH_TYPE='azure-cli'
databricks account service-principals create --display-name sentinelops-staging-ci --active
databricks account workspace-assignment update 7405619144539463 <principal id> --json '{"permissions": ["USER"]}'
databricks account service-principal-federation-policy create <principal id> --json '{"oidc_policy": {"issuer": "https://token.actions.githubusercontent.com", "audiences": ["https://adb-7405619144539463.3.azuredatabricks.net/oidc/v1/token", "5b731cd2-ed03-4635-963e-154fc8b4f034"], "subject": "repo:cheng-huang-ca@333634208/Databricks_NASA-C-MAPSS-HSE@1386801570:environment:staging"}}'
# Workspace: entitlements (SCIM patch), catalogs and grants, warehouse CAN_USE:
.venv/Scripts/python.exe scripts/setup_environment_catalogs.py
databricks permissions update warehouses <warehouse id> --json '{"access_control_list": [{"service_principal_name": "<application id>", "permission_level": "CAN_USE"}]}'
```
