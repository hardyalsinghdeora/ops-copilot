"""Wire the agents into one LangGraph.

    START → supervisor ─┬→ knowledge ──┐
                        ├→ triage ─────┼→ collect_pending → reviewer ─┬→ (retry) → specialist
                        └→ release ────┘                              ├→ human_approval → END
                                                                      └→ END

``build_graph`` takes the LLMs and tools as parameters. That makes it trivial to run
with fake models in tests, with Haiku everywhere for cheap experiments, or with the
in-process ``local`` tools instead of MCP subprocesses.

The specialist nodes are async (MCP tools are async-only), so run the compiled graph
with ``ainvoke`` / ``astream``.
"""

from __future__ import annotations

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import HumanMessage
from langchain_core.tools import BaseTool
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, START, StateGraph

from ..config import get_settings
from ..mcp_client import split_tools
from .human_approval import collect_pending, human_approval
from .knowledge_agent import build_knowledge_agent
from .release_agent import build_release_agent
from .reviewer import after_review, build_reviewer
from .state import CopilotState, Route
from .supervisor import build_supervisor, route_from_state
from .triage_agent import build_triage_agent


def build_graph(
    llm_strong: BaseChatModel,
    llm_fast: BaseChatModel,
    tools: list[BaseTool],
    checkpointer: BaseCheckpointSaver | None = None,
    max_review_rounds: int | None = None,
    max_tool_calls: int | None = None,
):
    s = get_settings()
    max_review_rounds = s.max_review_rounds if max_review_rounds is None else max_review_rounds
    max_tool_calls = s.max_tool_calls if max_tool_calls is None else max_tool_calls
    knowledge_tools, ops_tools = split_tools(tools)

    g = StateGraph(CopilotState)
    g.add_node("supervisor", build_supervisor(llm_fast))
    g.add_node("knowledge", build_knowledge_agent(llm_strong, knowledge_tools, max_tool_calls))
    g.add_node("triage", build_triage_agent(llm_strong, [*ops_tools, *knowledge_tools], max_tool_calls + 2))
    g.add_node("release_notes", build_release_agent(llm_strong, [*ops_tools, *knowledge_tools], max_tool_calls))
    g.add_node("collect_pending", collect_pending)
    g.add_node("reviewer", build_reviewer(llm_fast, max_review_rounds))
    g.add_node("human_approval", human_approval)

    g.add_edge(START, "supervisor")
    g.add_conditional_edges(
        "supervisor",
        route_from_state,
        {"knowledge": "knowledge", "triage": "triage", "release_notes": "release_notes"},
    )
    for specialist in ("knowledge", "triage", "release_notes"):
        g.add_edge(specialist, "collect_pending")
    g.add_edge("collect_pending", "reviewer")
    g.add_conditional_edges(
        "reviewer",
        after_review,
        {"retry": "supervisor", "approval": "human_approval", "done": END},
    )
    g.add_edge("human_approval", END)

    return g.compile(checkpointer=checkpointer or MemorySaver())


def initial_state(task: str, route: Route | None = None) -> CopilotState:
    """Input for one turn. Every per-turn key is reset so a reused ``thread_id`` starts
    clean (fresh routing, no stale answer or pending ticket); only ``messages`` carries over."""
    return {
        "messages": [HumanMessage(content=task)],
        "task": task,
        "route": route,
        "route_reason": "",
        "evidence": None,  # type: ignore[typeddict-item]  # merge_evidence: None clears the list
        "tool_calls": 0,
        "draft": "",
        "review": None,
        "attempts": 0,
        "report": None,
        "pending_action": None,
        "approval": None,
        "final": "",
    }


def thread_config(thread_id: str) -> dict:
    return {"configurable": {"thread_id": thread_id}}


def final_answer(state: dict) -> str:
    return state.get("final") or state.get("draft") or ""
