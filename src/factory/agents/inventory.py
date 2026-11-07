"""Inventory agent (Stage 2): fills the inventory fields of the model card from scanner facts,
source code and documentation files.

Every value must come with a verbatim quote from a named file. Quotes are checked against the
files in code; a value whose quote cannot be found is dropped and becomes a gap for the
reviewer, so the agent cannot introduce facts that are not in the material.
"""

from __future__ import annotations

import json
import re

from factory.agents.common import complete_json, contains, find_quote
from factory.llm import LLMClient

AGENT = "inventory"
FIELDS = ("name", "model_ref", "owner", "purpose", "model_type", "last_validation_date",
          "documentation_date")
LITERAL_FIELDS = ("model_ref", "owner")       # value itself must appear in the quote
DATE_FIELDS = ("last_validation_date", "documentation_date")
DATE_RE = re.compile(r"^(\d{4})(?:-(\d{2}))?(?:-(\d{2}))?$")

SYSTEM = """You are a model inventory analyst at a UK bank, working to PRA SS1/23 (model risk \
management). You receive static-scan facts, source code and any documentation files for ONE model.
Rules:
- Use only the material provided. Never guess. If something is not stated or clearly implied, \
set "value" to null and add it to "unknowns".
- Every non-null value needs "evidence": a verbatim quote of at most 200 characters copied \
exactly from one file, and "source": that file's path.
- Reply with one JSON object and nothing else."""

TEMPLATE = """Fill the inventory record for model folder `{model_id}`.

Risk-tier rubric (choose one tier and justify it from the material):
{rubric}

Return exactly this JSON shape:
{{
  "name":                 {{"value": "short model name or null", "evidence": "...", "source": "..."}},
  "model_ref":            {{"value": "the bank's model identifier or null", "evidence": "...", "source": "..."}},
  "owner":                {{"value": "owning team or person, or null", "evidence": "...", "source": "..."}},
  "purpose":              {{"value": "one sentence, or null", "evidence": "...", "source": "..."}},
  "model_type":           {{"value": "e.g. application PD scorecard (logistic regression)", "evidence": "...", "source": "..."}},
  "last_validation_date": {{"value": "YYYY-MM-DD or YYYY-MM or null", "evidence": "...", "source": "..."}},
  "documentation_date":   {{"value": "YYYY-MM-DD or YYYY-MM date of the separate documentation file, or null", "evidence": "...", "source": "..."}},
  "risk_tier":            {{"value": "high|medium|low|null", "rationale": "one sentence", "evidence": "...", "source": "..."}},
  "documentation_findings": [
    {{"issue": "what the documentation says vs what the code does", "doc_quote": "...", "doc_source": "...", "code_quote": "...", "code_source": "..."}}
  ],
  "unknowns": ["inventory facts that the material does not state"]
}}
documentation_findings lists statements in documentation files or header comments that \
contradict the code itself (constants, variables, method). Use [] if there are none.
{feedback}
=== STATIC SCAN FACTS (JSON) ===
{facts}
{files}"""


def build_prompt(model_id: str, scan: dict, texts: dict[str, str], rubric: str,
                 feedback: str | None = None) -> str:
    facts = {k: v for k, v in scan.items() if k not in ("scanned_at",)}
    facts["files"] = [{k: v for k, v in f.items() if k != "header"} for f in scan["files"]]
    files = "".join(f"\n=== FILE: {path} ===\n{text}\n" for path, text in texts.items())
    fb = (f"\nThe reviewer rejected the previous inventory record with this feedback; address "
          f"it:\n{feedback}\n") if feedback else ""
    return TEMPLATE.format(model_id=model_id, rubric=rubric.strip(), feedback=fb,
                           facts=json.dumps(facts, indent=1), files=files)


def _date_ok(value: str, evidence: str) -> bool:
    m = DATE_RE.match(value or "")
    return bool(m) and m.group(1) in evidence


def check_answer(raw: dict, texts: dict[str, str]) -> dict:
    """Keep only values backed by a quote found in the material; report the rest."""
    fields, evidence, rejected = {}, {}, []
    for name in FIELDS + ("risk_tier",):
        item = raw.get(name) or {}
        value = item.get("value") if isinstance(item, dict) else None
        if value in (None, "", "null"):
            fields[name] = None
            continue
        value = str(value).strip()
        quote = item.get("evidence") or ""
        source = find_quote(quote, texts, item.get("source"))
        problem = None
        if source is None:
            problem = "quote not found in any provided file"
        elif name in LITERAL_FIELDS and not contains(quote, value):
            problem = "value does not appear in its quote"
        elif name in DATE_FIELDS and not _date_ok(value, quote):
            problem = "date is not YYYY[-MM[-DD]] or its year is not in the quote"
        elif name == "risk_tier" and value.lower() not in ("high", "medium", "low"):
            problem = "tier must be high, medium or low"
        if problem:
            fields[name] = None
            rejected.append({"field": name, "value": value, "evidence": quote, "problem": problem})
            continue
        fields[name] = value.lower() if name == "risk_tier" else value
        evidence[name] = {"evidence": quote, "source": source}
        if name == "risk_tier":
            evidence[name]["rationale"] = (item.get("rationale") or "").strip()

    findings = []
    for f in raw.get("documentation_findings") or []:
        doc_src = find_quote(f.get("doc_quote"), texts, f.get("doc_source"))
        code_src = find_quote(f.get("code_quote"), texts, f.get("code_source"))
        if doc_src and code_src:
            findings.append({**f, "doc_source": doc_src, "code_source": code_src})
        else:
            rejected.append({"field": "documentation_finding", "value": f.get("issue"),
                             "evidence": f.get("doc_quote"),
                             "problem": "doc or code quote not found in the provided files"})
    unknowns = [str(u) for u in raw.get("unknowns") or []]
    return {"fields": fields, "evidence": evidence, "findings": findings, "rejected": rejected,
            "unknowns": unknowns}


def run(llm: LLMClient, model_id: str, scan: dict, texts: dict[str, str], cfg: dict,
        feedback: str | None = None) -> dict:
    prompt = build_prompt(model_id, scan, texts, cfg["risk_tier_rubric"], feedback)
    raw, results = complete_json(llm, AGENT, prompt, SYSTEM, cfg["max_tokens"], model_id)
    out = check_answer(raw, texts)
    out["llm"] = {"model": results[-1].model, "calls": len(results),
                  "input_tokens": sum(r.input_tokens for r in results),
                  "output_tokens": sum(r.output_tokens for r in results),
                  "cost_usd": round(sum(r.cost_usd for r in results), 6),
                  "cached": all(r.cached for r in results)}
    return out
