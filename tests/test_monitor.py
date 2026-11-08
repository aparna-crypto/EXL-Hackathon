import json

import pandas as pd
import pytest

from factory import model_card, monitor, orchestrator, validate
from factory.config import REPO_ROOT
from factory.llm import FakeBackend, LLMClient

DATA = REPO_ROOT / "data" / "processed"
CARD = REPO_ROOT / "artifacts/model_cards/credit_scorecard.json"


def test_first_trigger_rule():
    assert monitor.first_trigger(["green", "amber", "green", "amber"], 2) is None
    assert monitor.first_trigger(["green", "amber", "amber", "red"], 2) == 2
    assert monitor.first_trigger(["green", "red", "amber"], 2) == 1
    assert monitor.first_trigger(["green"] * 5, 2) is None


def test_make_batch_is_deterministic_and_drifts():
    pool = pd.DataFrame({"id": range(200), "bad": [0, 1] * 100,
                         "credit_amount": [1000] * 200, "duration": [12] * 200})
    mcfg = {"batch_size": 500, "drift": {"start_month": 3, "amount_inflation_per_month": 0.1,
                                         "long_term_share_per_month": 1.0, "long_term_extra_months": 6,
                                         "duration_cap": 15,
                                         "risk_tilt_per_month": 0.5}}
    a, b = monitor.make_batch(pool, 1, mcfg, 42), monitor.make_batch(pool, 1, mcfg, 42)
    pd.testing.assert_frame_equal(a, b)
    assert a.drift_k.iloc[0] == 0 and (a.credit_amount == 1000).all()
    late = monitor.make_batch(pool, 4, mcfg, 42)  # k = 2
    assert late.drift_k.iloc[0] == 2
    assert (late.credit_amount == 1200).all() and (late.duration == 15).all()  # capped
    assert late.bad.mean() > a.bad.mean() + 0.1  # riskier intake


def fake_alert(model, system, prompt):
    if "monitoring analyst" in system:
        return json.dumps({"alert": "Breach in month [[trigger.month]]. PSI was 0.99.",
                           "likely_drivers": ["Largest shift in [[trigger.month]]."],
                           "recommended_actions": ["Re-validate.", "Uses [[nope]]."]})
    from tests.test_validate import fake_report
    return fake_report(model, system, prompt)


def _approved_card(cfg):
    card = json.loads(CARD.read_text())
    card["stages"]["validate"]["status"] = "approved"
    card.pop("monitoring", None)
    model_card.save(card, cfg["paths"]["model_cards"], "test copy")
    return model_card.load(cfg["paths"]["model_cards"], "credit_scorecard")


@pytest.mark.skipif(not (DATA / "german_credit_test.csv").exists(), reason="needs make data")
def test_monitor_triggers_revalidation_and_reopens_gate4(cfg):
    card = _approved_card(cfg)
    llm = LLMClient(cfg, backend=FakeBackend(fake_alert), log_to_mlflow=False)
    ctx = orchestrator.Context(cfg, orchestrator.MODELS_DIR / "credit_scorecard", _llm=llm)
    out = monitor.run(card, ctx)
    mon = card["monitoring"]
    start = cfg["monitor"]["drift"]["start_month"]
    assert all(m["rag"] == "green" for m in mon["months"] if m["month"] < start)
    t = out["trigger"]
    assert t is not None and t["month"] >= start
    # pre-drift months never trigger; trigger respects the rule
    rags = [m["rag"] for m in mon["months"]]
    assert monitor.first_trigger(rags, cfg["monitor"]["trigger"]["consecutive_amber"]) == \
        t["month"] - 1
    # alert grounded: typed numbers and unknown placeholders dropped
    alert = mon["alert"]
    assert "0.99" not in alert["alert"] and str(t["month"]) in alert["alert"]
    assert alert["recommended_actions"] == ["Re-validate."]
    assert len(alert["dropped_sentences"]) == 2
    # re-validation ran on the breaching batch and Gate 4 is open again
    rv = mon["revalidation"]
    assert rv["status"] == "pending" and rv["month"] == t["month"]
    assert card["stages"]["validate"]["status"] == "awaiting_review"
    val = card["validation"]
    assert val["trigger"].startswith("Re-validation triggered by monitoring")
    assert f"_revalidation_m{t['month']:02d}.html" in val["report_html"]
    assert "RE-VALIDATION" in val["data"]
    model_card.validate(card)
    events = (REPO_ROOT / cfg["paths"]["audit"] / "monitoring_events.jsonl").read_text()
    assert "revalidation_triggered" in events
    # idempotent: a second run does not re-trigger or re-validate
    n_calls = llm.backend.calls
    out2 = monitor.run(card, ctx)
    assert "already triggered" in out2["action"] and llm.backend.calls == n_calls


@pytest.mark.skipif(not (DATA / "german_credit_test.csv").exists(), reason="needs make data")
def test_no_drift_no_trigger(cfg):
    cfg["monitor"]["drift"].update(amount_inflation_per_month=0, long_term_share_per_month=0,
                                   risk_tilt_per_month=0)
    card = _approved_card(cfg)
    llm = LLMClient(cfg, backend=FakeBackend(fake_alert), log_to_mlflow=False)
    ctx = orchestrator.Context(cfg, orchestrator.MODELS_DIR / "credit_scorecard", _llm=llm)
    out = monitor.run(card, ctx)
    assert out["trigger"] is None and llm.backend.calls == 0
    assert card["stages"]["validate"]["status"] == "approved"


def test_monitor_requires_approved_gate4(cfg):
    card = json.loads(CARD.read_text())
    card["stages"]["validate"].update(status="awaiting_review",
                                      completed_at=model_card.now_iso())
    card.pop("monitoring", None)
    ctx = orchestrator.Context(cfg, orchestrator.MODELS_DIR / "credit_scorecard")
    with pytest.raises(validate.AgentError, match="approved Gate 4"):
        monitor.run(card, ctx)
