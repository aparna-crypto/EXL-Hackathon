# Agentic Model Risk Factory

EXL Hackathon, Challenge 04. This is an AI-native "factory" that **documents, redevelops and validates** a
bank's model inventory, following the PRA's model risk management principles (SS1/23). An orchestrator
calls specialised agents (Discover, Document, Redevelop, Validate, Monitor) that share a versioned
**model card**. Every stage ends in a **human gate** (approve, reject or comment), and each decision is
logged with a timestamp, the reviewer and the reason.

> Status: **Stages 0-1 done** (setup; legacy models, data and reference outputs). The agents are built in Stages 2-7. See `docs/work_log.md`.

## Quickstart

Prerequisites: Linux or macOS, `make`, R 4.x (`apt install r-base r-cran-jsonlite`), and
[uv](https://docs.astral.sh/uv/) (it installs Python 3.11 for you).

```bash
make setup                 # .venv with Python 3.11, pinned deps, R packages
cp .env.example .env       # then set ANTHROPIC_API_KEY (never commit .env)
make data                  # download public datasets, build fixed splits (about 1 min)
make legacy                # run the legacy models, refresh artifacts/reference/
make test                  # offline: uses a fake LLM, needs no key
make llm-check             # confirms the configured Claude model IDs exist (free call)
```

## One-command run

```bash
make run MODEL=<name>      # runs models/<name> through every stage and its gate
```

The run is resumable. At each gate the CLI asks for approve/reject/comment, a reviewer and a reason.
Press Enter to defer the decision to the UI instead (`make ui` → *Gate review*), then run
`make run` again to continue. A rejected stage is executed again on the next run.

## Other commands

| Command | What it does |
|---|---|
| `make ui` | Streamlit: inventory, gate review, audit log, LLM spend |
| `make mlflow-ui` | MLflow UI on the local tracking store (`artifacts/mlruns`) |
| `make status MODEL=<name>` | stage and gate status for one model |
| `make spend` | LLM tokens and cost per budget bucket |
| `make lint` | ruff |

## Layout

```
config/config.yaml        models per agent, prices, budgets, tracking backend, gate stages
schemas/                  model card JSON Schema (versioned)
src/factory/              orchestrator, gates, LLM interface, tracking, model card, CLI
app/streamlit_app.py      reviewer UI
models/<name>/            source code of each inventoried model (Stage 1)
r/                        R package list, installer, smoke test
tests/                    pytest suite (run in CI on every push)
artifacts/                model cards, gate audit log, LLM ledger and cache, MLflow store
docs/                     design and evidence documentation (start at docs/architecture.md)
```

## Key rules

- Data is public or synthetic only. Full datasets are never sent to the LLM; only schemas, summaries and code are.
- The LLM never invents numbers. All metrics are computed by code.
- Hard token and USD budgets apply per run and per session (`config/config.yaml`).
- The API key comes only from the environment or `.env`. It is never logged or committed.
