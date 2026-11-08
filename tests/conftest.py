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
        "inventory": str(tmp_path / "inventory"),
        "model_docs": str(tmp_path / "model_docs"),
        "redeveloped": str(tmp_path / "redeveloped"),
        "redevelop_artifacts": str(tmp_path / "redevelop"),
        "parity_report": str(tmp_path / "parity_report.md"),
        "validation_reports": str(tmp_path / "validation_reports"),
        "validation_artifacts": str(tmp_path / "validation"),
        "monitoring_reports": str(tmp_path / "monitoring_reports"),
        "monitoring_artifacts": str(tmp_path / "monitoring"),
    }
    c["llm"]["budget"].update(per_run_usd=10.0, session_usd=100.0)  # tests never use live caps
    c["tracking"]["local_uri"] = str(tmp_path / "mlruns")
    return c
