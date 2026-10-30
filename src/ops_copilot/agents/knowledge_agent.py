"""Knowledge specialist: grounded Q&A over the team's documents."""

from __future__ import annotations

from langchain_core.language_models import BaseChatModel
from langchain_core.tools import BaseTool

from .prompts import KNOWLEDGE_AGENT
from .specialist import build_specialist


def build_knowledge_agent(llm: BaseChatModel, knowledge_tools: list[BaseTool], max_tool_calls: int = 8):
    return build_specialist("knowledge", KNOWLEDGE_AGENT, llm, knowledge_tools, max_tool_calls)
