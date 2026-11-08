import json

import pytest

from factory import model_card, orchestrator, redevelop
from factory.agents import AgentError
from factory.agents import translation as tr
from factory.config import REPO_ROOT, load_config
from factory.llm import FakeBackend, LLMClient

GOOD = (REPO_ROOT / "redeveloped/credit_scorecard/scorecard.py").read_text()
ALLOWED = load_config()["redevelop"]["allowed_imports"]


def block(code):
    return f"```python\n{code}```"


def test_extract_code():
    assert tr.extract_code("text\n```python\nx = 1\n```\nmore") == "x = 1\n"
    with pytest.raises(ValueError):
        tr.extract_code("no code here")


def test_static_check_flags_imports_calls_and_interface():
    assert tr.static_check(GOOD, ALLOWED) == []
    issues = tr.static_check("import os\nimport subprocess\neval('1')\n", ALLOWED)
    assert "import not allowed: os" in issues and "import not allowed: subprocess" in issues
    assert "call not allowed: eval()" in issues
    assert any(i.startswith("missing from module interface") for i in issues)
    assert tr.static_check("def f(:\n", ALLOWED)[0].startswith("syntax error")


def test_same_tolerance_records_and_types():
    assert redevelop.same([1, "Inf", 0.5], [1.0, float("inf"), 0.5 + 1e-12], 1e-9)
    assert not redevelop.same([0.5], [0.5 + 1e-6], 1e-9)
    assert not redevelop.same(True, 1, 1e-9)
    assert not redevelop.same("(30, Inf]", "(30,Inf]", 1e-9)
    rows_r = [{"bin": "b", "woe": 1.0, "_row": "b"}, {"bin": "a", "woe": 2.0}]
    assert redevelop.same(rows_r, [{"bin": "a", "woe": 2.0}, {"bin": "b", "woe": 1.0}], 1e-9)


def test_build_prompt_contains_contract_and_feedback():
    p = tr.build_prompt("models/x.R", "x <- 1", {"bins.csv": "a,b"}, [], ALLOWED, "fix labels")
    assert "assign_bin" in p
    assert "fix labels" in p and "x <- 1" in p and "statsmodels GLM" in p


def ctx_for(cfg, responder, model="credit_scorecard", language="R"):
    files = [f"models/{model}/scorecard.R"] if language == "R" else ["models/fraud_gbm/x.py"]
    card = model_card.new_card(model, model, language, files, cfg["gates"]["stages"])
    card["card_version"] = 1
    llm = LLMClient(cfg, backend=FakeBackend(responder), log_to_mlflow=False)
    return card, orchestrator.Context(cfg, orchestrator.MODELS_DIR / model, _llm=llm)


def test_not_applicable_for_python_models(cfg):
    card, ctx = ctx_for(cfg, lambda *a: "", "fraud_gbm", "Python")
    assert redevelop.run_stage(card, ctx)["applicable"] is False


def test_self_healing_escalates_and_proves_parity(cfg):
    cfg["llm"]["max_retries_before_escalation"] = 1
    broken = GOOD.replace('return " Inf"', 'return "Inf"')
    assert broken != GOOD
    prompts = []

    def responder(model, system, prompt):
        prompts.append(prompt)
        return block(GOOD if "opus" in model else broken)

    card, ctx = ctx_for(cfg, responder)
    out = redevelop.run_stage(card, ctx)
    red = card["redevelopment"]
    assert [a["model"] for a in red["attempts"]] == ["claude-sonnet-5-5", "claude-opus-5-5"]
    assert [a["passed"] for a in red["attempts"]] == [False, True]
    assert red["attempts"][1]["escalated"] is True
    assert "(30, Inf]" in prompts[1] and "Attempt 1 failed" in prompts[1]
    assert red["unit_tests"]["passed"] == red["unit_tests"]["total"] == len(redevelop.CASES)
    assert red["parity"]["passed"] == red["parity"]["total"] > 30
    assert (cfg["paths"]["redeveloped"] + "/credit_scorecard/scorecard.py") == red["target"]
    assert open(red["target"]).read() == GOOD
    assert "PARITY PROVEN" in open(cfg["paths"]["parity_report"]).read()
    assert "Parity checks" in out["summary"]


def test_gives_up_after_max_attempts(cfg):
    cfg["redevelop"]["max_attempts"] = 2
    cfg["llm"]["max_retries_before_escalation"] = 1
    card, ctx = ctx_for(cfg, lambda *a: block("import os\n"))
    with pytest.raises(AgentError, match="no parity after 2 attempts"):
        redevelop.run_stage(card, ctx)
    attempts = json.loads(open(cfg["paths"]["redevelop_artifacts"]
                               + "/credit_scorecard/attempts.json").read())
    assert len(attempts) == 2 and attempts[0]["failures"][0].startswith("static check")
    assert "PARITY NOT ACHIEVED" in open(cfg["paths"]["parity_report"]).read()
