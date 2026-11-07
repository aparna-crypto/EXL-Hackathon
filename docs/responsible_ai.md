# Responsible AI and audit trail

*Started in Stage 0. Extended as each stage is added.*

## Human gates
- Every pipeline stage (Discover, Document, Redevelop, Validate) ends in a gate. The orchestrator does not
  start the next stage until the latest decision for the current stage is **approve**.
- Decisions are `approve`, `reject` or `comment`. A reviewer name is mandatory, and reject or comment also needs a reason.
- Decisions are appended to `artifacts/audit/gate_decisions.jsonl`: timestamp (UTC), model, stage,
  decision, reviewer, reason and model card version. They are also logged to MLflow. Entries are never edited.
- A rejected stage is re-executed on the next run and needs a fresh decision. Older decisions do not carry over.

## LLM use
- Every agent call goes through `LLMClient`. Each call is logged to MLflow (prompt, output, model, duration,
  tokens, cost) and to the cost ledger.
- Hard budgets cap spend per run and per session.
- Data handling: full datasets are never sent to the LLM, only code, schemas and aggregate summaries.
  Only public or synthetic data is used.
- The report agent may describe only numbers computed by code (enforced from Stage 5).

## Secrets
- `ANTHROPIC_API_KEY` is read from the environment or `.env` (gitignored) only. It is never logged or committed. CI uses the fake LLM and has no secrets.

## Mapping to SS1/23 principles
*To be completed in Stage 7 once all stages exist.*
