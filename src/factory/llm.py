"""Single interface for every LLM call: model routing per agent, hard budgets, cost ledger,
response cache and MLflow logging.

Agents call `LLMClient.complete(agent=..., prompt=...)`; they never talk to a provider directly.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import time
import uuid
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Callable

from factory import tracking
from factory.model_card import now_iso


class BudgetExceeded(RuntimeError):
    pass


class LLMError(RuntimeError):
    """The provider rejected or failed a call (bad request, network, auth)."""


@dataclass
class LLMResult:
    text: str
    agent: str
    model: str
    input_tokens: int
    output_tokens: int
    cost_usd: float
    cached: bool
    duration_s: float


# ---- provider backends -------------------------------------------------------------------

class AnthropicBackend:
    def __init__(self) -> None:
        import anthropic
        if not os.getenv("ANTHROPIC_API_KEY"):
            raise RuntimeError("ANTHROPIC_API_KEY is not set (see .env.example)")
        self._client = anthropic.Anthropic()

    def call(self, model: str, system: str, prompt: str, max_tokens: int,
             temperature: float, thinking: str | None = None) -> tuple[str, int, int]:
        kwargs = {"system": system} if system else {}
        if temperature is not None:
            kwargs["temperature"] = temperature
        if thinking:
            kwargs["thinking"] = {"type": thinking}
        msg = self._client.messages.create(
            model=model, max_tokens=max_tokens,
            messages=[{"role": "user", "content": prompt}], **kwargs,
        )
        text = "".join(block.text for block in msg.content if block.type == "text")
        return text, msg.usage.input_tokens, msg.usage.output_tokens


class FakeBackend:
    """Offline backend for tests and dry runs. `responder(model, system, prompt) -> str`."""

    def __init__(self, responder: Callable[[str, str, str], str] | None = None) -> None:
        self.responder = responder or (lambda model, system, prompt: f"[fake:{model}] ok")
        self.calls = 0

    def call(self, model, system, prompt, max_tokens, temperature, thinking=None):
        self.calls += 1
        text = self.responder(model, system, prompt)
        return text, estimate_tokens(system + prompt), estimate_tokens(text)


def estimate_tokens(text: str) -> int:
    """Conservative token estimate (~3 chars/token; real ratio for English is closer to 4)."""
    return math.ceil(len(text) / 3)


# ---- client ------------------------------------------------------------------------------

class LLMClient:
    def __init__(self, cfg: dict, backend=None, run_id: str | None = None,
                 log_to_mlflow: bool = True) -> None:
        self.cfg = cfg["llm"]
        self.paths = cfg["paths"]
        self.run_id = run_id or uuid.uuid4().hex[:12]
        self.log_to_mlflow = log_to_mlflow
        if backend is None:
            backend = FakeBackend() if self.cfg["provider"] == "fake" else AnthropicBackend()
        self.backend = backend
        self.run_tokens = 0
        self.run_cost = 0.0

    # routing
    def model_for(self, agent: str, escalate: bool = False) -> str:
        if escalate:
            if agent not in self.cfg.get("escalation", {}):
                raise ValueError(f"no escalation model configured for agent {agent!r}")
            return self.cfg["escalation"][agent]
        if agent not in self.cfg["agents"]:
            raise ValueError(f"no model configured for agent {agent!r} in config.yaml")
        return self.cfg["agents"][agent]

    def cost(self, model: str, input_tokens: int, output_tokens: int) -> float:
        price = self.cfg["pricing_usd_per_mtok"][model]
        return (input_tokens * price["input"] + output_tokens * price["output"]) / 1_000_000

    # ledger
    def _ledger(self) -> Path:
        return Path(self.paths["llm_ledger"])

    def session_spend(self) -> float:
        path, sid = self._ledger(), self.cfg["budget"]["session_id"]
        if not path.exists():
            return 0.0
        rows = [json.loads(line) for line in path.read_text().splitlines() if line.strip()]
        return sum(r["cost_usd"] for r in rows if r["session_id"] == sid)

    def _append_ledger(self, result: LLMResult, model_id: str | None) -> None:
        row = {"timestamp": now_iso(), "session_id": self.cfg["budget"]["session_id"],
               "run_id": self.run_id, "model_id": model_id, **asdict(result)}
        row.pop("text")
        path = self._ledger()
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "a") as f:
            f.write(json.dumps(row) + "\n")

    # budget
    def _check_budget(self, model: str, system: str, prompt: str, max_tokens: int) -> None:
        b = self.cfg["budget"]
        est_in = estimate_tokens(system + prompt)
        worst_tokens = est_in + max_tokens
        worst_cost = self.cost(model, est_in, max_tokens)
        if self.run_tokens + worst_tokens > b["per_run_tokens"]:
            raise BudgetExceeded(f"per-run token cap {b['per_run_tokens']} would be exceeded "
                                 f"(used {self.run_tokens}, next call up to {worst_tokens})")
        if self.run_cost + worst_cost > b["per_run_usd"]:
            raise BudgetExceeded(f"per-run cap ${b['per_run_usd']:.2f} would be exceeded "
                                 f"(used ${self.run_cost:.4f}, next call up to ${worst_cost:.4f})")
        spent = self.session_spend()
        if spent + worst_cost > b["session_usd"]:
            raise BudgetExceeded(f"session '{b['session_id']}' cap ${b['session_usd']:.2f} would "
                                 f"be exceeded (spent ${spent:.4f}, next call up to "
                                 f"${worst_cost:.4f})")

    # cache
    def thinking_for(self, agent: str) -> str | None:
        return (self.cfg.get("thinking") or {}).get(agent)

    def _cache_file(self, model: str, system: str, prompt: str, max_tokens: int,
                    thinking: str | None = None) -> Path:
        key = json.dumps([model, system, prompt, max_tokens, self.cfg["temperature"]]
                         + ([thinking] if thinking else []))
        digest = hashlib.sha256(key.encode()).hexdigest()
        return Path(self.paths["llm_cache"]) / f"{digest}.json"

    # main entry point
    def complete(self, agent: str, prompt: str, system: str = "", max_tokens: int | None = None,
                 escalate: bool = False, model_id: str | None = None,
                 use_cache: bool = True) -> LLMResult:
        model = self.model_for(agent, escalate)
        max_tokens = max_tokens or self.cfg["default_max_tokens"]
        thinking = self.thinking_for(agent)
        cache_file = self._cache_file(model, system, prompt, max_tokens, thinking)

        if use_cache and cache_file.exists():
            text = json.loads(cache_file.read_text())["text"]
            result = LLMResult(text, agent, model, 0, 0, 0.0, True, 0.0)
        else:
            self._check_budget(model, system, prompt, max_tokens)
            t0 = time.perf_counter()
            try:
                text, in_tok, out_tok = self.backend.call(
                    model, system, prompt, max_tokens, self.cfg["temperature"],
                    **({"thinking": thinking} if thinking else {}))
            except Exception as e:
                raise LLMError(f"{agent} call to {model} failed: {e}") from e
            duration = round(time.perf_counter() - t0, 3)
            result = LLMResult(text, agent, model, in_tok, out_tok,
                               round(self.cost(model, in_tok, out_tok), 6), False, duration)
            self.run_tokens += in_tok + out_tok
            self.run_cost += result.cost_usd
            cache_file.parent.mkdir(parents=True, exist_ok=True)
            cache_file.write_text(json.dumps({"agent": agent, "model": model, "text": text}))

        self._append_ledger(result, model_id)
        if self.log_to_mlflow:
            tracking.log_agent_run(agent, model, prompt, system, result.text, result.duration_s,
                                   result.input_tokens, result.output_tokens, result.cost_usd,
                                   result.cached, self.run_id, model_id)
        return result
