"""Triage specialist: alert/symptom → actionable incident report (+ optional draft ticket)."""

from __future__ import annotations

from langchain_core.language_models import BaseChatModel
from langchain_core.tools import BaseTool

from .prompts import TRIAGE_AGENT
from .specialist import build_specialist
from .state import TriageReport


def build_triage_agent(llm: BaseChatModel, tools: list[BaseTool], max_tool_calls: int = 10):
    """Gets BOTH ops tools and knowledge tools: triage needs live facts and the runbook."""
    return build_specialist("triage", TRIAGE_AGENT, llm, tools, max_tool_calls, output_schema=TriageReport)
