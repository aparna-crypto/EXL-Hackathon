# Limitations (honest list)

- **LLM prices are assumptions.** Costs use conservative per-token prices in `config/config.yaml` (D8),
  not verified published prices. Reported costs are estimates.
- **No temperature control.** The `claude-*-5-5` models reject `temperature`, so repeat outputs are made
  consistent through the response cache, not by sampling settings.
- **Gate identity is self-declared.** Reviewers type their name; there is no authentication in the
  hackathon build. Production would use SSO identities.
- **Audit log is a local file**, not tamper-proof storage.
- **SAS is not executed** (no SAS runtime). The SAS model is inventoried and documented only (Stage 2-3).
- **German Credit has no application date.** The Stage 5 "out-of-time" back-test for the scorecard
  can only use a synthetic time ordering. It is labelled as such and is not a true out-of-time test.
- **Legacy models are synthetic.** No EXL sample code was supplied. The owners, model IDs and dates in the model
  headers are invented for the demo (see `models/README.md`).
- **freMTPL2 version.** The CASdatasets copy has 677,991 rows, which is 22 fewer than the older OpenML copy
  (678,013), and it uses region names rather than codes.
- **German Credit is small** (1,000 rows; 300 in the test set), so metric confidence intervals are wide.
- **Scanner is pattern-based for R and SAS.** Python uses the AST; R and SAS use regular expressions, so unusual
  code styles (computed file paths, macro-generated dataset names) can be missed. The scan summary lists what
  was found so the reviewer can spot omissions.
- **Doc/code contradictions come from the LLM.** Grounding checks confirm that quoted evidence exists in the
  files, but not that the LLM's reading of it is right. They are presented as findings for the Gate 1 reviewer,
  not as facts. Example: one scorecard finding mixes a consistent point (validation date) with a real one (dummy
  bins vs WoE).
- **Risk tier for the fraud model is `unknown`**: its header records no owner, use or validation, so the agent
  correctly declined to guess. The reviewer is expected to supply it at Gate 1.
- **SS1/23 tags are a topic mapping, not a compliance claim.** Generated documentation is a draft for a
  human reviewer (Gate 2) and does not by itself meet SS1/23 documentation standards.
- **No data dictionaries.** Variable meanings in generated docs are inferred from column names and are
  labelled as such by the agent.
- **JSON retry re-sends the same token budget.** If a reply is cut off at `max_tokens`, the single retry
  is likely to be cut off too; the budget is set with headroom instead (D28).
- The Stage 3 coverage check matches variable names literally. A documented raw input whose fitted term is a
  derived column (pricing `Density` vs `LogDensity`) is reported as both missing and extra.

## Stage 4
- The interface contract, unit cases and parity checks are specific to WoE scorecards. Another R model
  type would need its own contract and cases.
- Parity is proven on German Credit, which has no missing values; R vs Python behaviour on NA inputs is
  untested.
- The subprocess is not a security sandbox: generated code can still read files the user can read. The
  import allow-list and stripped environment reduce, not remove, that risk.
- The Opus escalation path is tested with a fake LLM only; the live run passed on Sonnet's second attempt.

## Stage 5
- **Synthetic out-of-time.** German Credit has no dates; the scorecard's OOT tranches are id-ordered
  slices of the 300-row test set (100 rows each), so their PSI is noisy and not evidence of real drift.
- **Small samples.** The scorecard test set has 300 rows (AUC CI 0.745-0.853); `foreign_worker=A202` has 9
  test records and is not rated.
- **Fraud and pricing use the legacy reference predictions.** Development-sample predictions are not
  kept, so there is no score PSI for them (feature PSI instead) and the fraud train AUC comes from the
  legacy metrics file.
- **Fraud fairness not assessable:** the data has only anonymised PCA features, amount and time.
- **Fraud was not validated through the pipeline:** its Gate 2 (documentation) is still awaiting review.
- **Thresholds are illustrative**, not a bank's model risk policy; they sit in config for the reviewer.
- **Narrative findings can repeat upstream errors.** The pricing report repeats the Stage 3
  `Density`/`LogDensity` name-matching false gap as a low-severity finding. Numbers in the narrative are
  always filled from evidence; interpretation still needs the Gate 4 reviewer.
- **The PDF is a summary** (ratings, checks, findings, charts); the full report with tables and
  narrative is the HTML.

## Stage 6 (monitoring)

- **Simulated data.** Monthly batches are bootstrap resamples of the 300-row test set with
  synthetic drift; they demonstrate the loop, not real drift behaviour. Resampling repeats rows.
- **Outcomes assumed known at batch time.** Real credit outcomes mature over 12+ months, so
  calibration/AUC monitoring would lag; PSI/CSI do not need outcomes.
- **Only the credit scorecard is monitored.** Pricing and fraud would need their own simulators.
- **The re-validation reuses the Stage 5 builder** on the breaching batch, including the
  synthetic "tranches" by id order, which are not meaningful on a resampled batch.
- **Thresholds and drift parameters are illustrative.**

## Stage 7 (benchmark)

- **No manual comparison until the user supplies the baseline.** `config/manual_baseline.yaml`
  is empty, so no time saving is claimed.
- **Human review time is not measured.** Gate timestamps show when a decision was made, not how
  long the reviewer spent; `review_minutes_per_gate` in the baseline file adds it when supplied.
- **LLM latency is historical** (from the ledger, this VM, these model IDs) and includes failed
  attempts and reruns; it would differ with other models, load or network.
- **Fraud and SAS have only Discover and Document times**: fraud is waiting at Gate 2 and SAS has
  no runtime, so their totals are not comparable with the scorecard's.
- **Four models is a small sample** for a "consistency" claim; the table shows the same process
  ran, not that quality is equal.
