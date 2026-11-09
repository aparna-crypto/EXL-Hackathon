# Benchmark (Stage 7)

Generated 2026-11-09T00:02:13.313781+00:00 by `make benchmark`. Machine time = deterministic work re-executed and timed in this run + live LLM latency recorded in the ledger (all live calls for that model and stage, including failed attempts and reruns triggered by gate rejections). Human review time is not measured.

## Machine time per model (seconds)

| Model | discover | document | redevelop | validate | Total | LLM calls | LLM cost |
|---|---|---|---|---|---|---|---|
| credit_scorecard | 23.1 (0.0 code + 23.1 LLM) | 137.7 (0.0 code + 137.7 LLM) | 41.6 (3.1 code + 38.6 LLM) | 30.4 (1.4 code + 29.0 LLM) | 232.9 | 10 | $0.714 |
| fraud_gbm | 23.5 (0.0 code + 23.5 LLM) | 37.5 (0.0 code + 37.5 LLM) | - | - | 61.0 | 5 | $0.143 |
| pricing_glm | 21.6 (0.0 code + 21.6 LLM) | 37.4 (0.0 code + 37.4 LLM) | - | 14.5 (1.7 code + 12.8 LLM) | 73.4 | 5 | $0.191 |
| sas_behaviour_scorecard | 25.4 (0.0 code + 25.4 LLM) | 46.5 (0.0 code + 46.5 LLM) | - | - | 71.9 | 5 | $0.173 |

![machine time](../artifacts/benchmark/machine_time.png)

## Manual comparison

**Source of manual figures: Illustrative estimates by Devin (AI) at the user's request, 2026-11-08 - not measured; only the validation figure is anchored to the challenge brief (8-12 weeks)**

- discover: Assumption: one analyst-day less a couple of hours for a model with existing code headers.
- document: Assumption: one working week (37.5 h, rounded) per model.
- redevelop: Assumption: two working weeks per model, including writing parity tests.
- validate: Brief: independent validation takes 8-12 weeks elapsed. Assumed 8 weeks x 37.5 h x 50% allocation = 150 h (low end of the brief).
- review_minutes_per_gate: Assumption.

| Model | Stage | Machine (s) | Review (h) | Automated (h) | Manual (h) | Time saved |
|---|---|---|---|---|---|---|
| credit_scorecard | discover | 23.1 | 0.5 | 0.506 | 6.0 | 91.6% |
| credit_scorecard | document | 137.7 | 1.0 | 1.038 | 40.0 | 97.4% |
| credit_scorecard | redevelop | 41.6 | 0.5 | 0.512 | 80.0 | 99.4% |
| credit_scorecard | validate | 30.4 | 1.0 | 1.008 | 150.0 | 99.3% |
| fraud_gbm | discover | 23.5 | 1.0 | 1.007 | 6.0 | 83.2% |
| fraud_gbm | document | 37.5 | 0.5 | 0.51 | 40.0 | 98.7% |
| pricing_glm | discover | 21.6 | 0.5 | 0.506 | 6.0 | 91.6% |
| pricing_glm | document | 37.4 | 0.5 | 0.51 | 40.0 | 98.7% |
| pricing_glm | validate | 14.5 | 0.5 | 0.504 | 150.0 | 99.7% |
| sas_behaviour_scorecard | discover | 25.4 | 1.0 | 1.007 | 6.0 | 83.2% |
| sas_behaviour_scorecard | document | 46.5 | 0.5 | 0.513 | 40.0 | 98.7% |

## Consistency across models

Every model goes through the same schema, scanner, agents, evidence check and thresholds. The table shows what each produced.

| Model | Lang | Inventory fields found | Quotes | Gaps | Doc sections (high conf.) | Claims dropped | Unknowns | Variable coverage | Validation checks | Overall | Gate decisions (rejections) |
|---|---|---|---|---|---|---|---|---|---|---|---|
| credit_scorecard | R | 6/6 | 8 | 5 | 6 (4) | 0 | 10 | 14/14 | 12 | RED | 6 (1) |
| fraud_gbm | Python | 3/6 | 4 | 4 | 6 (4) | 0 | 9 | 30/30 | - | - | 2 (1) |
| pricing_glm | Python | 6/6 | 8 | 4 | 6 (4) | 0 | 8 | 8/9 | 6 | AMBER | 3 (0) |
| sas_behaviour_scorecard | SAS | 5/6 | 6 | 3 | 6 (5) | 0 | 12 | n/a (not executed) | - | - | 2 (1) |

## Reproducibility

- credit_scorecard parity re-check: 38/38 parity checks and 18 unit cases, passed = True.
- credit_scorecard validation evidence rebuilt twice: identical = True; equal to the stored Stage 5 evidence = True (26 metrics, digest f262ab01b797).
- pricing_glm validation evidence rebuilt twice: identical = True; equal to the stored Stage 5 evidence = True (14 metrics, digest c155e99249d9).

LLM outputs are not re-generated here: they are cached by prompt hash, so a rerun replays the approved text for free. Without the cache the documentation agent can phrase things differently; the evidence check and the gates are the consistency control for that part.
