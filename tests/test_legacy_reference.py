"""Stage 1: reference outputs of the legacy models are present, internally consistent and
reproducible. These outputs are the ground truth for the parity proof (Stage 4)."""

import json
import math
import shutil
import subprocess

import numpy as np
import pandas as pd
import pytest
from sklearn.metrics import roc_auc_score

from factory.config import REPO_ROOT

REF = REPO_ROOT / "artifacts" / "reference"
PROCESSED = REPO_ROOT / "data" / "processed"
PROTECTED = {"personal_status_sex", "sex", "age_group", "foreign_worker"}


def metrics(model):
    return json.loads((REF / model / "metrics.json").read_text())


def test_credit_scorecard_reference_consistent():
    m = metrics("credit_scorecard")
    preds = pd.read_csv(REF / "credit_scorecard" / "predictions_test.csv")
    coefs = pd.read_csv(REF / "credit_scorecard" / "coefficients.csv")
    assert len(preds) == m["metrics"]["test"]["n"] == 300
    assert roc_auc_score(preds["bad"], preds["pd"]) == pytest.approx(m["metrics"]["test"]["auc"],
                                                                     abs=1e-9)
    factor = m["pdo"] / math.log(2)
    offset = m["base_score"] - factor * math.log(m["base_odds"])
    expected = offset + factor * np.log((1 - preds["pd"]) / preds["pd"])
    assert np.allclose(preds["score"], expected, atol=1e-8)
    assert len(coefs) == len(m["selected_variables"]) + 1
    assert not PROTECTED & set(m["selected_variables"])


def test_fraud_reference_consistent():
    m = metrics("fraud_gbm")
    preds = pd.read_csv(REF / "fraud_gbm" / "predictions_test.csv.gz")
    assert len(preds) == m["metrics"]["test"]["n"]
    assert roc_auc_score(preds["Class"], preds["p_fraud"]) == pytest.approx(
        m["metrics"]["test"]["auc"], abs=1e-6)


def test_pricing_reference_consistent():
    m = metrics("pricing_glm")
    preds = pd.read_csv(REF / "pricing_glm" / "predictions_test.csv.gz")
    t = m["metrics"]["test"]
    assert len(preds) == t["n"]
    assert preds["ClaimNb"].sum() == t["observed_claims"]
    assert preds["expected_claims"].sum() == pytest.approx(t["predicted_claims"], rel=1e-6)
    # Poisson GLM with intercept: predicted claims equal observed claims on the training data
    assert m["metrics"]["train"]["predicted_claims"] == pytest.approx(
        m["metrics"]["train"]["observed_claims"], rel=1e-6)


def test_sas_model_has_no_fabricated_outputs():
    assert (REPO_ROOT / "models" / "sas_behaviour_scorecard" / "behaviour_score.sas").exists()
    assert not (REF / "sas_behaviour_scorecard").exists()


needs_german = pytest.mark.skipif(
    not (PROCESSED / "german_credit_train.csv").exists() or shutil.which("Rscript") is None,
    reason="run `make data` (german_credit) and install R")


@needs_german
def test_german_split_is_fixed():
    train = pd.read_csv(PROCESSED / "german_credit_train.csv")
    test = pd.read_csv(PROCESSED / "german_credit_test.csv")
    assert (len(train), len(test)) == (700, 300)
    assert not set(train["id"]) & set(test["id"])
    assert train["bad"].mean() == pytest.approx(0.3) and test["bad"].mean() == pytest.approx(0.3)
    ref_ids = pd.read_csv(REF / "credit_scorecard" / "predictions_test.csv")["id"]
    assert list(test["id"]) == list(ref_ids)


@needs_german
def test_r_scorecard_rerun_reproduces_reference(tmp_path):
    subprocess.run(["Rscript", "models/credit_scorecard/scorecard.R", str(PROCESSED), str(tmp_path)],
                   cwd=REPO_ROOT, check=True, capture_output=True)
    for name in ["coefficients.csv", "predictions_test.csv", "bins.csv"]:
        assert (tmp_path / name).read_text() == (REF / "credit_scorecard" / name).read_text(), name
