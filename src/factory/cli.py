"""Command-line entry point: python -m factory.cli <command>."""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections import defaultdict
from pathlib import Path

from factory import discover, gates, model_card, orchestrator, tracking
from factory.config import load_config
from factory.llm import LLMClient


def prompt_decision(card: dict, stage: str, summary: str):
    print(f"\n=== Gate: {stage} | model: {card['model_id']} | card v{card['card_version']} ===")
    print(summary or "(no summary)")
    while True:
        try:
            decision = input("Decision [approve/reject/comment, blank = defer to UI]: ")
        except EOFError:  # non-interactive run: leave the decision to the UI
            print("\n(no terminal input: decide in the UI)")
            return None
        decision = decision.strip().lower()
        if not decision:
            return None
        if decision in gates.DECISIONS:
            break
        print("Please type approve, reject or comment.")
    reviewer = input(f"Reviewer [{os.getenv('REVIEWER', '')}]: ").strip() or os.getenv("REVIEWER", "")
    reason = input("Reason: ").strip()
    return decision, reviewer, reason


def cmd_run(args, cfg) -> int:
    decide = None if args.no_prompt else prompt_decision
    try:
        out = orchestrator.run(args.model, cfg, decide=decide)
    except FileNotFoundError as e:
        print(f"[error] {e}")
        return 2
    print(f"[{out['status']}] {out['message']}")
    return 0 if out["status"] in ("complete", "awaiting_review") else 1


def cmd_gate(args, cfg) -> int:
    card = model_card.load(cfg["paths"]["model_cards"], args.model)
    rec = gates.record_decision(cfg["paths"]["audit"], args.model, args.stage, args.decision,
                                args.reviewer, args.reason or "", card["card_version"],
                                tuple(cfg["gates"]["reason_required_for"]))
    tracking.configure(cfg)
    tracking.log_gate_decision(rec)
    print(json.dumps(rec, indent=2))
    return 0


def cmd_status(args, cfg) -> int:
    card = model_card.load(cfg["paths"]["model_cards"], args.model)
    for stage, e in card["stages"].items():
        print(f"{stage:<10} {e['status']:<16} {e['summary'] or ''}")
    return 0


def cmd_inventory(args, cfg) -> int:
    out = discover.write_inventory(cfg)
    print(f"inventory written to {out}/ (inventory.md, inventory.csv, dependency_map.dot/.mmd)")
    return 0


def cmd_spend(args, cfg) -> int:
    path = Path(cfg["paths"]["llm_ledger"])
    rows = [json.loads(x) for x in path.read_text().splitlines()] if path.exists() else []
    by_session = defaultdict(lambda: {"calls": 0, "cached": 0, "tokens": 0, "usd": 0.0})
    for r in rows:
        s = by_session[r["session_id"]]
        s["calls"] += 1
        s["cached"] += int(r["cached"])
        s["tokens"] += r["input_tokens"] + r["output_tokens"]
        s["usd"] += r["cost_usd"]
    b = cfg["llm"]["budget"]
    print(f"active session '{b['session_id']}', cap ${b['session_usd']:.2f}")
    for sid, s in by_session.items():
        print(f"{sid}: {s['calls']} calls ({s['cached']} cached), {s['tokens']} tokens, "
              f"${s['usd']:.6f}")
    return 0


def cmd_llm_check(args, cfg) -> int:
    """Check configured model IDs exist (free API call); optionally make one tiny paid call."""
    import anthropic
    available = {m.id for m in anthropic.Anthropic().models.list(limit=100)}
    configured = set(cfg["llm"]["agents"].values()) | set(cfg["llm"]["escalation"].values())
    missing = sorted(configured - available)
    for m in sorted(configured):
        print(f"{m:<22} {'OK' if m in available else 'NOT AVAILABLE'}")
    if args.ping:
        tracking.configure(cfg)
        res = LLMClient(cfg).complete("healthcheck", "Reply with the single word: pong",
                                      max_tokens=5)
        print(f"ping: {res.text!r} model={res.model} tokens={res.input_tokens}+"
              f"{res.output_tokens} cost=${res.cost_usd:.6f} cached={res.cached}")
    return 1 if missing else 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="factory")
    sub = p.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run", help="run the pipeline for one model")
    r.add_argument("--model", required=True)
    r.add_argument("--no-prompt", action="store_true", help="never prompt; approve gates in the UI")
    g = sub.add_parser("gate", help="record a gate decision")
    g.add_argument("--model", required=True)
    g.add_argument("--stage", required=True)
    g.add_argument("--decision", required=True, choices=gates.DECISIONS)
    g.add_argument("--reviewer", required=True)
    g.add_argument("--reason", default="")
    s = sub.add_parser("status", help="show stage status for a model")
    s.add_argument("--model", required=True)
    sub.add_parser("spend", help="LLM token and cost summary from the ledger")
    sub.add_parser("inventory", help="rebuild the inventory table and dependency map")
    c = sub.add_parser("llm-check", help="verify configured model IDs")
    c.add_argument("--ping", action="store_true", help="also make one tiny paid call")
    args = p.parse_args(argv)
    cfg = load_config()
    handlers = {"run": cmd_run, "gate": cmd_gate, "status": cmd_status, "spend": cmd_spend, "inventory": cmd_inventory,
                "llm-check": cmd_llm_check}
    return handlers[args.cmd](args, cfg)


if __name__ == "__main__":
    sys.exit(main())
