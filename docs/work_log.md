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

**Review round 2 (reviewer `ap`, 2026-11-07 23:40-23:42 UTC)**
- Gate 1: `pricing_glm` approved; `fraud_gbm` and `sas_behaviour_scorecard` rejected with reason "For checking".
- Gate 2: `credit_scorecard` documentation rejected with reason "For checking".
- `make run-all` then re-ran each rejected stage with the reason in the prompt, and ran Document for
  `pricing_glm`: fraud and SAS inventory re-run (Haiku, $0.0065 and $0.0075) and back at Gate 1; scorecard
  documentation regenerated (Sonnet, $0.1545, 9,061 output tokens) and back at Gate 2; pricing documented
  (Sonnet, $0.1200) and at Gate 2.
- `pricing_glm` coverage: 8/9 model terms; the agent listed the raw input `Density`, while the fitted term is
  the derived `LogDensity`. The log transform is documented, so this is a naming mismatch the coverage check
  cannot resolve, and it is left as a flagged gap for the reviewer.

**Verification:** `make test` 55 passed; ruff clean.

**Token cost Stage 3 to date:** 6 calls, $0.565. Session `stages-0-3`: 20 calls, 126,814 tokens, $0.711 of $7.00.

**Review round 3 (reviewer `ap`, 2026-11-08 02:33-02:34 UTC)**
- Gate 1 approved for `fraud_gbm` and `sas_behaviour_scorecard`; Gate 2 approved for `credit_scorecard` and
  `pricing_glm` (no reasons given).
- Document run for fraud (Sonnet, $0.1048) and SAS (Sonnet, $0.1321); both at Gate 2.
  - Fraud: 30/30 fitted terms documented. Raw inputs `Amount` and `Time` are reported as not in the model
    because the fitted terms are the derived `log_amount` and `hour` (same naming limitation as pricing).
    Owner and risk tier remain gaps.
  - SAS: no reference outputs, so no coverage check; documented from source only and flagged non-executable.
- Stage 3 end state: all four models documented; scorecard and pricing approved at Gate 2; fraud and SAS
  awaiting Gate 2. `make run` for approved models now stops at `redevelop` (not implemented until Stage 4).

**Verification:** `make test` 55 passed; ruff clean.

**Stage 3 total token cost:** 8 calls, $0.802 (incl. $0.138 wasted on one truncated reply). Session
`stages-0-3`: 22 calls, 147,215 tokens, $0.948 of $7.00.

## Stage 4 - Redevelop (R scorecard -> Python)

**Budget:** new session bucket `stage-4`, $4.00 (user agreed by "go" on the proposed default). Per-run cap
raised to $3.00 to fit the worst case of 3 Sonnet + 2 Opus attempts.

**What was built**
- `factory.agents.translation`: Sonnet writes a Python module with a fixed interface (one function per
  transformation, plus `main()` that reproduces the R script's outputs). Prompt: R source, interface
  contract, first lines of the R reference outputs, Gate 2-approved transformation steps, Gate 3 feedback.
  No data rows. Static checks: syntax, import allow-list, required interface, no `eval`/`exec`.
- `r/oracle.R`: loads the legacy script's functions and constants without its data steps, so the same
  synthetic edge cases can be run through R and Python (18 cases, 8 transformations).
- `factory.redevelop_harness`: runs the cases against the generated module in a subprocess (no API
  keys in its environment, timeout).
- `factory.redevelop`: end-to-end parity on the original split (vs committed R reference outputs) and a
  resampled split (seed 7) the agent never saw, with R re-run; 19 checks per split. Self-healing loop:
  failures are fed back; after `max_retries_before_escalation` (3) failed attempts the escalation model
  (Opus) takes over, up to 5 attempts. Writes `redeveloped/credit_scorecard/scorecard.py`,
  `docs/parity_report.md`, `artifacts/redevelop/credit_scorecard/{attempts,parity}.json`.
- UI: Gate 3 shows attempts, unit tests per transformation, parity count, the report and the code.
- Fraud, pricing (Python) and SAS (no runtime) are marked not applicable for this stage.

**Live result:** attempt 1 (Sonnet) failed 5 checks, all the same bug: R's `cut()` labels the last bin
`(30, Inf]` with a space (from `formatC`), Python wrote `(30,Inf]`. The failures went back to the agent
and attempt 2 (Sonnet) passed everything: 18/18 unit cases, 38/38 parity checks. Max abs diff on the
original split: coefficients 3.4e-15, PD 1.5e-15, score 6.8e-13, AUC 4.4e-16. Opus was not needed.

**Failures and fixes**
- Opus rejects `thinking: between_tools` and `disabled`; it needs `adaptive` plus an effort level. Found
  with a tiny probe before any real Opus call. Added `thinking_escalation: {translation: adaptive/medium}`
  (D31). Two direct probe calls (about $0.0006) bypassed the ledger and are not in `make spend`.
- `itertuples()` renames `_merge`, which broke the bin-label comparison on the first evaluation; fixed
  before any retry was spent (the cached attempt-1 reply was re-evaluated for free).

**Verification:** `make test` all passing (new: `test_redevelop.py` with a fake LLM covering self-healing,
Opus escalation, give-up after max attempts; `test_redeveloped_scorecard.py` re-checks the committed rebuild
against R on every run). Ruff clean.

**Token cost:** 2 live Sonnet calls, 22,671 tokens, $0.161 of the $4.00 `stage-4` bucket.

## Stage 5 - Validate (2026-11-08)

**Budget:** new session bucket `stage-5`, $2.00; per-run cap lowered to $1.00 (user agreed by "go" on the
proposed default).

**What was built**
- `factory.validate`: deterministic evidence builders per model type (`validate.models` in config):
  - `scorecard_rebuild` (credit scorecard): validates the Gate 3-approved Python rebuild. It runs its
    `main()`, re-scores train and test with the rebuild's bins, WoE and coefficients, and checks that the
    re-scoring reproduces its test predictions (max diff 0 on the live run) before any metric is used.
  - `binary_classifier` (fraud GBM): committed reference test predictions + data.
  - `poisson_frequency` (pricing GLM): committed reference predictions, exposure and claims.
  - SAS: not applicable (no runtime, no outputs).
- Checks: performance (AUC with 500-sample bootstrap CI, Gini, KS, train-test AUC drop; pricing: A/E,
  Poisson deviance, ordered-Lorenz Gini), calibration (mean predicted vs observed, Hosmer-Lemeshow,
  calibration deciles; pricing: A/E by predicted decile, `Area`, `VehGas`), stability (score PSI, CSI per
  selected variable, synthetic out-of-time tranches; feature PSI for fraud and pricing), fairness
  (Fairlearn `MetricFrame` by `sex`, `age_group`, `foreign_worker`: adverse impact ratio of approval rates,
  equalized odds difference, per-group AUC and bad rate; pricing: A/E by driver age band). Groups below
  30 records are reported and flagged amber, not rated.
- RAG: every threshold is a `[green, amber]` pair in `validate.thresholds`; section rating = worst check,
  overall = worst section.
- `factory.agents.report` (Sonnet, one call per model): writes the executive summary, section narrative,
  findings and recommendations. It gets the facts table, checks, notes and the Gate 1/2 card context, no
  data rows. It writes numbers as `[[fact]]` placeholders; sentences with a typed decimal number or
  percentage, or an unknown placeholder, are dropped and counted (0 dropped on the live run).
- Outputs: `docs/validation/<model>.html` (self-contained, charts embedded) and `.pdf` (Matplotlib
  `PdfPages`: summary page + one page per chart), `artifacts/validation/<model>/evidence.json` + charts,
  and `card.validation` (ratings, checks, findings, report paths, LLM cost).
- UI: new *Validation reports* page; Gate 4 review shows the checks table, download buttons and the
  embedded report. `make run` without a terminal now defers the gate to the UI instead of crashing.

**Live result**

| Model | Overall | Performance | Calibration | Stability | Fairness |
|---|---|---|---|---|---|
| credit_scorecard | AMBER | GREEN (AUC 0.801, CI 0.745-0.853) | GREEN (HL p 0.90) | AMBER (worst synthetic tranche PSI 0.130) | AMBER (age_group EOD 0.132; foreign_worker A202 n=9) |
| pricing_glm | AMBER | GREEN (A/E 0.986) | AMBER (worst decile A/E dev 0.159) | GREEN | AMBER (worst driver age band A/E dev 0.125) |

Computation took 1.0 s (scorecard) and 1.5 s (pricing). Fraud was not run through the pipeline: its Gate 2
(documentation) is still awaiting review. Its evidence builder was exercised offline: AUC 0.986, calibration
amber (mean predicted 19% off the observed rate), stability red (feature `hour` PSI 5.2, because the
out-of-time test window only contains hours 16-23), fairness not assessable.

**Failures and fixes**
- Raising the live caps would have leaked into tests: the redevelop test hit the new $1 per-run cap with
  fake token estimates. Tests now set their own generous caps in `conftest.py`.
- The LLM thinking test assumed `report` had no thinking setting; it now expects `between_tools`.
- `make run` from a non-interactive shell raised `EOFError` at the gate prompt (card already saved);
  the CLI now defers to the UI.

**Verification:** `make test` all passing (new `test_validate.py`: PSI, Hosmer-Lemeshow, Poisson deviance,
Lorenz Gini, RAG, number grounding, SAS not applicable, scorecard end to end with a fake LLM including
HTML/PDF output, pricing evidence vs reference metrics). Ruff clean. HTML report checked visually in a
headless browser.

**Token cost:** 2 live Sonnet calls, 8,394 tokens, $0.069 of the $2.00 `stage-5` bucket.


## Stage 6 - Monitor (2026-11-08)

**Start state.** Gate 4 approved (audit log, reviewer "ap") for `credit_scorecard` and
`pricing_glm`; `make run --no-prompt` recorded both as complete. `fraud_gbm` still waits at Gate 2.

**Built**
- `src/factory/monitor.py`: 12 simulated monthly batches of 1,000 rows, resampled from the test
  set (seed 42 + month). From month 7 synthetic drift is injected: credit amounts inflate 4% per
  month, a growing share of loans (8% per month) gets 12 months longer terms, and intake tilts
  towards bad accounts. Per month: score PSI vs the development (train) scores of the approved
  rebuild, CSI per selected variable, calibration (|mean PD / bad rate - 1|) and AUC, rated with
  the Stage 5 thresholds. Rule: a red month or 2 amber months in a row triggers re-validation.
- On trigger: the breaching batch is saved, the monitor agent (Haiku, placeholders only) writes
  the alert, an event goes to `artifacts/audit/monitoring_events.jsonl`, Stage 5 is re-run on the
  batch (reports suffixed `_revalidation_mXX`, original report kept) and Gate 4 re-opens.
  A second run on the same breach does not re-trigger; when Gate 4 approves, the next monitor run
  closes the re-validation and logs `revalidation_approved`.
- `make monitor MODEL=<name>` / `factory monitor`; monthly metrics are logged to MLflow (step = month).
- Streamlit "Drift monitoring" page: trigger, dashboard, monthly table, alert, re-validation status,
  events. Gate review shows the re-validation report as the Gate 4 item.
- `tests/test_monitor.py` (5 tests): trigger rule, batch determinism and drift, end-to-end trigger
  with grounding and idempotence, no drift -> no trigger, Gate 4 prerequisite.

**Calibrating the simulation (honest record).** First attempt (300-row batches, duration +2 months
per month for every loan) was unusable: sampling noise alone made months 1-3 amber and triggered
at month 2, and the uniform duration shift jumped straight to red because German Credit durations
sit on the bin breaks. Fixed by 1,000-row batches and a "share of longer loans" drift. Months 1-7
are now all green.

**Live result (credit_scorecard).** Months 1-7 GREEN, 8-9 AMBER, 10-12 RED. Trigger at month 9
(2 consecutive amber): largest CSI 0.134 (duration) and calibration deviation 0.154. AUC stayed
green (0.77-0.81), so the drift shows in stability and calibration, not ranking. Re-validation on
the month-9 batch: overall **RED** (Hosmer-Lemeshow p < 0.001; calibration, stability, fairness
amber; performance green). The report agent dropped one sentence ("95%" typed).

**Verification.** 76 tests + ruff pass; Streamlit AppTest renders Drift monitoring, Gate review and
Validation reports pages without exceptions.

**Spend.** Bucket `stage-6` ($1 cap): 2 live calls (Haiku alert + Sonnet re-validation report),
8,678 tokens, $0.0498.

## Stage 7 - Evidence and demo (2026-11-08)

**Start state.** The user replied "approve it and go to stage 7". The Gate 4 re-validation approval
for `credit_scorecard` was recorded via the CLI (reviewer "ap", reason states it came from the chat
instruction). `make run` marked the scorecard complete; the next `make monitor` closed the
re-validation and logged `revalidation_approved` without re-triggering.

**Plan correction.** I first told the user I would use published figures for the manual baseline.
The brief says the user supplies it and it must never be invented, so I reverted: the baseline is
`config/manual_baseline.yaml`, shipped empty, and the user was told.

**Built**
- `src/factory/benchmark.py`, `make benchmark`: per model and stage, machine time = deterministic
  work re-executed and timed (scanner; R oracle + R on the reshuffled split + all parity checks on
  the approved rebuild; validation evidence) + live LLM latency from the ledger (all calls, incl.
  failed attempts and reruns). Outputs `docs/benchmark.md`, `artifacts/benchmark/benchmark.json`
  and a chart. Manual comparison only where the user supplied figures.
- Consistency table across all four models and reproducibility checks: parity re-check, validation
  evidence rebuilt twice and compared with the stored Stage 5 evidence.
- Streamlit "Benchmark" page; `docs/demo_script.md` (5 min), `docs/slides_outline.md`, Mermaid
  architecture diagram in `docs/architecture.md`.
- `tests/test_benchmark.py` (5 tests), including one that fails if invented figures are committed to
  the baseline template.

**Bug found while building.** The first benchmark run reported the parity re-check as failed with 0
unit cases: `evaluate()` reads `cases.json` from its work dir's parent, and I had passed the work dir
itself. Fixed to mirror Stage 4; re-check now 18/18 unit cases and 38/38 parity checks.

**Result.** Machine time: credit_scorecard 232 s (all four stages; 202 s of it LLM latency,
incl. a truncated first documentation reply and the rejection rerun), fraud 61 s and SAS 72 s
(Discover + Document only), pricing 73 s (no Redevelop). Validation evidence for both validated
models is bit-identical across two rebuilds and equal to the stored Stage 5 evidence.

**Verification.** 81 tests + ruff pass; Streamlit AppTest renders Benchmark, Drift monitoring and
Inventory pages without exceptions.

**Spend.** Bucket `stage-7` ($1 cap): 0 LLM calls, $0.
