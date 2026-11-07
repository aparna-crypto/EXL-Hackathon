"""Human stage gates. Every decision is appended to an audit log (JSONL) and never edited."""

from __future__ import annotations

import json
from pathlib import Path

from factory.model_card import now_iso

DECISIONS = ("approve", "reject", "comment")


class GateError(ValueError):
    pass


def _log_path(audit_dir: str | Path) -> Path:
    return Path(audit_dir) / "gate_decisions.jsonl"


def record_decision(audit_dir: str | Path, model_id: str, stage: str, decision: str,
                    reviewer: str, reason: str = "", card_version: int | None = None,
                    reason_required_for: tuple[str, ...] = ("reject", "comment")) -> dict:
    if decision not in DECISIONS:
        raise GateError(f"decision must be one of {DECISIONS}, got {decision!r}")
    if not reviewer.strip():
        raise GateError("reviewer name is required")
    if decision in reason_required_for and not reason.strip():
        raise GateError(f"a reason is required for '{decision}'")
    entry = {
        "timestamp": now_iso(),
        "model_id": model_id,
        "stage": stage,
        "decision": decision,
        "reviewer": reviewer.strip(),
        "reason": reason.strip(),
        "card_version": card_version,
    }
    path = _log_path(audit_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a") as f:
        f.write(json.dumps(entry) + "\n")
    return entry


def history(audit_dir: str | Path, model_id: str | None = None,
            stage: str | None = None) -> list[dict]:
    path = _log_path(audit_dir)
    if not path.exists():
        return []
    entries = [json.loads(line) for line in path.read_text().splitlines() if line.strip()]
    return [e for e in entries
            if (model_id is None or e["model_id"] == model_id)
            and (stage is None or e["stage"] == stage)]


def status(audit_dir: str | Path, model_id: str, stage: str, since: str | None = None) -> str:
    """'approved', 'rejected' or 'pending', from the latest approve/reject decision.

    Only decisions at or after `since` (the time the stage last finished running) count, so
    re-running a rejected stage resets it to pending. Comments never change the status.
    """
    decisions = [e for e in history(audit_dir, model_id, stage)
                 if e["decision"] in ("approve", "reject")
                 and (since is None or e["timestamp"] >= since)]
    if not decisions:
        return "pending"
    return "approved" if decisions[-1]["decision"] == "approve" else "rejected"
