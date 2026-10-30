"""Release-notes specialist: tickets + deployments → release notes."""

from __future__ import annotations

from langchain_core.language_models import BaseChatModel
from langchain_core.tools import BaseTool

from .prompts import RELEASE_AGENT
from .specialist import build_specialist


def build_release_agent(llm: BaseChatModel, tools: list[BaseTool], max_tool_calls: int = 8):
    # Read-only subset: the release writer must never be able to draft tickets.
    allowed = {"search_tickets", "get_ticket", "list_recent_deployments", "search_knowledge", "get_document"}
    scoped = [t for t in tools if t.name in allowed]
    return build_specialist("release_notes", RELEASE_AGENT, llm, scoped, max_tool_calls)
