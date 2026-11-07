"""LLM factory.

Two "roles" keep cost and latency under control:

* ``strong`` — the specialist agents (knowledge / triage / release notes) that must
  reason over long evidence and call tools well. Defaults to Claude Sonnet.
* ``fast`` — the supervisor router and the reviewer/judge. These are short,
  structured-output calls where a smaller model is cheaper and plenty accurate.
  Defaults to Claude Haiku.

Agents receive the LLM by dependency injection (``build_graph(llm_strong=...)``),
which is what lets the test-suite run with fake models and no API key.
"""

from __future__ import annotations

from typing import Literal

from langchain_core.language_models import BaseChatModel

from .config import get_settings

Role = Literal["strong", "fast"]


def get_llm(role: Role = "strong") -> BaseChatModel:
    from langchain_anthropic import ChatAnthropic  # imported lazily: keeps tests light

    s = get_settings()
    if not s.anthropic_api_key:
        raise RuntimeError(
            "ANTHROPIC_API_KEY is not set. Copy .env.example to .env and add your key, "
            "or run the offline parts only (ingest, eval retrieval, tests)."
        )
    model = s.model_strong if role == "strong" else s.model_fast
    return ChatAnthropic(
        model=model,
        api_key=s.anthropic_api_key.get_secret_value(),
        max_tokens=s.max_tokens,
        temperature=s.temperature,
    )
