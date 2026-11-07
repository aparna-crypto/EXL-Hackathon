import json

from test_discover import MODELS, SC_NOTE, SC_R, make_ctx, scorecard_answer

from factory import document, gates, model_card, orchestrator
from factory.agents.documentation import check_answer
from factory.config import REPO_ROOT
from factory.llm import FakeBackend, LLMClient

SC_METRICS = "artifacts/reference/credit_scorecard/metrics.json"
SELECTED = json.loads((REPO_ROOT / SC_METRICS).read_text())["selected_variables"]


def ev(quote, source=SC_R):
    return {"quote": quote, "source": source}


def doc_answer(model, system, prompt):
    variables = [{"name": v, "role": "input", "description": v, "transformation": "WoE",
                  "evidence": f'"{v}"', "source": SC_METRICS} for v in SELECTED if v != "job"]
    variables += [
        {"name": "sex", "role": "protected", "description": "excluded", "transformation": None,
         "evidence": "sex", "source": SC_R},
        {"name": "income", "role": "input", "description": "made up", "transformation": None,
         "evidence": "income band from bureau", "source": SC_R}]
    return json.dumps({
        "sections": {
            "purpose_and_use": {"text": "12m PD for loan applications", "confidence": "high",
                                "evidence": [ev("unsecured personal loan applications")]},
            "data": {"text": "German Credit", "confidence": "high", "evidence": []},
            "methodology": {"text": "WoE + logistic regression, PDO 20", "confidence": "high",
                            "evidence": [ev("PDO         <- 20"), ev("uses XGBoost")]},
            "implementation": {"text": "R, seed 42", "confidence": "high",
                               "evidence": [ev('"seed": 42', SC_METRICS)]},
            "performance": {"text": "see metrics", "confidence": "medium",
                            "evidence": [ev('"pdo": 20', SC_METRICS)]},
            "monitoring_and_governance": {"text": "Validated 2021", "confidence": "high",
                                          "evidence": [ev("Last validated: 2021-03-15")]},
        },
        "variables": variables,
        "transformations": [
            {"step": "scaling", "description": "PDO 20", "evidence": "PDO         <- 20",
             "source": SC_R},
            {"step": "capping", "description": "invented", "evidence": "pmin(income, 1e5)",
             "source": SC_R}],
        "assumptions": [{"text": "PDO 20", "confidence": "high", "evidence": "PDO         <- 20",
                         "source": SC_R},
                        {"text": "population stable", "confidence": "high", "evidence": "",
                         "source": SC_R}],
        "limitations": [{"text": "note is from 2019", "confidence": "high",
                         "evidence": "with 25 points to double the odds", "source": SC_NOTE}],
        "unknowns": ["approval body"],
    })


def responder(model, system, prompt):
    return (scorecard_answer if "inventory analyst" in system else doc_answer)(model, system,
                                                                            prompt)


def texts():
    return {p: (REPO_ROOT / p).read_text() for p in (SC_R, SC_NOTE, SC_METRICS)}


def test_check_answer_grounds_claims():
    out = check_answer(json.loads(doc_answer(None, "", "")), texts())
    s = out["sections"]
    assert s["purpose_and_use"]["confidence"] == "high"
    assert s["data"]["confidence"] == "low" and s["data"]["flag"] == "no supporting quote found"
    assert s["methodology"]["confidence"] == "medium" and len(s["methodology"]["evidence"]) == 1
    names = [v["name"] for v in out["variables"]]
    assert "income" not in names and "sex" in names and len(names) == len(SELECTED)
    assert [t["step"] for t in out["transformations"]] == ["scaling"]
    assert [a["grounded"] for a in out["assumptions"]] == [True, False]
    assert out["assumptions"][1]["confidence"] == "low"
    items = {r["item"] for r in out["rejected"]}
    assert {"variable:income", "transformation:capping", "section:methodology"} <= items


def test_model_terms_from_reference_outputs():
    assert document.model_terms(REPO_ROOT, "credit_scorecard") == sorted(SELECTED)
    assert set(document.model_terms(REPO_ROOT, "pricing_glm")) == {
        "VehPower", "VehAge", "DrivAge", "Area", "VehBrand", "VehGas", "Region", "BonusMalus",
        "LogDensity"}
    fraud = document.model_terms(REPO_ROOT, "fraud_gbm")
    assert len(fraud) == 30 and {"log_amount", "hour"} <= set(fraud)
    assert document.model_terms(REPO_ROOT, "sas_behaviour_scorecard") is None


def test_coverage_expands_numbered_ranges():
    terms = document.model_terms(REPO_ROOT, "fraud_gbm")
    cov = document.coverage(terms, [{"name": "V1-V28", "role": "input"},
                                    {"name": "log_amount", "role": "input"},
                                    {"name": "hour", "role": "input"},
                                    {"name": "Class", "role": "target"}])
    assert cov["missing"] == [] and cov["extra"] == [] and cov["documented"] == 30
    cov = document.coverage(terms, [{"name": "V1-V27", "role": "input"},
                                    {"name": "Time", "role": "input"}])
    assert set(cov["missing"]) == {"V28", "log_amount", "hour"} and cov["extra"] == ["Time"]
    assert document.coverage(None, [])["available"] is False


def test_document_stage_scorecard(cfg):
    card, ctx, backend = make_ctx(cfg, "credit_scorecard", responder)
    from factory import discover
    discover.run_stage(card, ctx)
    out = document.run_stage(card, ctx)
    model_card.validate(card)
    doc = card["documentation"]
    assert doc["coverage"]["missing"] == ["job"]
    assert any("not documented: job" in g for g in doc["gaps"])
    assert any("inferred without a supporting quote" in g for g in doc["gaps"])
    assert SC_METRICS in doc["sources"]
    md = open(doc["doc_path"]).read()
    assert "(P1)" in md and "Coverage check" in md and "Reviewer judgement needed" in md
    assert "RCS-APP-02" in md and "Agent claims dropped" in md
    assert "13/14 variables" in out["summary"] and backend.calls == 2


def test_document_runs_only_after_gate1_approval(cfg, monkeypatch):
    monkeypatch.setattr(orchestrator.Context, "llm", property(
        lambda self: LLMClient(self.cfg, backend=FakeBackend(responder), log_to_mlflow=False)))
    out = orchestrator.run("credit_scorecard", cfg, use_mlflow=False)
    assert out["status"] == "awaiting_review" and out["stage"] == "discover"
    assert not model_card.load(cfg["paths"]["model_cards"], "credit_scorecard").get(
        "documentation")
    gates.record_decision(cfg["paths"]["audit"], "credit_scorecard", "discover", "approve", "rev",
                          "")
    out = orchestrator.run("credit_scorecard", cfg, use_mlflow=False)
    assert out["status"] == "awaiting_review" and out["stage"] == "document"
    card = model_card.load(cfg["paths"]["model_cards"], "credit_scorecard")
    assert card["stages"]["discover"]["status"] == "approved"
    assert open(card["documentation"]["doc_path"]).read().startswith("# ")


def test_document_feedback_reaches_the_agent(cfg):
    prompts = []

    def recorder(model, system, prompt):
        prompts.append(prompt)
        return responder(model, system, prompt)

    card, ctx, _ = make_ctx(cfg, "credit_scorecard", recorder)
    from factory import discover
    discover.run_stage(card, ctx)
    card["stages"]["document"]["completed_at"] = model_card.now_iso()
    gates.record_decision(cfg["paths"]["audit"], "credit_scorecard", "document", "reject", "rev",
                          "Describe the WoE smoothing constant")
    document.run_stage(card, ctx)
    assert "WoE smoothing constant" in prompts[-1]
    assert card["documentation"]["reviewer_feedback_used"].startswith("- reject by rev")


def test_models_dir_constant():
    assert (MODELS / "credit_scorecard").is_dir()
