# Limitations (honest list)

- **LLM prices are assumptions.** Costs use conservative per-token prices in `config/config.yaml` (D8),
  not verified published prices. Reported costs are estimates.
- **No temperature control.** The `claude-*-5-5` models reject `temperature`, so repeat outputs are made
  consistent through the response cache, not by sampling settings.
- **Gate identity is self-declared.** Reviewers type their name; there is no authentication in the
  hackathon build. Production would use SSO identities.
- **Audit log is a local file**, not tamper-proof storage.
- **SAS is not executed** (no SAS runtime). The SAS model is inventoried and documented only (Stage 2-3).
