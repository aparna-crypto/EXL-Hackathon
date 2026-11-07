import pytest

from factory.config import load_config


@pytest.fixture
def cfg(tmp_path, monkeypatch):
    """Config with every output path redirected to a temp dir and the fake LLM provider."""
    monkeypatch.setenv("LLM_PROVIDER", "fake")
    monkeypatch.setenv("TRACKING_BACKEND", "local")
    c = load_config()
    c["paths"] = {
        "artifacts": str(tmp_path),
        "model_cards": str(tmp_path / "model_cards"),
        "audit": str(tmp_path / "audit"),
        "llm_cache": str(tmp_path / "llm_cache"),
        "llm_ledger": str(tmp_path / "audit" / "llm_ledger.jsonl"),
    }
    c["tracking"]["local_uri"] = str(tmp_path / "mlruns")
    return c
