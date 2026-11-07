from factory.config import load_config


def test_env_overrides(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "fake")
    monkeypatch.setenv("TRACKING_BACKEND", "azureml")
    c = load_config()
    assert c["llm"]["provider"] == "fake"
    assert c["tracking"]["backend"] == "azureml"


def test_every_agent_model_has_a_price():
    c = load_config()
    models = set(c["llm"]["agents"].values()) | set(c["llm"]["escalation"].values())
    assert models <= set(c["llm"]["pricing_usd_per_mtok"])


def test_config_contains_no_secrets():
    from factory.config import DEFAULT_CONFIG
    assert "sk-ant" not in DEFAULT_CONFIG.read_text()
