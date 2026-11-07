# Model inventory sources

One folder per model: `models/<model_id>/` containing its original source (`.R`, `.py`, `.sas`) and
any documentation that came with it. `make run MODEL=<model_id>` picks the folder up automatically.

These "legacy" models are **synthetic examples** written for the hackathon (no EXL sample code was
supplied). Owners, model IDs and dates in their headers are invented to look like a real
inventory. That includes some deliberately missing or outdated documentation, so the Discover
stage has real gaps to find. All data is public.

| Folder | Language | Data | Runs here? | Documentation that ships with it |
|---|---|---|---|---|
| `credit_scorecard` | R | UCI German Credit | yes | outdated 2019 methodology note (doesn't match the code) |
| `fraud_gbm` | Python | ULB credit-card fraud | yes | none |
| `pricing_glm` | Python | freMTPL2 claim frequency | yes | README, 2024 |
| `sas_behaviour_scorecard` | SAS | hypothetical warehouse tables | **no** (no SAS runtime) | header comment only |
