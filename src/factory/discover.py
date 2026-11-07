"""Stage 2 (Discover): scan the code, run the inventory agent, flag documentation and
validation gaps, and rebuild the cross-model inventory table and dependency map.
"""

from __future__ import annotations

import csv
import json
import re
from datetime import date
from pathlib import Path

from factory import gates, model_card, scanner
from factory.agents import inventory as inventory_agent

STAGE = "discover"


def _months_between(earlier: str, as_of: date) -> int:
    y, m = int(earlier[:4]), int(earlier[5:7]) if len(earlier) >= 7 else 1
    return (as_of.year - y) * 12 + (as_of.month - m)


def reviewer_feedback(audit_dir: str, model_id: str, since: str | None) -> str | None:
    """Reasons given in reject/comment decisions on the previous run of this stage."""
    if not since:
        return None
    notes = [f"- {e['decision']} by {e['reviewer']}: {e['reason']}"
             for e in gates.history(audit_dir, model_id, STAGE)
             if e["decision"] in ("reject", "comment") and e["timestamp"] >= since and e["reason"]]
    return "\n".join(notes) or None


def assess_gaps(card: dict, scan: dict, agent: dict, as_of: date, dcfg: dict) -> tuple[str, list]:
    """Deterministic rules -> (documentation_status, gaps). Thresholds come from config."""
    f, gaps = agent["fields"], []
    if not scan["doc_files"]:
        status = "missing"
        gaps.append("Documentation missing: no documentation file is kept with the code "
                    "(only code comments).")
    else:
        status = "current"
        for finding in agent["findings"]:
            status = "outdated"
            gaps.append(f"Documentation contradicts code: {finding['issue']}")
        doc_date = f.get("documentation_date")
        if doc_date and _months_between(doc_date, as_of) > dcfg["doc_max_age_months"]:
            status = "outdated"
            gaps.append(f"Documentation last updated {doc_date} "
                        f"({_months_between(doc_date, as_of)} months ago; limit "
                        f"{dcfg['doc_max_age_months']}).")
    tier = f.get("risk_tier")
    interval = dcfg["revalidation_months"][tier or "high"]
    last = f.get("last_validation_date")
    if not last:
        gaps.append("No validation record: last independent validation date is unknown.")
    elif _months_between(last, as_of) > interval:
        gaps.append(f"Validation overdue: last validated {last} ({_months_between(last, as_of)} "
                    f"months ago; {tier or 'unknown (high assumed)'} tier requires every "
                    f"{interval} months).")
    for key, label in (("owner", "owner"), ("purpose", "purpose"), ("risk_tier", "risk tier")):
        if not f.get(key):
            gaps.append(f"No {label} recorded.")
    for r in agent["rejected"]:
        if r["field"] != "documentation_finding":
            gaps.append(f"Agent answer for '{r['field']}' rejected ({r['problem']}); left blank "
                        "for the reviewer.")
    if set(scan["languages"]) - set(dcfg["executable_languages"]):
        gaps.append(f"Not executable here (no runtime for "
                    f"{', '.join(sorted(set(scan['languages']) - set(dcfg['executable_languages'])))}"
                    "): outputs cannot be reproduced; document only.")
    return status, gaps


def run_stage(card: dict, ctx) -> dict:
    cfg, dcfg = ctx.cfg, ctx.cfg["discover"]
    root = ctx.model_dir.parent.parent
    scan = scanner.scan_model(ctx.model_dir, root)
    cap = dcfg["max_chars_per_file"]
    texts = {f["path"]: (root / f["path"]).read_text()[:cap] for f in scan["files"]}
    texts |= {d["path"]: (root / d["path"]).read_text(errors="replace")[:cap]
              for d in scan["doc_files"] if d["chars"]}
    feedback = reviewer_feedback(cfg["paths"]["audit"], card["model_id"],
                                 card["stages"][STAGE]["completed_at"])
    agent = inventory_agent.run(ctx.llm, card["model_id"], scan, texts, dcfg, feedback)
    as_of = date.fromisoformat(dcfg["as_of"]) if dcfg.get("as_of") else date.today()
    doc_status, gaps = assess_gaps(card, scan, agent, as_of, dcfg)

    fld = agent["fields"]
    if fld.get("name"):
        card["name"] = fld["name"]
    card["inventory"].update(
        owner=fld.get("owner"), purpose=fld.get("purpose"), model_type=fld.get("model_type"),
        risk_tier=fld.get("risk_tier"), data_sources=scan["inputs"],
        last_validation_date=fld.get("last_validation_date"), documentation_status=doc_status,
        model_ref=fld.get("model_ref"), documentation_date=fld.get("documentation_date"),
        evidence=agent["evidence"], unknowns=agent["unknowns"], assessed_as_of=as_of.isoformat())
    card["discovery"] = {**scan, "documentation_findings": agent["findings"],
                         "rejected_agent_answers": agent["rejected"], "llm": agent["llm"],
                         "reviewer_feedback_used": feedback}
    card["gaps"] = gaps
    model_card.validate(card)
    write_inventory(cfg, pending=card)

    llm = agent["llm"]
    tier = fld.get("risk_tier") or "unknown"
    summary = "\n".join([
        f"{fld.get('model_ref') or '(no ref)'} {card['name']} | owner: {fld.get('owner') or 'unknown'}"
        f" | type: {fld.get('model_type') or 'unknown'} | risk tier: {tier}",
        f"Last validation: {fld.get('last_validation_date') or 'unknown'} | documentation: "
        f"{doc_status} | {len(agent['findings'])} doc/code contradiction(s)",
        f"Scan: {'/'.join(scan['languages'])}, {scan['loc']} LOC, libraries "
        f"{', '.join(scan['libraries']) or 'none'}; {len(scan['inputs'])} inputs, "
        f"{len(scan['outputs'])} outputs",
        f"Gaps ({len(gaps)}):", *[f"- {g}" for g in gaps],
        f"LLM: {llm['model']}, {llm['input_tokens'] + llm['output_tokens']} tokens, "
        f"${llm['cost_usd']:.4f}{' (cached)' if llm['cached'] else ''}",
    ])
    return {"summary": summary}


# ---- cross-model outputs ---------------------------------------------------------------------

COLUMNS = ["model_id", "model_ref", "name", "language", "owner", "purpose", "model_type",
           "risk_tier", "last_validation_date", "documentation_status", "n_gaps", "data_sources"]


def inventory_rows(cards: list[dict]) -> list[dict]:
    rows = []
    for c in cards:
        inv = c["inventory"]
        rows.append({"model_id": c["model_id"], "model_ref": inv.get("model_ref"),
                     "name": c["name"], "language": c["language"], "owner": inv.get("owner"),
                     "purpose": inv.get("purpose"), "model_type": inv.get("model_type"),
                     "risk_tier": inv.get("risk_tier"),
                     "last_validation_date": inv.get("last_validation_date"),
                     "documentation_status": inv.get("documentation_status"),
                     "n_gaps": len(c.get("gaps", [])),
                     "data_sources": "; ".join(inv.get("data_sources", []))})
    return rows


def _upstream(path: str) -> tuple[str, str] | None:
    """Processed dataset -> (source key, public URL) from factory.data.SOURCES."""
    from factory.data import SOURCES
    name = Path(path).name
    if not path.startswith("data/processed/"):
        return None
    for key, src in SOURCES.items():
        if name.startswith(key):
            return key, src["url"]
    return None


def dependency_graph(cards: list[dict]) -> tuple[dict[str, str], list[tuple[str, str, str]]]:
    """Nodes {id: kind} and edges (from, to, label) across all discovered models."""
    nodes, edges = {}, []
    for c in cards:
        d = c.get("discovery")
        if not d:
            continue
        m = f"model:{c['model_id']}"
        nodes[m] = "model"
        for i in d["inputs"]:
            nodes.setdefault(i, "data")
            edges.append((i, m, "reads"))
            up = _upstream(i)
            if up:
                nodes.setdefault(f"source:{up[0]}", "source")
                edge = (f"source:{up[0]}", i, "factory.data")
                if edge not in edges:
                    edges.append(edge)
        for o in d["outputs"]:
            nodes.setdefault(o, "output")
            edges.append((m, o, "writes"))
    return nodes, edges


def _labels(cards: list[dict]) -> dict[str, str]:
    from factory.data import SOURCES
    out = {f"model:{c['model_id']}": f"{c['inventory'].get('model_ref') or c['model_id']}\\n"
           f"{c['model_id']} ({c['language']})" for c in cards}
    out |= {f"source:{k}": f"{k}\\n{re.sub(r'^https?://', '', v['url']).split('/')[0]}"
            for k, v in SOURCES.items()}
    return out


SHAPES = {"model": ("box", "#cfe2ff"), "data": ("cylinder", "#e2e3e5"),
          "output": ("note", "#d1e7dd"), "source": ("folder", "#fff3cd")}


def to_dot(cards: list[dict]) -> str:
    nodes, edges = dependency_graph(cards)
    labels = _labels(cards)
    lines = ["digraph dependencies {", "  rankdir=LR;", '  node [fontsize=10, style=filled];']
    for n, kind in nodes.items():
        shape, color = SHAPES[kind]
        lines.append(f'  "{n}" [label="{labels.get(n, n)}", shape={shape}, fillcolor="{color}"];')
    lines += [f'  "{a}" -> "{b}" [label="{lbl}", fontsize=8];' for a, b, lbl in edges]
    return "\n".join(lines + ["}"]) + "\n"


def to_mermaid(cards: list[dict]) -> str:
    nodes, edges = dependency_graph(cards)
    labels = _labels(cards)
    ids = {n: f"n{i}" for i, n in enumerate(nodes)}
    brackets = {"model": ("[", "]"), "data": ("[(", ")]"), "output": ("[/", "/]"),
                "source": ("{{", "}}")}
    lines = ["flowchart LR"]
    for n, kind in nodes.items():
        a, b = brackets[kind]
        lines.append(f'  {ids[n]}{a}"{labels.get(n, n).replace(chr(92) + "n", "<br>")}"{b}')
    lines += [f"  {ids[a]} -->|{lbl}| {ids[b]}" for a, b, lbl in edges]
    return "\n".join(lines) + "\n"


def write_inventory(cfg: dict, pending: dict | None = None) -> Path:
    """Rebuild artifacts/inventory/ from all model cards (plus a card not yet saved)."""
    cards = {c["model_id"]: c for c in model_card.list_cards(cfg["paths"]["model_cards"])}
    if pending:
        cards[pending["model_id"]] = pending
    cards = [cards[k] for k in sorted(cards)]
    out = Path(cfg["paths"]["inventory"])
    out.mkdir(parents=True, exist_ok=True)
    rows = inventory_rows(cards)
    with open(out / "inventory.csv", "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=COLUMNS)
        w.writeheader()
        w.writerows(rows)
    md = ["# Model inventory", "",
          "Generated by Stage 2 (Discover) from the model cards. Gate status is live in the UI.", "",
          "| " + " | ".join(COLUMNS[:-1]) + " |", "|" + "---|" * (len(COLUMNS) - 1)]
    md += ["| " + " | ".join(str(r[k] if r[k] is not None else "unknown").replace("|", "/")
                             for k in COLUMNS[:-1]) + " |" for r in rows]
    md += ["", "## Gaps", ""]
    for c in cards:
        md.append(f"### {c['model_id']}")
        md += [f"- {g}" for g in c.get("gaps", [])] or ["- none"]
        md.append("")
    md += ["## Dependency map", "", "```mermaid", to_mermaid(cards).rstrip(), "```", ""]
    (out / "inventory.md").write_text("\n".join(md))
    (out / "dependency_map.dot").write_text(to_dot(cards))
    (out / "dependency_map.mmd").write_text(to_mermaid(cards))
    (out / "inventory.json").write_text(json.dumps(rows, indent=2))
    return out
