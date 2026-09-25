import fnmatch
import importlib.util
import io
from pathlib import Path
import re
import subprocess

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
BUNDLE = yaml.safe_load((ROOT / "databricks.yml").read_text())
WORKFLOW = yaml.safe_load((ROOT / ".github/workflows/ci.yml").read_text())
JOBS = WORKFLOW["jobs"]
TRIGGERS = WORKFLOW[True]  # PyYAML reads the bare key `on` as the boolean True.


def run_as(target):
    return BUNDLE["targets"][target]["run_as"]["service_principal_name"]


def test_each_environment_has_its_own_catalog_and_principal():
    staging, prod = BUNDLE["targets"]["staging"], BUNDLE["targets"]["prod"]
    assert staging["mode"] == prod["mode"] == "production"
    assert run_as("staging") != run_as("prod")
    catalogs = {t: BUNDLE["targets"][t].get("variables", {}).get("catalog", BUNDLE["variables"]["catalog"]["default"])
                for t in ("dev", "staging", "prod")}
    assert catalogs == {"dev": "sentinelops_dev", "staging": "sentinelops_staging", "prod": "sentinelops_prod"}
    for name, target in (("staging", staging), ("prod", prod)):
        assert {"user_name": "cheng.huang.ca@outlook.com", "level": "CAN_MANAGE"} in target["permissions"]
        # The principal owns its bundle folder; strict validation fails unless the bundle says so.
        assert {"service_principal_name": run_as(name), "level": "CAN_MANAGE"} in target["permissions"]
        # name_prefix renames Unity Catalog schemas too (run 36094370073 created staging_bronze), and
        # the code addresses schemas by name: environments are told apart by a tag instead.
        assert "name_prefix" not in target["presets"] and target["presets"]["tags"] == {"environment": name}
    script = (ROOT / "scripts/setup_environment_catalogs.py").read_text()
    for target, catalog in (("staging", "sentinelops_staging"), ("prod", "sentinelops_prod")):
        assert re.search(rf'"{catalog}": \("{target}", "{run_as(target)}"\)', script)


def test_every_sync_pattern_matches_a_tracked_file():
    # CI deploys from a clean checkout, where a pattern matching nothing (say, a git-ignored
    # directory) is a warning, and strict validation fails on warnings.
    tracked = subprocess.run(["git", "ls-files"], cwd=ROOT, capture_output=True, text=True, check=True).stdout.split()
    for kind, patterns in BUNDLE["sync"].items():
        for pattern in patterns:
            assert any(fnmatch.fnmatch(path, pattern.replace("**", "*")) for path in tracked), (kind, pattern)


def test_environment_resources_create_schemas_before_pipelines_use_them():
    environments = yaml.safe_load((ROOT / "resources/environments.yml").read_text())["targets"]
    assert environments["staging"]["resources"] == environments["prod"]["resources"]
    resources = environments["staging"]["resources"]
    assert {s["name"] for s in resources["schemas"].values()} == {"${var.schema}", "bronze", "silver", "gold"}
    assert resources["volumes"]["landing"]["schema_name"] == "${resources.schemas.base.name}"
    pipelines = set()
    for path in (ROOT / "resources").glob("*.yml"):
        pipelines |= set((yaml.safe_load(path.read_text()).get("resources") or {}).get("pipelines", {}))
    assert set(resources["pipelines"]) == pipelines
    assert all(p == {"schema": "${resources.schemas.bronze.name}"} for p in resources["pipelines"].values())
    analytics = yaml.safe_load((ROOT / "resources/analytics.yml").read_text())
    assert "genie_spaces" not in analytics["resources"] and set(analytics["targets"]) == {"dev"}


def test_workflow_pins_actions_and_limits_oidc_tokens():
    assert WORKFLOW["permissions"] == {"contents": "read"}
    for name, job in JOBS.items():
        for step in job["steps"]:
            if "uses" in step:
                assert re.fullmatch(r"[\w.-]+/[\w.-]+@[0-9a-f]{40}", step["uses"]), (name, step["uses"])
        assert job.get("timeout-minutes"), name
        if name == "test":
            assert "permissions" not in job and "DATABRICKS_CLIENT_ID" not in job.get("env", {})
        else:
            assert job["permissions"] == {"contents": "read", "id-token": "write"}, name
    assert set(TRIGGERS) == {"pull_request", "push", "workflow_dispatch"} and TRIGGERS["push"]["branches"] == ["main"]
    # Only documentation may skip CI; code, config and data contracts always run.
    ignored = TRIGGERS["push"]["paths-ignore"]
    assert ignored == TRIGGERS["pull_request"]["paths-ignore"] == ["docs/**", "*.md", "**/*.md"]
    assert WORKFLOW["concurrency"]["cancel-in-progress"] is False


def test_each_deploy_uses_its_targets_principal_and_prod_waits_for_staging():
    assert JOBS["validate"]["env"]["DATABRICKS_CLIENT_ID"] == run_as("staging")
    assert "head.repo.full_name == github.repository" in JOBS["validate"]["if"]
    # Strict only where the identity matches the target: as the staging principal, prod's strict
    # validation fails on the principal's own folder permissions (run 36104608730).
    assert [s.get("run") for s in JOBS["validate"]["steps"] if "run" in s] == [
        "databricks bundle validate --strict -t staging", "databricks bundle validate -t prod"]
    assert (JOBS["staging"]["environment"], JOBS["staging"]["env"]["DATABRICKS_CLIENT_ID"]) == ("staging", run_as("staging"))
    assert "refs/heads/main" in JOBS["staging"]["if"] and "pull_request" in JOBS["staging"]["if"]
    assert (JOBS["prod"]["environment"], JOBS["prod"]["env"]["DATABRICKS_CLIENT_ID"]) == ("prod", run_as("prod"))
    assert JOBS["prod"]["needs"] == "staging" and JOBS["staging"]["needs"] == "test"
    commands = [step.get("run", "") for step in JOBS["staging"]["steps"]]
    order = [next(i for i, c in enumerate(commands) if needle in c) for needle in (
        "bundle deploy -t staging", "sentinelops.landing", "upload_landing.py", "run -t staging cmapss_ingest",
        "run -t staging cmapss_verify")]
    assert order == sorted(order)
    upload = next(c for c in commands if "upload_landing.py" in c)
    assert upload.endswith("/Volumes/sentinelops_staging/sentinelops/landing/cmapss_ingest/v1")
    assert [s.get("run") for s in JOBS["prod"]["steps"] if "run" in s] == [
        "databricks bundle validate --strict -t prod", "databricks bundle deploy -t prod"]


def test_only_a_manual_run_can_let_staging_recreate_data_assets():
    switch = TRIGGERS["workflow_dispatch"]["inputs"]["allow_staging_recreate"]
    assert switch["type"] == "boolean" and switch["default"] is False
    steps = [(name, step.get("run", "")) for name, job in JOBS.items() for step in job["steps"]]
    approving = [(name, run) for name, run in steps if "auto-approve" in run]
    assert approving == [("staging", "databricks bundle deploy -t staging ${{ github.event_name == 'workflow_dispatch' "
                                     "&& inputs.allow_staging_recreate && '--auto-approve' || '' }}")]


def load_uploader():
    spec = importlib.util.spec_from_file_location("upload_landing", ROOT / "scripts/upload_landing.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_landing_upload_never_overwrites(tmp_path):
    from databricks.sdk.errors import NotFound

    class Files:
        def __init__(self, present):
            self.present, self.uploads = dict(present), []

        def get_metadata(self, path):
            if path not in self.present:
                raise NotFound(path)

        def create_directory(self, path):
            pass

        def upload(self, path, contents, overwrite=None):
            assert overwrite is False and path not in self.present
            self.present[path] = contents.read()
            self.uploads.append(path)

        def download(self, path):
            return type("Download", (), {"contents": io.BytesIO(self.present[path])})()

    (tmp_path / "labels").mkdir()
    (tmp_path / "labels/FD001.json").write_bytes(b'{"unit": 1}\n')
    (tmp_path / "manifest.json").write_bytes(b"{}\n")
    client = type("Client", (), {"files": Files({"/Volumes/c/s/v/x/manifest.json": b"{}\n"})})()
    summary = load_uploader().upload(client, tmp_path, "/Volumes/c/s/v/x/")
    assert summary == {"uploaded": ["labels/FD001.json"], "identical": ["manifest.json"], "bytes": 12}
    client.files.present["/Volumes/c/s/v/x/manifest.json"] = b'{"changed": true}\n'
    with pytest.raises(ValueError, match="immutable"):
        load_uploader().upload(client, tmp_path, "/Volumes/c/s/v/x")
