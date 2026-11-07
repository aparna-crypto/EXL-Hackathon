"""MLflow tracking. TRACKING_BACKEND=local|azureml selects the tracking URI; code is identical."""

from __future__ import annotations

import os
from pathlib import Path

import mlflow


def configure(cfg: dict) -> str:
    """Point MLflow at the configured backend and experiment. Returns the tracking URI."""
    t = cfg["tracking"]
    if t["backend"] == "local":
        uri = Path(t["local_uri"])
        if not uri.is_absolute():
            from factory.config import REPO_ROOT
            uri = REPO_ROOT / uri
        uri = uri.as_uri()
    elif t["backend"] == "azureml":
        uri = os.getenv(t["azureml_uri_env"], "")
        if not uri:
            raise RuntimeError(f"TRACKING_BACKEND=azureml needs env var {t['azureml_uri_env']}")
        import azureml.mlflow  # noqa: F401  (registers the azureml:// scheme; requirements-azure.txt)
    else:
        raise ValueError(f"unknown tracking backend {t['backend']!r}")
    mlflow.set_tracking_uri(uri)
    mlflow.set_experiment(t["experiment"])
    return uri


def log_agent_run(agent: str, model: str, prompt: str, system: str, output: str,
                  duration_s: float, input_tokens: int, output_tokens: int, cost_usd: float,
                  cached: bool, run_id: str, model_id: str | None = None) -> None:
    """One MLflow run per agent call (nested under the active pipeline run if there is one)."""
    with mlflow.start_run(run_name=f"agent:{agent}", nested=mlflow.active_run() is not None):
        mlflow.set_tags({"kind": "agent_call", "agent": agent, "factory_run_id": run_id,
                         "model_id": model_id or ""})
        mlflow.log_params({"llm_model": model, "cached": cached})
        mlflow.log_metrics({"duration_s": duration_s, "input_tokens": input_tokens,
                            "output_tokens": output_tokens, "cost_usd": cost_usd})
        mlflow.log_text(system, "system_prompt.txt")
        mlflow.log_text(prompt, "prompt.txt")
        mlflow.log_text(output, "output.txt")


def log_gate_decision(entry: dict) -> None:
    with mlflow.start_run(run_name=f"gate:{entry['stage']}", nested=mlflow.active_run() is not None):
        mlflow.set_tags({"kind": "gate_decision", "model_id": entry["model_id"],
                         "stage": entry["stage"], "decision": entry["decision"],
                         "reviewer": entry["reviewer"]})
        mlflow.log_dict(entry, "gate_decision.json")
