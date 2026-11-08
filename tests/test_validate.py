import json

import numpy as np
import pandas as pd
import pytest

from factory import model_card, orchestrator, validate
from factory.agents import report
from factory.config import REPO_ROOT
from factory.llm import FakeBackend, LLMClient

DATA = REPO_ROOT / "data" / "processed"


def test_psi_zero_for_identical_and_positive_for_shift():
    rng = np.random.default_rng(0)
    x = rng.normal(size=5000)
    assert validate.psi(x, x, 10)[0] == pytest.approx(0, abs=1e-12)
    assert validate.psi(x, x + 1, 10)[0] > 0.25
    assert validate.psi(pd.Series(list("aab")), pd.Series(list("abb")))[0] > 0


def test_hosmer_lemeshow_well_vs_badly_calibrated():
    rng = np.random.default_rng(3)
    p = rng.uniform(0.05, 0.6, 20000)
    y = rng.binomial(1, p)
    assert validate.hosmer_lemeshow(y, p, 10)[1] > 0.01
    assert validate.hosmer_lemeshow(y, np.clip(p * 1.5, 0, 0.99), 10)[1] < 1e-6


def test_poisson_deviance_and_lorenz_gini():
    y = np.array([0, 1, 2, 0])
    assert validate.poisson_deviance(y, y + 1e-12) == pytest.approx(0, abs=1e-9)
    assert validate.poisson_deviance(y, np.ones(4)) > 0
    expo = np.ones(4)
    perfect = validate.lorenz_gini(y, y + 0.1, expo)[0]
    reverse = validate.lorenz_gini(y, 3 - y, expo)[0]
    assert perfect > 0 > reverse


def test_rag_and_worst():
    assert validate.rag(0.80, [0.75, 0.65], True) == "green"
    assert validate.rag(0.70, [0.75, 0.65], True) == "amber"
    assert validate.rag(0.60, [0.75, 0.65], True) == "red"
    assert validate.rag(0.05, [0.10, 0.25], False) == "green"
    assert validate.rag(0.30, [0.10, 0.25], False) == "red"
    assert validate.worst(["green", "amber", "n/a"]) == "amber"
    assert validate.worst(["n/a"]) == "n/a"


def test_report_grounding_fills_placeholders_and_drops_typed_numbers():
    facts = {"performance.test_auc": "0.801"}
    text, dropped = report.ground(
        "AUC is [[performance.test_auc]]. AUC is 0.9. Rate is 12%. Unknown [[x.y]]. Fine.", facts)
    assert text == "AUC is 0.801. Fine."
    assert len(dropped) == 3


def test_not_applicable_for_sas(cfg):
    card = model_card.new_card("sas_behaviour_scorecard", "sas", "SAS", ["x.sas"],
                               cfg["gates"]["stages"])
    ctx = orchestrator.Context(cfg, orchestrator.MODELS_DIR / "sas_behaviour_scorecard")
    assert validate.run_stage(card, ctx)["applicable"] is False


def fake_report(model, system, prompt):
    return json.dumps({
        "executive_summary": "Test AUC is [[performance.test_auc]]. The AUC is 0.99.",
        "sections": {s: f"{s} narrative." for s in validate.SECTIONS},
        "findings": [{"severity": "medium", "section": "stability",
                      "finding": "Tranche PSI is [[stability.tranche_max_psi]].",
                      "recommendation": "Monitor."}],
        "limitations": ["Synthetic time split."]})


@pytest.mark.skipif(not (DATA / "german_credit_test.csv").exists(), reason="needs make data")
def test_scorecard_validation_end_to_end(cfg):
    card = json.loads((REPO_ROOT / "artifacts/model_cards/credit_scorecard.json").read_text())
    card.pop("monitoring", None)  # validate the original test set, not a monitoring batch
    calls = []

    def responder(model, system, prompt):
        calls.append(prompt)
        return fake_report(model, system, prompt)

    llm = LLMClient(cfg, backend=FakeBackend(responder), log_to_mlflow=False)
    ctx = orchestrator.Context(cfg, orchestrator.MODELS_DIR / "credit_scorecard", _llm=llm)
    out = validate.run_stage(card, ctx)
    val = card["validation"]
    model_card.validate(card)
    m = {c["metric"]: c for c in val["checks"] if c["metric"]}
    ref = json.loads((REPO_ROOT / "artifacts/reference/credit_scorecard/metrics.json").read_text())
    assert m["performance.test_auc"]["value"] == pytest.approx(ref["metrics"]["test"]["auc"],
                                                               abs=1e-12)
    assert {"sex_adverse_impact_ratio", "age_group_equalized_odds_difference"} <= {
        c["metric"].split(".", 1)[1] for c in val["checks"] if c["metric"]}
    assert any("too small" in c["check"] for c in val["checks"])  # foreign_worker A202 n=9
    assert val["overall_rag"] in ("green", "amber", "red")
    # numbers come from evidence, typed numbers are dropped, no data rows go to the LLM
    assert "0.99" not in json.dumps(val["findings"])
    assert val["narrative_dropped"] == ["The AUC is 0.99."]
    assert "checking_status,duration" not in calls[0] and "A11" not in calls[0]
    html = (REPO_ROOT / val["report_html"]) if not val["report_html"].startswith("/") else None
    html = html or __import__("pathlib").Path(val["report_html"])
    assert "Overall rating" in html.read_text()
    pdf = __import__("pathlib").Path(val["report_pdf"])
    assert pdf.read_bytes()[:4] == b"%PDF"
    assert "Overall:" in out["summary"]


@pytest.mark.skipif(not (DATA / "fremtpl2_test.parquet").exists(), reason="needs make data")
def test_pricing_evidence_matches_reference(cfg, tmp_path):
    ref = json.loads((REPO_ROOT / "artifacts/reference/pricing_glm/metrics.json").read_text())
    vcfg = cfg["validate"]
    ev = validate.Evidence(vcfg["thresholds"], tmp_path)
    validate.poisson_frequency({"model_id": "pricing_glm"}, REPO_ROOT, ev, vcfg, cfg["seed"])
    perf = ev.metrics["performance"]
    assert perf["test_actual_claims"] == ref["metrics"]["test"]["observed_claims"]
    assert perf["test_mean_poisson_deviance"] == pytest.approx(
        ref["metrics"]["test"]["mean_poisson_deviance"], rel=1e-6)
