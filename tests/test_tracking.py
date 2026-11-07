import mlflow
import pytest

from factory import tracking
from factory.llm import FakeBackend, LLMClient


def test_agent_call_logged_to_local_mlflow(cfg):
    tracking.configure(cfg)
    LLMClient(cfg, backend=FakeBackend()).complete("scanner", "scan this", model_id="m1")
    runs = mlflow.search_runs(experiment_names=[cfg["tracking"]["experiment"]])
    assert len(runs) == 1
    row = runs.iloc[0]
    assert row["tags.agent"] == "scanner"
    assert row["params.llm_model"] == "claude-haiku-5-5"
    assert row["metrics.input_tokens"] > 0


def test_azureml_backend_requires_uri(cfg, monkeypatch):
    cfg["tracking"]["backend"] = "azureml"
    monkeypatch.delenv("AZUREML_MLFLOW_URI", raising=False)
    with pytest.raises(RuntimeError, match="AZUREML_MLFLOW_URI"):
        tracking.configure(cfg)
