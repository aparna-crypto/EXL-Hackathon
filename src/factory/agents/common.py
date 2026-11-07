"""Helpers shared by agents: JSON extraction and evidence grounding."""

from __future__ import annotations

import json
import re

from factory.agents import AgentError
from factory.llm import LLMClient, LLMResult


def parse_json(text: str) -> dict:
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text.strip())
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end <= start:
        raise ValueError("no JSON object in reply")
    return json.loads(text[start:end + 1])


def complete_json(llm: LLMClient, agent: str, prompt: str, system: str, max_tokens: int,
                  model_id: str) -> tuple[dict, list[LLMResult]]:
    """Ask for a JSON object; on a parse failure retry once with a correction note."""
    results = []
    for attempt in range(2):
        p = prompt if attempt == 0 else (prompt + "\n\nYour previous reply was not a valid JSON "
                                         "object. Reply with the JSON object only.")
        res = llm.complete(agent, p, system=system, max_tokens=max_tokens, model_id=model_id)
        results.append(res)
        try:
            return parse_json(res.text), results
        except (ValueError, json.JSONDecodeError):
            continue
    raise AgentError(f"{agent} agent returned invalid JSON twice for {model_id}")


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[#*\"']", " ", s)).strip().lower()


def find_quote(quote: str | None, texts: dict[str, str], prefer: str | None = None) -> str | None:
    """Return the file whose text contains `quote` (whitespace and comment markers ignored)."""
    q = _norm(quote or "")
    if len(q) < 3:
        return None
    order = ([prefer] if prefer in texts else []) + [k for k in texts if k != prefer]
    return next((k for k in order if q in _norm(texts[k])), None)


def contains(haystack: str, needle: str) -> bool:
    return _norm(needle) in _norm(haystack)
