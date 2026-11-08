# Parity report: credit_scorecard (R -> Python)

**Result: PARITY PROVEN** - pending Gate 3 review.

- Original: `models/credit_scorecard/scorecard.R` (R)
- Rebuild: `redeveloped/credit_scorecard/scorecard.py` (sha256 `dc0f6c4e40ed`)
- Generated at 2026-11-08T02:43:17.858972+00:00 by the translation agent; checks are deterministic.
- Reproduce: `make test` (runs `tests/test_redeveloped_scorecard.py`), or `make run MODEL=credit_scorecard` to re-run the stage (cached replies cost nothing).

## Self-healing attempts

| # | Model | Escalated | Failures | Tokens | Cost (USD) | Cached |
|---|---|---|---|---|---|---|
| 1 | claude-sonnet-5-5 | no | 5 | 0 | 0.0000 | yes |
| 2 | claude-sonnet-5-5 | no | 0 | 0 | 0.0000 | yes |

Attempt 1 failures (fed back to the agent):

- unit test assign_bin (assign_bin_7): args {"x": [4, 12, 13, 15, 24, 25, 30, 31, 72], "breaks": ["-Inf", 12, 15, 24, 30, "Inf"]} -> differs at [7] R (30, Inf] vs Python (30,Inf]; [8] R (30, Inf] vs Python (30,Inf]
- unit test assign_bin (assign_bin_8): args {"x": [250, 1248.2, 1248.3, 1919, 2788.8, 4738, 18424], "breaks": ["-Inf", 1248.2, 1919, 2788.8, 4738, "Inf"]} -> differs at [6] R (4738, Inf] vs Python (4738,Inf]
- unit test assign_bin (assign_bin_9): args {"x": [0.5, 15857.5, 2000000.5], "breaks": ["-Inf", 15857.5, 1000000, "Inf"]} -> differs at [2] R (1000000, Inf] vs Python (1000000,Inf]
- parity [original] bin labels per variable: mismatched bins: age '(44, Inf]' (R only); age '(44,Inf]' (Python only); credit_amount '(4738, Inf]' (R only); credit_amount '(4738,Inf]' (Python only); duration '(30, Inf]' (R only); duration '(30,Inf]' (Python only)
- parity [resampled] bin labels per variable: mismatched bins: age '(45, Inf]' (R only); age '(45,Inf]' (Python only); credit_amount '(4677.2, Inf]' (R only); credit_amount '(4677.2,Inf]' (Python only); duration '(30, Inf]' (R only); duration '(30,Inf]' (Python only)

## Transformation unit tests (R oracle vs Python, synthetic edge cases)

| Transformation | Function | Cases passed |
|---|---|---|
| Numeric vs categorical rule (>10 distinct values) | `is_binned_numeric` | 4/4 |
| Quantile break points (type 7, outer -Inf/Inf) | `numeric_breaks` | 3/3 |
| Bin assignment and R cut() labels | `assign_bin` | 5/5 |
| WoE and IV per bin (0.5 smoothing) | `woe_table` | 2/2 |
| WoE substitution (unseen level -> 0) | `apply_woe` | 1/1 |
| Score scaling (600 at 50:1, PDO 20) | `to_score` | 1/1 |
| AUC (rank-based, ties) | `auc` | 1/1 |
| KS statistic (ties) | `ks` | 1/1 |

IV selection and the logistic regression fit are tested end to end below (selected variables, coefficients).

## End-to-end parity

`original` = the committed R reference outputs on the fixed train/test split. `resampled` = a reshuffled split (seed 7) that the agent never saw, with R re-run on it.

| Split | Check | Max abs diff | Tolerance | Pass |
|---|---|---|---|---|
| original | bin labels per variable |  |  | yes |
| original | good/bad counts per bin |  |  | yes |
| original | WoE per bin | 3.11e-15 | 1.00e-09 | yes |
| original | IV per variable | 4.02e-16 | 1.00e-09 | yes |
| original | IV selection (selected variables, order) |  |  | yes |
| original | numeric break points |  |  | yes |
| original | coefficient terms (names, order) |  |  | yes |
| original | coefficient estimates | 3.44e-15 | 1.00e-06 | yes |
| original | coefficient standard errors | 6.66e-16 | 1.00e-05 | yes |
| original | prediction rows (id, bad, order) |  |  | yes |
| original | predicted PD | 1.50e-15 | 1.00e-06 | yes |
| original | score | 6.82e-13 | 1.00e-04 | yes |
| original | score distribution: deciles | 2.27e-13 | 1.00e-04 | yes |
| original | score distribution: 20-point band counts |  |  | yes |
| original | AUC (train, test) | 4.44e-16 | 1.00e-09 | yes |
| original | GINI (train, test) | 2.22e-16 | 1.00e-09 | yes |
| original | KS (train, test) | 3.33e-16 | 1.00e-09 | yes |
| original | BAD_RATE (train, test) | 0.00e+00 | 1.00e-09 | yes |
| original | N (train, test) | 0.00e+00 | 1.00e-09 | yes |
| resampled | bin labels per variable |  |  | yes |
| resampled | good/bad counts per bin |  |  | yes |
| resampled | WoE per bin | 3.11e-15 | 1.00e-09 | yes |
| resampled | IV per variable | 5.00e-16 | 1.00e-09 | yes |
| resampled | IV selection (selected variables, order) |  |  | yes |
| resampled | numeric break points |  |  | yes |
| resampled | coefficient terms (names, order) |  |  | yes |
| resampled | coefficient estimates | 6.00e-15 | 1.00e-06 | yes |
| resampled | coefficient standard errors | 8.05e-16 | 1.00e-05 | yes |
| resampled | prediction rows (id, bad, order) |  |  | yes |
| resampled | predicted PD | 1.55e-15 | 1.00e-06 | yes |
| resampled | score | 6.82e-13 | 1.00e-04 | yes |
| resampled | score distribution: deciles | 5.68e-13 | 1.00e-04 | yes |
| resampled | score distribution: 20-point band counts |  |  | yes |
| resampled | AUC (train, test) | 3.33e-16 | 1.00e-09 | yes |
| resampled | GINI (train, test) | 3.33e-16 | 1.00e-09 | yes |
| resampled | KS (train, test) | 3.33e-16 | 1.00e-09 | yes |
| resampled | BAD_RATE (train, test) | 3.89e-16 | 1.00e-09 | yes |
| resampled | N (train, test) | 0.00e+00 | 1.00e-09 | yes |

## Score distribution (original split, test set)

| Statistic | R | Python |
|---|---|---|
| mean | 521.595840 | 521.595840 |
| std | 40.473642 | 40.473642 |
| P10 | 469.153395 | 469.153395 |
| P20 | 484.926264 | 484.926264 |
| P30 | 499.096538 | 499.096538 |
| P40 | 509.609211 | 509.609211 |
| P50 | 521.854781 | 521.854781 |
| P60 | 534.090905 | 534.090905 |
| P70 | 546.696287 | 546.696287 |
| P80 | 557.397330 | 557.397330 |
| P90 | 575.405013 | 575.405013 |

| Score band | R count | Python count |
|---|---|---|
| 420-440 | 6 | 6 |
| 440-460 | 14 | 14 |
| 460-480 | 28 | 28 |
| 480-500 | 43 | 43 |
| 500-520 | 55 | 55 |
| 520-540 | 51 | 51 |
| 540-560 | 48 | 48 |
| 560-580 | 34 | 34 |
| 580-600 | 14 | 14 |
| 600-620 | 6 | 6 |
| 620-640 | 1 | 1 |

## Notes

- Tolerances are absolute and set in `config/config.yaml` (`redevelop.tolerance`). Labels, counts, selected variables, term names and row ids must match exactly.
- R's `glm()` and statsmodels both use IRLS but stop at slightly different convergence points, so coefficients agree to about 1e-8, not bit for bit.
- Generated code runs in a subprocess with a timeout and without API keys in its environment, after a static import allow-list check.
- Not covered: R-specific behaviour on inputs outside these cases (e.g. missing values, which German Credit does not contain).
