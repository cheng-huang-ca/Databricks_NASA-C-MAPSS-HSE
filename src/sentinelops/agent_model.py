"""Models-from-code entry point for the OSHA agent: jobs/log_osha_agent.py logs this file as the
python_model, with the sentinelops package as code and the index and documents as artifacts."""
import mlflow

from sentinelops.agent import OshaAgent

mlflow.models.set_model(OshaAgent())
