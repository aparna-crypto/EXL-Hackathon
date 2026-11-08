# Slides outline

1. **Problem.** ~250 credit, fraud and pricing models in SAS/R/Python; stale documentation;
   independent validation takes 8-12 weeks (challenge brief).
2. **Approach.** One pipeline, five stages (Discover, Document, Redevelop, Validate, Monitor),
   a human gate after each, one JSON model card per model. Architecture diagram from
   `docs/architecture.md`.
3. **Guardrails.** Code computes every number; agents only extract, draft and explain. Evidence
   quotes are checked against source files; narrative numbers are placeholders filled by code;
   no data rows go to the LLM; hard token and dollar budgets; append-only gate log.
4. **Discover.** Inventory table and dependency map for 4 models; planted gaps found; fraud owner
   and tier left unknown instead of guessed.
5. **Document.** SS1/23-tagged drafts with confidence and quotes; variable coverage check;
   unknowns listed. Known false gap from name matching (Density vs LogDensity).
6. **Redevelop.** R -> Python with self-healing loop: 2 attempts, 18/18 unit cases, 38/38 parity
   checks on two splits, max diff ~1e-15. Opus escalation ready but not needed.
7. **Validate.** RAG report per model (HTML + PDF): scorecard AMBER, pricing AMBER; fairness via
   Fairlearn; limits of a 300-row test set and synthetic out-of-time split.
8. **Monitor.** Drift dashboard: green to month 7, trigger at month 9, re-validation RED,
   Gate 4 re-opened and re-approved by a human.
9. **Evidence.** `docs/benchmark.md`: machine time per model and stage; reproducibility (parity
   and validation re-run identical); consistency table across all four models; manual
   comparison from the bank-supplied baseline; LLM spend ~$1.23 in total.
10. **Limitations and next steps.** Public/synthetic data, illustrative thresholds, SAS not
    executable, Azure ML path configured but not exercised; next: real production feeds,
    SAS runtime, policy thresholds, Azure deployment.
