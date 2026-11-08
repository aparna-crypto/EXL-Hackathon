"""Monitor agent: writes the drift alert from computed monitoring evidence.

Like the report agent it never sees data rows and never types a metric: numbers are [[fact]]
placeholders filled in from the evidence, and sentences with typed numbers are dropped.
"""

from __future__ import annotations

import json

from factory.agents.common import complete_json
from factory.agents.report import ground
from factory.llm import LLMClient

AGENT = "monitor"

SYSTEM = (
    "You are a model monitoring analyst at a UK bank (PRA SS1/23, principle 5). You explain "
    "drift evidence computed by deterministic code to the model owner. Reply with one JSON "
    "object only."
)


def build_prompt(context: dict, facts: dict[str, str]) -> str:
    return "\n\n".join([
        "A monitoring breach has triggered re-validation. Write the alert.",
        "CONTEXT\n" + json.dumps(context, indent=1, default=str),
        "FACTS (key: value). These are the only numbers you may use.\n"
        + "\n".join(f"{k}: {v}" for k, v in facts.items()),
        """RULES
- Write every number as a placeholder [[key]] using a key from FACTS. Never type a metric value:
  sentences with typed decimal numbers or percentages are deleted automatically.
- The RAG ratings and the trigger decision are final; explain them, never re-rate.
- Likely drivers must be supported by the CSI facts; say so if the evidence is only suggestive.
- Do not invent business events, dates, owners or results that are not in the input.

Reply with JSON:
{"alert": "3-5 sentences: what breached, when, how it evolved, why it matters",
 "likely_drivers": ["..."],
 "recommended_actions": ["..."]}""",
    ])


def run(llm: LLMClient, model_id: str, context: dict, facts: dict[str, str],
        max_tokens: int) -> dict:
    data, results = complete_json(llm, AGENT, build_prompt(context, facts), SYSTEM, max_tokens,
                                  model_id)
    alert, dropped = ground(str(data.get("alert", "")), facts)
    lists = {}
    for key in ("likely_drivers", "recommended_actions"):
        lists[key] = []
        for item in data.get(key) or []:
            text, d = ground(str(item), facts)
            dropped += d
            if text:
                lists[key].append(text)
    return {"alert": alert, **lists, "dropped_sentences": dropped,
            "llm": {"model": results[-1].model,
                    "input_tokens": sum(r.input_tokens for r in results),
                    "output_tokens": sum(r.output_tokens for r in results),
                    "cost_usd": round(sum(r.cost_usd for r in results), 6),
                    "cached": all(r.cached for r in results), "calls": len(results)}}
