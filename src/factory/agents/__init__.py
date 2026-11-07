"""LLM agents. Each agent calls the model only through `factory.llm.LLMClient`."""


class AgentError(RuntimeError):
    """An agent could not produce a usable answer (e.g. unparseable output after a retry)."""
