# Demo script (5 minutes)

Setup before the demo: `make ui` running (Streamlit), a terminal in the repo, and
`docs/validation/credit_scorecard.html` open in a browser tab. Everything shown is already
generated and committed; nothing needs a live LLM call (the cache replays it for free).

| Time | Show | Say |
|---|---|---|
| 0:00-0:30 | README status line, `config/config.yaml` | "A Tier-1 bank has ~250 models in SAS, R and Python; validation takes 8-12 weeks. This factory runs every model through the same five agent-assisted stages, each ending at a human gate. Four sample models: an R credit scorecard, a Python fraud GBM, a Python pricing GLM and a SAS scorecard." |
| 0:30-1:15 | UI > **Inventory** | "Stage 2 scans the code without running it, then a Haiku agent fills the inventory. Every value must quote its source file or it is dropped. It found the planted gaps: outdated 2019 note, fraud model with no owner or docs." Point at the dependency map. |
| 1:15-1:50 | UI > **Gate review** (any model), then **Audit log** | "Every stage stops here. Approve, reject or comment; a rejection re-runs the stage with the reviewer's reason in the prompt. Decisions go to an append-only log, which is the source of truth, not the card." |
| 1:50-2:20 | `docs/models/credit_scorecard.md` | "Stage 3: Sonnet drafts SS1/23-aligned documentation from code and old docs; each section has a confidence level and quotes; 14/14 model variables covered; it lists what it couldn't establish instead of guessing." |
| 2:20-3:05 | `docs/parity_report.md`, `redeveloped/credit_scorecard/scorecard.py` | "Stage 4: the R scorecard rebuilt in Python. Attempt 1 failed five checks on a bin-label space; the failures went back to the agent and attempt 2 passed all 18 unit cases and 38 parity checks, on the original split and a reshuffled split the agent never saw. Max coefficient difference 3e-15." |
| 3:05-3:45 | UI > **Validation reports** (scorecard) | "Stage 5: performance, calibration, stability and fairness computed by code, rated by config thresholds. The report agent only writes narrative with placeholders, so it can't invent numbers. Scorecard: AMBER, age-group equalized-odds gap, foreign-worker group too small to rate." |
| 3:45-4:25 | UI > **Drift monitoring** | "Stage 6: twelve simulated months with drift injected from month 7. Two amber months triggered at month 9; validation re-ran on that batch, came out RED on calibration, and Gate 4 re-opened for a human." |
| 4:25-5:00 | UI > **Benchmark**, **LLM spend** | "Machine time per model is about 1-4 minutes, almost all LLM latency; re-running validation reproduces the stored metrics exactly. Total LLM spend for the whole build was about $1.23. The manual comparison uses the bank's own baseline figures, which the factory never invents." |

Fallbacks: if the UI is down, the same content is in `docs/` (Markdown, HTML, PDF). If asked
"is it real data?" - public German Credit, ULB fraud and freMTPL2 data; monitoring batches are
synthetic and labelled as such.
