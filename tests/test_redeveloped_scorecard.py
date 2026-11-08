"""The committed Python rebuild must keep matching the R original (re-checked on every test run)."""

from factory import redevelop
from factory.config import REPO_ROOT, load_config

MODULE = REPO_ROOT / "redeveloped/credit_scorecard/scorecard.py"
R_PATH = REPO_ROOT / "models/credit_scorecard/scorecard.R"


def test_rebuild_matches_r_oracle_and_reference(tmp_path):
    rcfg = load_config()["redevelop"]
    oracle = redevelop.run_oracle(REPO_ROOT, R_PATH, tmp_path, 120)
    data = REPO_ROOT / "data" / "processed"
    splits = [("original", REPO_ROOT / "artifacts/reference/credit_scorecard", data)]
    att = tmp_path / "attempt"
    att.mkdir()
    res = redevelop.evaluate(REPO_ROOT, MODULE, att, oracle, splits, rcfg)
    assert res["failures"] == []
    assert len(res["unit"]) == len(redevelop.CASES) and res["passed"]
