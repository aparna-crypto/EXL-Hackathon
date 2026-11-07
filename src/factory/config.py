"""Load config/config.yaml and apply environment-variable overrides."""

from __future__ import annotations

import os
from pathlib import Path

import yaml
from dotenv import load_dotenv

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CONFIG = REPO_ROOT / "config" / "config.yaml"


def load_config(path: str | Path | None = None) -> dict:
    """Return the config dict. Secrets come only from the environment / .env, never from YAML."""
    load_dotenv(REPO_ROOT / ".env", override=False)
    with open(path or DEFAULT_CONFIG) as f:
        cfg = yaml.safe_load(f)

    if os.getenv("LLM_PROVIDER"):
        cfg["llm"]["provider"] = os.environ["LLM_PROVIDER"]
    if os.getenv("TRACKING_BACKEND"):
        cfg["tracking"]["backend"] = os.environ["TRACKING_BACKEND"]

    for key, rel in cfg["paths"].items():
        p = Path(rel)
        cfg["paths"][key] = str(p if p.is_absolute() else REPO_ROOT / p)
    return cfg
