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
