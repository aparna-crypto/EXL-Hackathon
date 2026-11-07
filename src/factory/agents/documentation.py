"""Documentation agent (Stage 3): reverse-engineers methodology, variables, transformations,
assumptions and limitations from the code, documentation files and reference outputs.

Claims must cite verbatim quotes. Quotes are checked in code: variables and transformations
without a quote found in the material are dropped; sections and assumptions/limitations
without one are kept but forced to low confidence and marked for the reviewer.
"""

from __future__ import annotations

import json

from factory.agents.common import complete_json, contains, find_quote
from factory.llm import LLMClient

AGENT = "documentation"
SECTIONS = ("purpose_and_use", "data", "methodology", "implementation", "performance",
            "monitoring_and_governance")
CONFIDENCE = ("high", "medium", "low")

SYSTEM = """You are a model documentation specialist at a UK bank, writing model documentation \
aligned with PRA SS1/23 (model risk management). You reverse-engineer ONE model from its source \
code, any documentation files and its saved reference outputs.
Rules:
- Describe what the CODE does. Where documentation disagrees with the code, the code wins; \
mention the disagreement.
- Use only the material provided. Never guess. Put anything you cannot establish in "unknowns".
- Back claims with "evidence": verbatim quotes (each at most 200 characters) copied exactly from \
one file, with "source": that file's path.
- confidence: high = stated directly in code or outputs; medium = clear from code but not \
stated; low = inferred.
- Reply with one JSON object and nothing else."""

TEMPLATE = """Write the documentation record for model `{model_id}`.

Return exactly this JSON shape:
{{
  "sections": {{
    "purpose_and_use":           {{"text": "...", "confidence": "high|medium|low", "evidence": [{{"quote": "...", "source": "..."}}]}},
    "data":                      {{"text": "data sources, target definition, sample, splits", "confidence": "...", "evidence": [...]}},
    "methodology":               {{"text": "model form, estimation, variable selection, scaling", "confidence": "...", "evidence": [...]}},
    "implementation":            {{"text": "language, libraries, seeds, inputs and outputs, how to run", "confidence": "...", "evidence": [...]}},
    "performance":               {{"text": "results from the reference outputs only; say if none exist", "confidence": "...", "evidence": [...]}},
    "monitoring_and_governance": {{"text": "owner, validation history, monitoring in place", "confidence": "...", "evidence": [...]}}
  }},
  "variables": [
    {{"name": "variable name as in the code", "role": "input|target|offset|excluded|protected|identifier", "description": "...", "transformation": "...", "evidence": "...", "source": "..."}}
  ],
  "transformations": [
    {{"step": "short name", "description": "what the code does, with constants", "evidence": "...", "source": "..."}}
  ],
  "assumptions": [{{"text": "...", "confidence": "...", "evidence": "...", "source": "..."}}],
  "limitations": [{{"text": "...", "confidence": "...", "evidence": "...", "source": "..."}}],
  "unknowns": ["facts a validator would need that the material does not give"]
}}
List every model input variable separately (for a numbered family such as V1..V28 one entry \
named "V1-V28" is fine). Transformations are in execution order.
{feedback}
=== CONTEXT FROM STAGE 2 (inventory, approved at Gate 1) ===
{context}
{files}"""


def build_prompt(model_id: str, context: dict, texts: dict[str, str],
                 feedback: str | None = None) -> str:
    files = "".join(f"\n=== FILE: {path} ===\n{text}\n" for path, text in texts.items())
    fb = (f"\nThe reviewer rejected or commented on the previous documentation; address this "
          f"feedback:\n{feedback}\n") if feedback else ""
    return TEMPLATE.format(model_id=model_id, feedback=fb, files=files,
                           context=json.dumps(context, indent=1))


def _conf(value) -> str:
    v = str(value or "").lower().strip()
    return v if v in CONFIDENCE else "low"


def _lower(conf: str) -> str:
    return CONFIDENCE[min(CONFIDENCE.index(conf) + 1, 2)]


def check_answer(raw: dict, texts: dict[str, str]) -> dict:
    rejected = []
    sections = {}
    for key in SECTIONS:
        item = (raw.get("sections") or {}).get(key) or {}
        text = str(item.get("text") or "").strip() if isinstance(item, dict) else ""
        if not text:
            sections[key] = {"text": None, "confidence": "low", "evidence": [],
                             "flag": "not described by the agent"}
            continue
        kept, bad = [], 0
        for ev in item.get("evidence") or []:
            ev = ev if isinstance(ev, dict) else {"quote": str(ev)}
            src = find_quote(ev.get("quote"), texts, ev.get("source"))
            if src:
                kept.append({"quote": ev["quote"], "source": src})
            else:
                bad += 1
                rejected.append({"item": f"section:{key}", "quote": ev.get("quote"),
                                 "problem": "quote not found in any provided file"})
        conf, flag = _conf(item.get("confidence")), None
        if not kept:
            conf, flag = "low", "no supporting quote found"
        elif bad:
            conf, flag = _lower(conf), f"{bad} quote(s) not found; confidence lowered"
        sections[key] = {"text": text, "confidence": conf, "evidence": kept, "flag": flag}

    def grounded(item: dict) -> str | None:
        return find_quote(item.get("evidence"), texts, item.get("source"))

    variables = []
    for v in raw.get("variables") or []:
        name = str(v.get("name") or "").strip()
        src = grounded(v)
        bare = name.removeprefix("woe_")
        if not name or src is None or not any(contains(t, bare.split("-")[0])
                                              for t in texts.values()):
            rejected.append({"item": f"variable:{name}", "quote": v.get("evidence"),
                             "problem": "variable or its quote not found in the material"})
            continue
        variables.append({"name": name, "role": str(v.get("role") or "input").lower(),
                          "description": v.get("description"),
                          "transformation": v.get("transformation"),
                          "evidence": v["evidence"], "source": src})

    transformations = []
    for t in raw.get("transformations") or []:
        src = grounded(t)
        if src is None:
            rejected.append({"item": f"transformation:{t.get('step')}", "quote": t.get("evidence"),
                             "problem": "quote not found in any provided file"})
            continue
        transformations.append({"step": t.get("step"), "description": t.get("description"),
                                "evidence": t["evidence"], "source": src})

    def statements(key: str) -> list[dict]:
        out = []
        for s in raw.get(key) or []:
            if not str(s.get("text") or "").strip():
                continue
            src = grounded(s)
            out.append({"text": s["text"], "confidence": _conf(s.get("confidence")) if src else "low",
                        "evidence": s.get("evidence") if src else None, "source": src,
                        "grounded": src is not None})
        return out

    return {"sections": sections, "variables": variables, "transformations": transformations,
            "assumptions": statements("assumptions"), "limitations": statements("limitations"),
            "unknowns": [str(u) for u in raw.get("unknowns") or []], "rejected": rejected}


def run(llm: LLMClient, model_id: str, context: dict, texts: dict[str, str], cfg: dict,
        feedback: str | None = None) -> dict:
    prompt = build_prompt(model_id, context, texts, feedback)
    raw, results = complete_json(llm, AGENT, prompt, SYSTEM, cfg["max_tokens"], model_id)
    out = check_answer(raw, texts)
    out["llm"] = {"model": results[-1].model, "calls": len(results),
                  "input_tokens": sum(r.input_tokens for r in results),
                  "output_tokens": sum(r.output_tokens for r in results),
                  "cost_usd": round(sum(r.cost_usd for r in results), 6),
                  "cached": all(r.cached for r in results)}
    return out
