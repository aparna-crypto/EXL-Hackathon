# Work log

## Stage 0: Setup (2026-11-07)

**Done**
- Repo structure, `pyproject.toml`, pinned `requirements.txt` (compiled from `requirements.in` with uv),
  Python 3.11.18 venv, R 4.1.2 + `jsonlite` 1.7.3 (`r/package_versions.txt`).
- Model card JSON Schema v1.0.0 plus load/save/validate with version bump and history.
- Gate mechanism: append-only `artifacts/audit/gate_decisions.jsonl`, reason required for reject/comment,
  status derived from the log. Usable from the CLI prompt, `factory.cli gate`, and the Streamlit UI.
- LLM interface (`LLMClient`): model per agent from config, Opus escalation for translation, hard per-run
  token/USD caps and a session USD cap (checked before each call), cost ledger, response cache, and an
  MLflow run per call.
- MLflow tracking with the `TRACKING_BACKEND=local|azureml` switch.
- Resumable orchestrator with a gate after each stage. Stage functions are plugged in from Stage 2 onward.
- Streamlit UI (inventory, gate review, audit log, LLM spend), Makefile, GitHub Actions CI (ubuntu-22.04, R from apt, lint + pytest).

**Failures and fixes**
- First real API call returned `400 system: Input should be a valid array`, because we sent `system=None`.
  Fixed: `system` is now omitted when empty.
- Next call returned `400 temperature is deprecated for this model` (claude-haiku-5-5). Fixed: temperature
  is no longer sent (D9).

**Verification**
- `make test`: 26 passed (offline, fake LLM). `ruff`: clean.
- `make llm-check`: all three configured model IDs exist (the list-models call is free).
- `llm-check --ping`: 1 real Haiku call, 18 input + 4 output tokens, **$0.000038** (at the assumed prices).
  A second ping was served from cache at $0. The ledger and MLflow both recorded both calls.
- Streamlit app started headless and returned HTTP 200 on its health endpoint.

**Token cost for Stage 0:** 22 tokens, about $0.00004 (session `stages-0-3`: $0.00004 of $7.00).
