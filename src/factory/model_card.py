"""Model card: the shared, versioned JSON record passed between pipeline stages."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import jsonschema

from factory.config import REPO_ROOT

SCHEMA_VERSION = "1.0.0"
SCHEMA_PATH = REPO_ROOT / "schemas" / "model_card.schema.json"


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


def _schema() -> dict:
    return json.loads(SCHEMA_PATH.read_text())


def new_card(model_id: str, name: str, language: str, source_files: list[str],
             stages: list[str]) -> dict:
    return {
        "schema_version": SCHEMA_VERSION,
        "model_id": model_id,
        "card_version": 0,  # becomes 1 on first save
        "name": name,
        "language": language,
        "source_files": source_files,
        "inventory": {
            "owner": None, "purpose": None, "model_type": None, "risk_tier": None,
            "data_sources": [], "last_validation_date": None, "documentation_status": None,
        },
        "gaps": [],
        "stages": {s: {"status": "not_started", "completed_at": None, "summary": None}
                   for s in stages},
        "history": [],
    }


def validate(card: dict) -> None:
    """Raise jsonschema.ValidationError if the card does not match the schema."""
    jsonschema.validate(card, _schema())


def card_path(cards_dir: str | Path, model_id: str) -> Path:
    return Path(cards_dir) / f"{model_id}.json"


def save(card: dict, cards_dir: str | Path, note: str) -> dict:
    """Bump card_version, append a history entry, validate and write. Returns the card."""
    card["card_version"] += 1
    card["history"].append({"card_version": card["card_version"], "timestamp": now_iso(),
                            "note": note})
    validate(card)
    path = card_path(cards_dir, card["model_id"])
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(card, indent=2))
    return card


def load(cards_dir: str | Path, model_id: str) -> dict:
    card = json.loads(card_path(cards_dir, model_id).read_text())
    validate(card)
    return card


def exists(cards_dir: str | Path, model_id: str) -> bool:
    return card_path(cards_dir, model_id).exists()


def list_cards(cards_dir: str | Path) -> list[dict]:
    return [json.loads(p.read_text()) for p in sorted(Path(cards_dir).glob("*.json"))]
