import json
import shutil
import subprocess

import pytest

from factory.config import REPO_ROOT


@pytest.mark.skipif(shutil.which("Rscript") is None, reason="R not installed")
def test_r_runs_and_returns_json():
    out = subprocess.run(["Rscript", "r/smoke_test.R"], cwd=REPO_ROOT, capture_output=True,
                         text=True, check=True).stdout
    result = json.loads(out)
    assert result["r_version"].startswith("4.")
    assert len(result["coef"]) == 2
