"""Orchestrator: runs the specialised agents stage by stage, with a human gate after each.

The run is resumable. Re-running `make run MODEL=x` skips approved stages, re-asks for pending
gates, and re-executes a stage whose latest gate decision was a rejection.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

import mlflow

from factory import discover, document, gates, model_card, redevelop, tracking, validate
from factory.agents import AgentError
from factory.config import REPO_ROOT, load_config
from factory.llm import BudgetExceeded, LLMClient, LLMError

MODELS_DIR = REPO_ROOT / "models"
LANGUAGE_BY_EXT = {".r": "R", ".py": "Python", ".sas": "SAS"}

# A decision function returns (decision, reviewer, reason), or None to defer to the UI.
Decide = Callable[[dict, str, str], "tuple[str, str, str] | None"]
# A stage function updates the card in place and returns {"summary": str, "applicable": bool}.
StageFn = Callable[[dict, "Context"], dict]


@dataclass
class Context:
    cfg: dict
    model_dir: Path
    run_id: str | None = None
    _llm: LLMClient | None = field(default=None, repr=False)

    @property
    def llm(self) -> LLMClient:
        if self._llm is None:
            self._llm = LLMClient(self.cfg, run_id=self.run_id)
        return self._llm


def _not_implemented(card: dict, ctx: Context) -> dict:
    raise NotImplementedError


# Filled in as stages are built (Stage 2: discover, 3: document, 4: redevelop, 5: validate).
STAGE_FUNCS: dict[str, StageFn] = {"discover": discover.run_stage,
                                   "document": document.run_stage,
                                   "redevelop": redevelop.run_stage,
                                   "validate": validate.run_stage}


def find_source_files(model_dir: Path) -> tuple[str, list[str]]:
    """Return (language, source files relative to the repo) for a model folder."""
    files = sorted(p for p in model_dir.rglob("*") if p.suffix.lower() in LANGUAGE_BY_EXT)
    if not files:
        raise FileNotFoundError(f"no .R/.py/.sas files in {model_dir}")
    languages = {LANGUAGE_BY_EXT[p.suffix.lower()] for p in files}
    language = languages.pop() if len(languages) == 1 else "Python"
    root = model_dir.parent.parent
    return language, [str(p.relative_to(root)) for p in files]


def load_or_init_card(cfg: dict, model_id: str, models_dir: Path) -> dict:
    cards_dir = cfg["paths"]["model_cards"]
    if model_card.exists(cards_dir, model_id):
        return model_card.load(cards_dir, model_id)
    model_dir = models_dir / model_id
    if not model_dir.is_dir():
        raise FileNotFoundError(f"unknown model '{model_id}': expected folder {model_dir}")
    language, files = find_source_files(model_dir)
    card = model_card.new_card(model_id, model_id, language, files, cfg["gates"]["stages"])
    return model_card.save(card, cards_dir, "card created")


def run(model_id: str, cfg: dict | None = None, stage_funcs: dict[str, StageFn] | None = None,
        decide: Decide | None = None, models_dir: Path | None = None,
        use_mlflow: bool = True) -> dict:
    """Run the pipeline for one model. Returns {"status": ..., "stage": ..., "message": ...}."""
    cfg = cfg or load_config()
    stage_funcs = STAGE_FUNCS if stage_funcs is None else stage_funcs
    models_dir = models_dir or MODELS_DIR
    if not (models_dir / model_id).is_dir() and not model_card.exists(
            cfg["paths"]["model_cards"], model_id):
        raise FileNotFoundError(f"unknown model '{model_id}': expected folder {models_dir / model_id}")
    if use_mlflow:
        tracking.configure(cfg)
        with mlflow.start_run(run_name=f"pipeline:{model_id}") as active:
            mlflow.set_tags({"kind": "pipeline", "model_id": model_id})
            return _run(model_id, cfg, stage_funcs, decide, models_dir, active.info.run_id, True)
    return _run(model_id, cfg, stage_funcs, decide, models_dir, None, False)


def _run(model_id, cfg, stage_funcs, decide, models_dir, run_id, use_mlflow) -> dict:
    cards_dir, audit_dir = cfg["paths"]["model_cards"], cfg["paths"]["audit"]
    reason_required = tuple(cfg["gates"]["reason_required_for"])
    card = load_or_init_card(cfg, model_id, models_dir)
    ctx = Context(cfg, models_dir / model_id, run_id)

    for stage in cfg["gates"]["stages"]:
        entry = card["stages"][stage]
        if entry["status"] in ("approved", "not_applicable"):
            continue

        gate = (gates.status(audit_dir, model_id, stage, since=entry["completed_at"])
                if entry["completed_at"] else None)

        if gate is None or gate == "rejected":
            fn = stage_funcs.get(stage, _not_implemented)
            try:
                out = fn(card, ctx)
            except NotImplementedError:
                return {"status": "not_implemented", "stage": stage,
                        "message": f"stage '{stage}' is not implemented yet"}
            except BudgetExceeded as e:
                return {"status": "budget_exceeded", "stage": stage, "message": str(e)}
            except (AgentError, LLMError) as e:
                return {"status": "failed", "stage": stage, "message": str(e)}
            if out.get("applicable") is False:
                entry.update(status="not_applicable", summary=out.get("summary"))
                model_card.save(card, cards_dir, f"{stage}: not applicable")
                continue
            entry.update(status="awaiting_review", completed_at=model_card.now_iso(),
                         summary=out["summary"])
            model_card.save(card, cards_dir, f"{stage}: completed, awaiting review")
            gate = "pending"

        while gate == "pending":
            answer = decide(card, stage, entry["summary"]) if decide else None
            if answer is None:
                return {"status": "awaiting_review", "stage": stage,
                        "message": f"gate '{stage}' is waiting for a reviewer (UI or CLI)"}
            decision, reviewer, reason = answer
            rec = gates.record_decision(audit_dir, model_id, stage, decision, reviewer, reason,
                                        card["card_version"], reason_required)
            if use_mlflow:
                tracking.log_gate_decision(rec)
            if decision != "comment":
                gate = "approved" if decision == "approve" else "rejected"

        entry["status"] = gate
        model_card.save(card, cards_dir, f"{stage}: gate {gate}")
        if gate == "rejected":
            return {"status": "rejected", "stage": stage,
                    "message": f"gate '{stage}' rejected; fix and re-run to redo the stage"}

    return {"status": "complete", "stage": None, "message": "all stages approved"}
