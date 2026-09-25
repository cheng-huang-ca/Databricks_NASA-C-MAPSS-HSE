from pathlib import Path

import yaml

RESOURCES = Path(__file__).resolve().parents[1] / "resources"


def jobs():
    """Every job, including target-only ones (the dev-only agent jobs)."""
    for path in sorted(RESOURCES.glob("*.yml")):
        config = yaml.safe_load(path.read_text()) or {}
        yield from (config.get("resources") or {}).get("jobs", {}).items()
        for target in (config.get("targets") or {}).values():
            yield from ((target or {}).get("resources") or {}).get("jobs", {}).items()


def test_every_job_is_manual_standard_single_run_bounded_and_not_retried():
    found = dict(jobs())
    assert "osha_answer_eval" in found and len(found) >= 10
    for name, job in found.items():
        assert job["max_concurrent_runs"] == 1 and job["performance_target"] == "STANDARD", name
        assert job.get("timeout_seconds", 0) > 0, name
        assert not {"schedule", "trigger", "continuous"} & set(job), name
        for task in job["tasks"]:
            # Serverless auto-optimization retries failed tasks even with max_retries 0 (seen on
            # cmapss_retrain run 245262911606779), which could repeat paid model calls.
            assert task["max_retries"] == 0 and task["disable_auto_optimization"] is True, (name, task["task_key"])


def test_retraining_chain_runs_in_order_and_retrains_only_on_change():
    job = dict(jobs())["cmapss_retrain"]
    tasks = {task["task_key"]: task for task in job["tasks"]}
    chain = ["ingest", "verify", "train", "promote", "score", "monitor", "alerts"]
    assert list(tasks) == [*chain, "analytics"]
    for previous, key in zip(chain, chain[1:]):
        assert tasks[key]["depends_on"] == [{"task_key": previous}] and "run_if" not in tasks[key]
    # The marts refresh beside alerts: a breach can't leave them stale, and a failed dashboard
    # check can't suppress an alert. The refresh job is reused, not copied.
    assert tasks["analytics"]["depends_on"] == [{"task_key": "monitor"}] and "run_if" not in tasks["analytics"]
    assert tasks["analytics"]["run_job_task"] == {"job_id": "${resources.jobs.analytics_refresh.id}"}
    parameters = {key: task.get("spark_python_task", {}).get("parameters", []) for key, task in tasks.items()}
    assert "--only-if-changed" in parameters["train"] and "--only-pending" in parameters["promote"]
    assert {p["name"]: p["default"] for p in job["parameters"]}["force_retrain"] == "false"
    assert job["timeout_seconds"] >= sum(task["timeout_seconds"] for task in job["tasks"])
