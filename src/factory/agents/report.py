"""Report agent: writes the narrative of a validation report from computed evidence.

The agent never sees data rows and never types a metric: numbers are written as [[fact]]
placeholders and filled in from the evidence. Sentences that contain a typed decimal number or
percentage, or an unknown placeholder, are dropped and logged.
"""

from __future__ import annotations

import json
import re

from factory.agents.common import complete_json
from factory.llm import LLMClient

AGENT = "report"
SEVERITIES = ("high", "medium", "low")
PLACEHOLDER = re.compile(r"\[\[([A-Za-z0-9_.:\-]+)\]\]")
TYPED_NUMBER = re.compile(r"\d+(?:\.\d+)?\s?%|\b\d+\.\d+\b")

SYSTEM = (
    "You are an independent model validator writing a validation report for a UK bank's model "
    "risk committee (PRA SS1/23 principles). You interpret evidence computed by deterministic "
    "code. Reply with one JSON object only."
)


def build_prompt(context: dict, facts: dict[str, str], checks: list[dict], notes: list[str],
                 sections: list[str], feedback: str | None = None) -> str:
    parts = [
        "Write the narrative for the validation report of this model.",
        "MODEL CONTEXT\n" + json.dumps(context, indent=1, default=str),
        "FACTS (key: value). These are the only numbers you may use.\n"
        + "\n".join(f"{k}: {v}" for k, v in facts.items()),
        "CHECKS with deterministic RAG ratings (final; explain them, never re-rate)\n"
        + json.dumps(checks, indent=1, default=str),
        "VALIDATOR NOTES\n" + "\n".join(f"- {n}" for n in notes),
        f"""RULES
- Write every number as a placeholder [[key]] using a key from FACTS, e.g. "a test AUC of
  [[performance.test_auc]]". Never type a metric value: sentences with typed decimal numbers or
  percentages are deleted automatically.
- Do not invent data, thresholds, owners, dates, approvals or results that are not in the input.
- Each section narrative: 2-4 sentences on what the evidence shows and why it matters.
- One finding per amber or red check (merge closely related checks), each with a concrete,
  proportionate recommendation. Severity: red check -> high, amber -> medium, otherwise low.
- Limitations: what this validation could not assess, based on the notes and checks.

Reply with JSON:
{{"executive_summary": "3-5 sentences",
  "sections": {{{", ".join(f'"{s}": "..."' for s in sections)}}},
  "findings": [{{"severity": "high|medium|low", "section": "<section>", "finding": "...",
                "recommendation": "..."}}],
  "limitations": ["..."]}}""",
    ]
    if feedback:
        parts.append("REVIEWER FEEDBACK FROM THE LAST GATE (address it):\n" + feedback)
    return "\n\n".join(parts)


def ground(text: str, facts: dict[str, str]) -> tuple[str, list[str]]:
    """Fill placeholders; drop sentences with typed numbers or unknown placeholders."""
    kept, dropped = [], []
    for sent in re.split(r"(?<=[.!?])\s+", (text or "").strip()):
        if not sent:
            continue
        keys = PLACEHOLDER.findall(sent)
        if TYPED_NUMBER.search(PLACEHOLDER.sub("", sent)) or any(k not in facts for k in keys):
            dropped.append(sent)
            continue
        kept.append(PLACEHOLDER.sub(lambda m: facts[m.group(1)], sent))
    return " ".join(kept), dropped


def run(llm: LLMClient, model_id: str, context: dict, facts: dict[str, str], checks: list[dict],
        notes: list[str], sections: list[str], max_tokens: int,
        feedback: str | None = None) -> dict:
    prompt = build_prompt(context, facts, checks, notes, sections, feedback)
    data, results = complete_json(llm, AGENT, prompt, SYSTEM, max_tokens, model_id)
    dropped: list[str] = []

    def g(text) -> str:
        out, d = ground(str(text or ""), facts)
        dropped.extend(d)
        return out

    secs = data.get("sections") or {}
    findings = []
    for f in data.get("findings") or []:
        if not isinstance(f, dict):
            continue
        item = {"severity": f.get("severity") if f.get("severity") in SEVERITIES else "medium",
                "section": f.get("section") if f.get("section") in sections else "general",
                "finding": g(f.get("finding")), "recommendation": g(f.get("recommendation"))}
        if item["finding"]:
            findings.append(item)
    narrative = {
        "executive_summary": g(data.get("executive_summary")),
        "sections": {s: g(secs.get(s)) for s in sections},
        "findings": findings,
        "limitations": [x for x in (g(t) for t in data.get("limitations") or []) if x],
        "dropped_sentences": dropped,
        "llm": {"model": results[-1].model, "calls": len(results),
                "input_tokens": sum(r.input_tokens for r in results),
                "output_tokens": sum(r.output_tokens for r in results),
                "cost_usd": round(sum(r.cost_usd for r in results), 6),
                "cached": all(r.cached for r in results)},
    }
    return narrative
