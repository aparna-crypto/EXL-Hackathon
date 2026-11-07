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

## Stage 1: Legacy models and data (2026-11-07)

**Done**
- `src/factory/data.py` (`make data`): downloads 3 public datasets with sha256 checks and writes fixed
  splits (seed 42) to `data/processed/` (gitignored).
  - German Credit: 1,000 rows, 700 train / 300 test, stratified, bad rate 0.300 in both. `sex` and
    `age_group` were derived for fairness testing only.
  - ULB fraud: 284,807 transactions split by time into 227,845 train / 56,962 test. Fraud rate 0.00173.
  - freMTPL2freq: 677,991 policies, 542,392 train / 135,599 test. Claim frequency 0.0737 per policy-year.
- Four synthetic legacy models in `models/` (`make legacy` runs the three executable ones):

| Model | Language | Method | Reference result (test set) |
|---|---|---|---|
| `credit_scorecard` | R 4.1.2 | WoE binning + logistic regression, PDO-scaled score | 14 of 17 candidates selected (IV ≥ 0.02); AUC 0.8010, Gini 0.6019, KS 0.5063 |
| `fraud_gbm` | Python | sklearn HistGradientBoosting | AUC 0.9859, average precision 0.7966 |
| `pricing_glm` | Python | statsmodels Poisson GLM, log(exposure) offset | mean Poisson deviance 0.2386; 5,234 observed vs 5,307 predicted claims |
| `sas_behaviour_scorecard` | SAS | PROC LOGISTIC behavioural score | not executed (no SAS runtime) |

- Reference outputs are in `artifacts/reference/<model>/`: coefficients, WoE bins and breaks (scorecard),
  test-set predictions and scores, metrics, and seed and library versions.
- The deliberate documentation gaps for Stage 2 to find: the scorecard ships with a 2019 methodology note that
  doesn't match the code (4 variables/4 bins vs 14 variables/5 bins, different scaling). The fraud model has no
  documentation at all. The pricing model has a 2024 README. The SAS program has only a header comment.

**Failures and fixes**
- The OpenML Parquet endpoints returned HTTP 503. The OpenML ARFF downloads ran at about 60 KB/s and then
  stalled. I switched to the TensorFlow mirror (fraud) and the CASdatasets GitHub copy (freMTPL2), which both
  download in seconds (D14).
- I accidentally killed my own shell with `pkill -f` on a pattern that also matched the command line. No
  impact on the repo.

**Verification**
- `make test`: 32 passed (6 new tests). They recompute AUC and claim totals from the saved predictions and check
  the score scaling formula. They also confirm no protected attribute is a model input and that the SAS model
  has no outputs. Finally, they re-run the R scorecard and require byte-identical coefficients, bins and
  predictions. CI downloads German Credit (80 KB) for this.
- I re-ran the fraud and pricing models into a temp directory. Their predictions and coefficients were
  byte-identical to the committed reference.

**Token cost for Stage 1:** $0 (no LLM calls). Session `stages-0-3` total is still about $0.00004 of $7.00.

## Stage 2: Discover (2026-11-07)

**What was built**
- `factory.scanner`: static scan of every file in `models/<name>/`. Python via the AST; R and SAS via regular
  expressions. It records libraries, data inputs and outputs, model methods, seeds, lines of code, SAS librefs,
  PROCs and macro variables, and documentation files with the years they mention (D20).
- `factory.agents.inventory`: one Haiku call per model. The prompt holds the scan summary, the file text
  (capped at 15k chars per file, no data rows), the risk-tier rubric from config, and any Gate 1 reviewer
  feedback from a previous reject or comment. Every non-null answer must carry a quote and its source file;
  quotes not found in the files are nulled and listed under `rejected_agent_answers` (D21).
- `factory.discover`: the stage function, fixed gap rules (D22), the cross-model inventory
  (`artifacts/inventory/inventory.{md,csv,json}`) and the dependency map (`dependency_map.dot` / `.mmd`).
- UI: the inventory page shows the table, gaps and the Graphviz dependency map. The Discover gate review shows
  each value with its evidence quote, the contradictions found and any rejected answers.
- CLI / Makefile: `make run-all` (every model to its next gate) and `make inventory`.
- Config: `discover` section (as-of date, file cap, thresholds, rubric) and `llm.thinking` per agent (D24).

**Result (all four models waiting at Gate 1)**

| Model | Owner | Tier | Last validation | Docs | Gaps |
|---|---|---|---|---|---|
| `credit_scorecard` (RCS-APP-02) | Retail Credit Risk Modelling | high | 2021-03-15 | outdated, 3 contradictions | 5 |
| `fraud_gbm` (FRD-CARD-07) | unknown | unknown | unknown | missing | 4 |
| `pricing_glm` (PRC-MTPL-03) | Personal Lines Pricing | high | 2024-11 | outdated, 2 contradictions | 4 |
| `sas_behaviour_scorecard` (BHV-CC-01) | Cards Risk Analytics | medium | unknown | missing | 3 |

The agent found the gaps planted in Stage 1: the 2019 scorecard note disagrees with the code on variables,
bins and score scaling; the fraud model has no documentation or owner; the pricing README claims caps the code
doesn't apply. No answer was rejected by the evidence check.

**Failures and fixes**
- The scanner first tagged the R scorecard as an "offset" model because the word appeared in a comment. The
  pattern now requires `offset =`.
- First live run: 3 of 4 inventory replies were cut off mid-JSON at `max_tokens` 2000, even after one retry.
  The cause was default extended thinking using the output budget. Turning thinking off for extraction agents
  fixed it (D24); the four replies used 807-1,524 output tokens. The truncated replies were removed from the
  response cache; the ledger keeps their cost.

**Verification**
- `make test`: 47 passed (15 new: scanner per language, evidence grounding and rejection, gap rules,
  artifacts, JSON retry, failed-stage handling, reviewer feedback in the prompt, cache reuse, thinking setting
  per agent). `ruff` clean.
- Live run of all four models through the real API; cards validate against the schema.

**Token cost for Stage 2:** 12 inventory calls, 63,520 tokens, about $0.146 (of which $0.108 was the failed
first run). Session `stages-0-3` total: $0.1464 of $7.00.

## Stage 3: Document (2026-11-07, in progress)

**What was built**
- `factory.agents.documentation`: one Sonnet call per model. Input: code, documentation files, reference
  outputs (`metrics.json`, `coefficients.csv`; no row-level predictions), the Gate 1-approved inventory and any
  Gate 2 reviewer feedback. Output: six sections (purpose, data, methodology, implementation, performance,
  monitoring/governance) with confidence and quotes, plus variables, transformations, assumptions,
  limitations and unknowns. Evidence policy in D26.
- `factory.document`: stage function, coverage check of documented variables against the fitted model (D27),
  gap list, and Markdown rendering to `docs/models/<model>.md` with SS1/23 principle tags (D25).
- UI: the Gate 2 review shows section confidence, coverage, dropped claims and the rendered draft.
- `LLMError`: provider errors now fail the stage cleanly instead of crashing `make run-all` (D29).

**Result so far:** `credit_scorecard` documented and waiting at Gate 2. All sections high confidence except
monitoring/governance (medium); 14/14 model variables documented; 7 transformations; 0 claims dropped; 12
unknowns. The other three models are waiting at Gate 1, so Stage 3 has not run for them.

**Failures and fixes**
- Sonnet rejected `thinking: disabled` (400, "send between_tools instead"); config changed (D28). The CLI
  crashed on that error, which led to `LLMError` (D29).
- The first scorecard reply hit the 8000-token cap; the retry succeeded at 7,939 tokens. `max_tokens` raised to
  16000. The truncated reply was removed from the cache.

**Verification:** `make test` 55 passed (8 new); ruff clean.

**Token cost so far:** 2 Sonnet calls, 28,291 tokens, $0.276. Session `stages-0-3`: $0.423 of $7.00.
