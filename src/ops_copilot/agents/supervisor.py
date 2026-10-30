"""Supervisor: one cheap structured-output call that picks the specialist.

Routing with a *small* model and a *typed* schema is deliberate: it is fast, cheap,
and the output is a validated enum, so the graph's conditional edge can never receive
an unexpected string.
"""

from __future__ import annotations

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import HumanMessage, SystemMessage

from .prompts import SUPERVISOR
from .state import CopilotState, RouteDecision


def build_supervisor(llm: BaseChatModel):
    router = llm.with_structured_output(RouteDecision)

    def supervisor(state: CopilotState) -> dict:
        if state.get("route"):  # route already fixed by the caller (API endpoints) or a retry
            return {}
        task = state.get("task") or _last_human_text(state)
        decision: RouteDecision = router.invoke([SystemMessage(content=SUPERVISOR), HumanMessage(content=task)])
        return {"route": decision.route, "route_reason": decision.reason, "task": task}

    return supervisor


def _last_human_text(state: CopilotState) -> str:
    for m in reversed(state.get("messages", [])):
        if m.type == "human":
            return m.content if isinstance(m.content, str) else str(m.content)
    return ""


def route_from_state(state: CopilotState) -> str:
    return state["route"]
