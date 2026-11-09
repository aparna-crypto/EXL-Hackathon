"""Stage 8: offline checks of the Azure files. Nothing here contacts Azure."""

import os
import shutil
import subprocess

import pytest

from factory.config import REPO_ROOT

INFRA = REPO_ROOT / "infra"
BICEP = (INFRA / "main.bicep").read_text()


@pytest.mark.parametrize("rtype", [
    "Microsoft.Storage/storageAccounts", "Microsoft.KeyVault/vaults",
    "Microsoft.OperationalInsights/workspaces", "Microsoft.Insights/components",
    "Microsoft.ContainerRegistry/registries", "Microsoft.MachineLearningServices/workspaces",
    "Microsoft.App/managedEnvironments", "Microsoft.App/containerApps",
    "Microsoft.Consumption/budgets",
])
def test_required_resources_present(rtype):
    assert f"'{rtype}@" in BICEP


def test_brief_constraints():
    assert "targetScope = 'resourceGroup'" in BICEP
    assert "scale: { minReplicas: 0, maxReplicas: 1 }" in BICEP
    assert "sku: { name: 'Basic' }" in BICEP               # ACR Basic
    assert "adminUserEnabled: false" in BICEP              # pulls via managed identity
    assert "param budgetAmount int = 10" in BICEP


def test_no_secrets_in_image():
    ignore = (REPO_ROOT / ".dockerignore").read_text().splitlines()
    assert ".env" in ignore and "data/" in ignore
    docker = (REPO_ROOT / "Dockerfile").read_text()
    assert "ANTHROPIC" not in docker and "USER app" in docker


@pytest.mark.parametrize("script", ["deploy.sh", "teardown.sh"])
def test_scripts_strict_and_executable(script):
    path = INFRA / script
    text = path.read_text()
    assert os.access(path, os.X_OK)
    assert "set -euo pipefail" in text
    assert "--password" not in text and "client-secret" not in text  # user runs `az login` themselves


def test_teardown_deletes_group_and_purges():
    text = (INFRA / "teardown.sh").read_text()
    assert "az group delete" in text and "az keyvault purge" in text and "forceToPurge" in text


@pytest.mark.skipif(not shutil.which("bicep"), reason="bicep CLI not installed")
def test_bicep_builds():
    subprocess.run(["bicep", "build", str(INFRA / "main.bicep"), "--stdout"],
                   check=True, capture_output=True)


@pytest.mark.skipif(not shutil.which("shellcheck"), reason="shellcheck not installed")
def test_shellcheck():
    subprocess.run(["shellcheck", str(INFRA / "deploy.sh"), str(INFRA / "teardown.sh")], check=True)
