import json

import pytest

from factory.llm import BudgetExceeded, FakeBackend, LLMClient


def client(cfg, **kw):
    return LLMClient(cfg, backend=kw.pop("backend", FakeBackend()), log_to_mlflow=False, **kw)


def test_routing_and_escalation(cfg):
    c = client(cfg)
    assert c.model_for("scanner") == "claude-haiku-5-5"
    assert c.model_for("documentation") == "claude-sonnet-5-5"
    assert c.model_for("translation", escalate=True) == "claude-opus-5-5"
    with pytest.raises(ValueError):
        c.model_for("unknown_agent")


def test_cost_uses_config_prices(cfg):
    c = client(cfg)
    assert c.cost("claude-sonnet-5-5", 1_000_000, 0) == pytest.approx(3.0)
    assert c.cost("claude-sonnet-5-5", 0, 1_000_000) == pytest.approx(15.0)


def test_cache_makes_reruns_free(cfg):
    backend = FakeBackend()
    first = client(cfg, backend=backend).complete("scanner", "hello")
    second = client(cfg, backend=backend).complete("scanner", "hello")
    assert backend.calls == 1
    assert not first.cached and second.cached
    assert second.cost_usd == 0 and second.text == first.text


def test_ledger_records_every_call(cfg):
    c = client(cfg)
    c.complete("scanner", "a", model_id="m1")
    c.complete("scanner", "a", model_id="m1")
    rows = [json.loads(x) for x in open(cfg["paths"]["llm_ledger"])]
    assert len(rows) == 2 and [r["cached"] for r in rows] == [False, True]
    assert rows[0]["session_id"] == cfg["llm"]["budget"]["session_id"]
    assert "text" not in rows[0]


def test_per_run_token_cap(cfg):
    cfg["llm"]["budget"]["per_run_tokens"] = 100
    with pytest.raises(BudgetExceeded, match="per-run token cap"):
        client(cfg).complete("scanner", "x", max_tokens=500)


def test_per_run_usd_cap(cfg):
    cfg["llm"]["budget"]["per_run_usd"] = 0.0001
    with pytest.raises(BudgetExceeded, match="per-run cap"):
        client(cfg).complete("documentation", "x", max_tokens=2000)


def test_session_cap_spans_runs(cfg):
    cfg["llm"]["budget"]["session_usd"] = 0.05
    c1 = client(cfg)
    c1.complete("documentation", "first", max_tokens=1000)  # worst case ~ $0.015
    c1.complete("documentation", "second", max_tokens=1000)
    c1.complete("documentation", "third", max_tokens=1000)
    with pytest.raises(BudgetExceeded, match="session"):
        # spend so far is small but worst-case of a 2000-token call pushes past the cap
        client(cfg).complete("documentation", "y" * 30000, max_tokens=2000)


def test_budget_blocks_before_calling_provider(cfg):
    backend = FakeBackend()
    cfg["llm"]["budget"]["per_run_tokens"] = 10
    with pytest.raises(BudgetExceeded):
        client(cfg, backend=backend).complete("scanner", "x" * 100)
    assert backend.calls == 0


def test_thinking_setting_is_passed_per_agent(cfg):
    seen = []

    class Recorder(FakeBackend):
        def call(self, model, system, prompt, max_tokens, temperature, thinking=None):
            seen.append(thinking)
            return super().call(model, system, prompt, max_tokens, temperature)

    c = LLMClient(cfg, backend=Recorder(), log_to_mlflow=False)
    c.complete("inventory", "a")
    c.complete("translation", "a")
    assert seen == ["disabled", None]


def test_provider_error_becomes_llm_error(cfg):
    from factory.llm import LLMError

    class Broken(FakeBackend):
        def call(self, *a, **k):
            raise ValueError("400 bad request")

    c = LLMClient(cfg, backend=Broken(), log_to_mlflow=False)
    with pytest.raises(LLMError, match="400 bad request"):
        c.complete("inventory", "a")
