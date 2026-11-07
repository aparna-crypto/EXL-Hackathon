import json
from datetime import date

import pytest

from factory import discover, gates, model_card, orchestrator
from factory.agents import AgentError
from factory.agents.inventory import check_answer
from factory.config import REPO_ROOT
from factory.llm import FakeBackend, LLMClient
from factory.scanner import scan_model

MODELS = REPO_ROOT / "models"
SC_R = "models/credit_scorecard/scorecard.R"
SC_NOTE = "models/credit_scorecard/methodology_note_2019.txt"


def scan(name):
    return scan_model(MODELS / name, REPO_ROOT)


# ---- scanner ---------------------------------------------------------------------------------

def test_scan_r_scorecard():
    s = scan("credit_scorecard")
    assert s["languages"] == ["R"] and s["libraries"] == ["jsonlite"] and s["seeds"] == ["42"]
    assert s["inputs"] == ["data/processed/german_credit_test.csv",
                           "data/processed/german_credit_train.csv"]
    assert "artifacts/reference/credit_scorecard/coefficients.csv" in s["outputs"]
    assert {"logistic regression", "weight of evidence (WoE) binning"} <= set(s["methods"])
    assert "offset term" not in s["methods"]
    assert s["files"][0]["header_fields"]["Owner"] == "Retail Credit Risk Modelling"
    assert [d["path"] for d in s["doc_files"]] == [SC_NOTE]


def test_scan_python_models():
    f = scan("fraud_gbm")
    assert f["libraries"] == ["numpy", "pandas", "sklearn"] and f["doc_files"] == []
    assert f["inputs"] == ["data/processed/creditcard_test.parquet",
                           "data/processed/creditcard_train.parquet"]
    assert f["methods"] == ["gradient boosting"]
    p = scan("pricing_glm")
    assert "statsmodels" in p["libraries"] and "Poisson GLM" in p["methods"]
    assert "artifacts/reference/pricing_glm/coefficients.csv" in p["outputs"]


def test_scan_sas():
    s = scan("sas_behaviour_scorecard")
    f = s["files"][0]
    assert s["inputs"] == ["crdw.account_month", "crdw.bureau_monthly", "crdw.delinquency_flags"]
    assert {"model.bhv_cc_01_scored", "/exports/bhv_cc_01_scores.csv"} <= set(s["outputs"])
    assert f["intermediate"] == ["work.base", "work.base_binned"]
    assert f["librefs"]["crdw"] == "/data/warehouse/cards"
    assert "PROC LOGISTIC" in s["libraries"]
    assert "stepwise variable selection" in s["methods"]


def test_scan_rejects_folder_without_code(tmp_path):
    (tmp_path / "m").mkdir()
    with pytest.raises(FileNotFoundError):
        scan_model(tmp_path / "m", tmp_path)


# ---- grounding checks ------------------------------------------------------------------------

def texts_for(name):
    s = scan(name)
    paths = [f["path"] for f in s["files"]] + [d["path"] for d in s["doc_files"]]
    return {p: (REPO_ROOT / p).read_text() for p in paths}


def field(value, evidence, source=SC_R):
    return {"value": value, "evidence": evidence, "source": source}


def test_check_answer_keeps_grounded_and_drops_fabricated():
    raw = {
        "owner": field("Retail Credit Risk Modelling", "Owner      : Retail Credit Risk Modelling"),
        "model_ref": field("RCS-APP-99", "Retail Application Scorecard  (RCS-APP-02)"),
        "purpose": field("PD for loans", "Probability of default for every product in the bank"),
        "last_validation_date": field("2021-03-15", "Last validated: 2021-03-15"),
        "risk_tier": {**field("high", "Probability of default (12m)"), "rationale": "credit"},
        "documentation_findings": [
            {"issue": "PDO", "doc_quote": "with 25 points to double the odds",
             "doc_source": SC_NOTE, "code_quote": "PDO         <- 20", "code_source": SC_R},
            {"issue": "made up", "doc_quote": "uses a neural network", "doc_source": SC_NOTE,
             "code_quote": "PDO <- 20", "code_source": SC_R},
        ],
        "unknowns": ["approval committee"],
    }
    out = check_answer(raw, texts_for("credit_scorecard"))
    f = out["fields"]
    assert f["owner"] == "Retail Credit Risk Modelling" and f["risk_tier"] == "high"
    assert f["last_validation_date"] == "2021-03-15"
    assert f["model_ref"] is None and f["purpose"] is None  # value not in quote / quote invented
    assert {r["field"] for r in out["rejected"]} == {"model_ref", "purpose", "documentation_finding"}
    assert [x["issue"] for x in out["findings"]] == ["PDO"]
    assert out["evidence"]["risk_tier"]["rationale"] == "credit"


def test_check_answer_rejects_bad_dates_and_tiers():
    raw = {"last_validation_date": field("March 2021", "Last validated: 2021-03-15"),
           "risk_tier": field("critical", "Probability of default (12m)")}
    out = check_answer(raw, texts_for("credit_scorecard"))
    assert out["fields"]["last_validation_date"] is None and out["fields"]["risk_tier"] is None


# ---- stage -----------------------------------------------------------------------------------

def make_ctx(cfg, name, responder):
    backend = FakeBackend(responder)
    llm = LLMClient(cfg, backend=backend, log_to_mlflow=False)
    card = orchestrator.load_or_init_card(cfg, name, MODELS)
    return card, orchestrator.Context(cfg, MODELS / name, _llm=llm), backend


def scorecard_answer(model, system, prompt):
    return "```json\n" + json.dumps({
        "name": field("Retail Application Scorecard", "Retail Application Scorecard"),
        "model_ref": field("RCS-APP-02", "Retail Application Scorecard  (RCS-APP-02)"),
        "owner": field("Retail Credit Risk Modelling", "Owner      : Retail Credit Risk Modelling"),
        "purpose": field("12m PD for unsecured loan applications",
                         "Probability of default (12m) for unsecured personal loan applications"),
        "model_type": field("application PD scorecard", "weight-of-evidence (WoE) binning"),
        "last_validation_date": field("2021-03-15", "Last validated: 2021-03-15"),
        "documentation_date": field("2019-06", "version 1.0, June 2019", SC_NOTE),
        "risk_tier": {**field("high", "unsecured personal loan applications"),
                      "rationale": "drives credit decisions"},
        "documentation_findings": [
            {"issue": "Note says 25 points to double odds; code uses PDO 20",
             "doc_quote": "with 25 points to double the odds", "doc_source": SC_NOTE,
             "code_quote": "PDO         <- 20", "code_source": SC_R}],
        "unknowns": [],
    }) + "\n```"


def test_discover_stage_scorecard(cfg):
    cfg["discover"]["as_of"] = "2026-11-07"
    card, ctx, backend = make_ctx(cfg, "credit_scorecard", scorecard_answer)
    out = discover.run_stage(card, ctx)
    model_card.validate(card)
    inv = card["inventory"]
    assert card["name"] == "Retail Application Scorecard" and inv["model_ref"] == "RCS-APP-02"
    assert inv["risk_tier"] == "high" and inv["documentation_status"] == "outdated"
    assert inv["data_sources"] == ["data/processed/german_credit_test.csv",
                                   "data/processed/german_credit_train.csv"]
    gaps = " ".join(card["gaps"])
    assert "contradicts code" in gaps and "Validation overdue" in gaps and "2019-06" in gaps
    assert "RCS-APP-02" in out["summary"] and backend.calls == 1
    inv_dir = cfg["paths"]["inventory"]
    md = open(f"{inv_dir}/inventory.md").read()
    assert "RCS-APP-02" in md and "flowchart LR" in md
    dot = open(f"{inv_dir}/dependency_map.dot").read()
    assert '"model:credit_scorecard"' in dot and "source:german_credit" in dot


def test_discover_stage_with_no_answers_flags_gaps(cfg):
    empty = json.dumps({"unknowns": ["owner", "purpose"], "documentation_findings": []})
    card, ctx, _ = make_ctx(cfg, "fraud_gbm", lambda *a: empty)
    discover.run_stage(card, ctx)
    assert card["inventory"]["documentation_status"] == "missing"
    gaps = " ".join(card["gaps"])
    for text in ("Documentation missing", "No validation record", "No owner", "No risk tier"):
        assert text in gaps


def test_sas_is_flagged_not_executable(cfg):
    empty = json.dumps({"documentation_findings": []})
    card, ctx, _ = make_ctx(cfg, "sas_behaviour_scorecard", lambda *a: empty)
    discover.run_stage(card, ctx)
    assert any("Not executable here" in g for g in card["gaps"])


def test_invalid_json_retries_once_then_fails(cfg):
    replies = iter(["not json", json.dumps({"documentation_findings": []})])
    card, ctx, backend = make_ctx(cfg, "fraud_gbm", lambda *a: next(replies))
    discover.run_stage(card, ctx)
    assert backend.calls == 2 and card["discovery"]["llm"]["calls"] == 2
    card, ctx, _ = make_ctx(cfg, "pricing_glm", lambda *a: "still not json")
    with pytest.raises(AgentError):
        discover.run_stage(card, ctx)


def test_orchestrator_reports_agent_failure(cfg, monkeypatch):
    monkeypatch.setattr(orchestrator.Context, "llm", property(
        lambda self: LLMClient(self.cfg, backend=FakeBackend(lambda *a: "no"),
                               log_to_mlflow=False)))
    out = orchestrator.run("fraud_gbm", cfg, use_mlflow=False)
    assert out["status"] == "failed" and out["stage"] == "discover"


def test_rejection_feedback_reaches_the_agent(cfg):
    prompts = []

    def responder(model, system, prompt):
        prompts.append(prompt)
        return json.dumps({"documentation_findings": []})

    card, ctx, _ = make_ctx(cfg, "fraud_gbm", responder)
    card["stages"]["discover"]["completed_at"] = model_card.now_iso()
    gates.record_decision(cfg["paths"]["audit"], "fraud_gbm", "discover", "reject", "rev",
                          "Owner is Cards Fraud Analytics per the model committee")
    discover.run_stage(card, ctx)
    assert "Cards Fraud Analytics" in prompts[0]
    assert card["discovery"]["reviewer_feedback_used"].startswith("- reject by rev")


def test_months_between():
    assert discover._months_between("2021-03-15", date(2026, 11, 7)) == 68
    assert discover._months_between("2024", date(2026, 11, 7)) == 34


def test_prompt_is_stable_across_runs(cfg):
    prompts = []

    def responder(model, system, prompt):
        prompts.append(prompt)
        return json.dumps({"documentation_findings": []})

    for _ in range(2):
        card, ctx, _ = make_ctx(cfg, "pricing_glm", responder)
        discover.run_stage(card, ctx)
    # second run is served from the response cache, so the backend sees one prompt only
    assert len(prompts) == 1
