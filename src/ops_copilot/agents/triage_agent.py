"""Triage specialist: alert/symptom → actionable incident report (+ optional draft ticket)."""

from __future__ import annotations

from langchain_core.language_models import BaseChatModel
from langchain_core.tools import BaseTool

from .prompts import TRIAGE_AGENT
from .specialist import build_specialist
from .state import TriageReport

TRIAGE_TOOLS = {
    "get_active_alerts", "get_service", "list_recent_deployments", "search_tickets", "get_ticket",
    "draft_ticket", "search_knowledge", "get_document",
}


def build_triage_agent(llm: BaseChatModel, tools: list[BaseTool], max_tool_calls: int = 10):
    """Live ops facts + the runbook. Only triage may draft a ticket; it never needs list_documents."""
    scoped = [t for t in tools if t.name in TRIAGE_TOOLS]
    return build_specialist("triage", TRIAGE_AGENT, llm, scoped, max_tool_calls, output_schema=TriageReport)
