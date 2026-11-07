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
