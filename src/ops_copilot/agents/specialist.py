"""A reusable ReAct specialist: LLM ⇄ tools loop, built as a LangGraph sub-graph.

    ┌──────────┐  has tool_calls   ┌───────┐
    │  reason  │ ───────────────▶ │ tools │
    └──────────┘ ◀─────────────── └───────┘
         │ no tool_calls (or budget exhausted)
         ▼
     set draft → END

Compared to ``langgraph.prebuilt.create_react_agent`` this is ~60 lines you can read,
and it adds two things every production agent needs: a *tool-call budget* (so a
confused model cannot loop forever) and *evidence capture* (every tool result is
copied into ``state.evidence`` for the reviewer).

The nodes are async because tools loaded over MCP (``langchain-mcp-adapters``) only
implement async invocation; run the parent graph with ``ainvoke``.
"""

from __future__ import annotations

import json
from collections.abc import Callable

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from langchain_core.tools import BaseTool
from langgraph.graph import END, START, StateGraph
from langgraph.prebuilt import ToolNode
from pydantic import BaseModel

from .state import CopilotState, Evidence

FORMAT_PROMPT = (
    "You convert an investigation transcript into a structured report. Use only facts present in the "
    "transcript and tool results; keep citation ids exactly as they appear."
)


def _render_tool_content(content) -> str:
    """ToolMessage.content may be a string or a list of content blocks (MCP returns blocks)."""
    if isinstance(content, str):
        return content
    parts = []
    for block in content:
        if isinstance(block, dict):
            parts.append(block.get("text") or json.dumps(block))
        else:
            parts.append(str(block))
    return "\n".join(parts)


def build_specialist(
    name: str,
    system_prompt: str,
    llm: BaseChatModel,
    tools: list[BaseTool],
    max_tool_calls: int = 8,
    output_schema: type[BaseModel] | None = None,
) -> Callable:
    """Compile a specialist sub-graph. Returned object is usable as a graph node.

    ``output_schema``: when given, the final step converts the agent's free-text
    conclusion into this Pydantic model with ``with_structured_output`` (one extra
    cheap call). The typed object lands in ``state.report`` and its ``to_markdown()``
    rendering becomes the ``draft`` the reviewer and the human read.
    """

    llm_with_tools = llm.bind_tools(tools) if tools else llm
    # Once the budget is spent the tools stay declared (Anthropic rejects a request whose
    # history holds tool_use/tool_result blocks but declares no tools); tool_choice "none"
    # is what stops further calls.
    llm_answer_only = llm.bind_tools(tools, tool_choice={"type": "none"}) if tools else llm
    tool_node = ToolNode(tools, handle_tool_errors=True)
    formatter = llm.with_structured_output(output_schema) if output_schema else None

    async def reason(state: CopilotState) -> dict:
        budget_left = max_tool_calls - state.get("tool_calls", 0)
        msgs = [SystemMessage(content=system_prompt), *state["messages"]]
        if budget_left <= 0:
            msgs.append(
                HumanMessage(
                    content="Tool budget exhausted. Write your final answer now from the evidence you already have."
                )
            )
            response: AIMessage = await llm_answer_only.ainvoke(msgs)
            response.tool_calls = []
        else:
            response = await llm_with_tools.ainvoke(msgs)
        return {"messages": [response]}

    async def run_tools(state: CopilotState) -> dict:
        last: AIMessage = state["messages"][-1]
        result = await tool_node.ainvoke({"messages": [last]})
        tool_msgs: list[ToolMessage] = result["messages"]
        evidence: list[Evidence] = []
        by_id = {tc["id"]: tc for tc in last.tool_calls}
        for tm in tool_msgs:
            tc = by_id.get(tm.tool_call_id, {})
            evidence.append(
                Evidence(tool=tm.name or tc.get("name", "?"), args=tc.get("args", {}), content=_render_tool_content(tm.content))
            )
        return {
            "messages": tool_msgs,
            "evidence": evidence,
            "tool_calls": state.get("tool_calls", 0) + len(tool_msgs),
        }

    async def finish(state: CopilotState) -> dict:
        last = state["messages"][-1]
        text = last.content if isinstance(last.content, str) else _render_tool_content(last.content)
        if formatter is None:
            return {"draft": text}
        report: BaseModel = await formatter.ainvoke(
            [
                SystemMessage(content=FORMAT_PROMPT),
                *state["messages"],
                HumanMessage(content="Convert your final conclusion above into the required structured report. Do not add facts."),
            ]
        )
        rendered = report.to_markdown() if hasattr(report, "to_markdown") else report.model_dump_json(indent=2)
        return {"draft": rendered, "report": report.model_dump()}

    def should_continue(state: CopilotState) -> str:
        last = state["messages"][-1]
        if isinstance(last, AIMessage) and last.tool_calls:
            return "tools"
        return "finish"

    g = StateGraph(CopilotState)
    g.add_node("reason", reason)
    g.add_node("tools", run_tools)
    g.add_node("finish", finish)
    g.add_edge(START, "reason")
    g.add_conditional_edges("reason", should_continue, {"tools": "tools", "finish": "finish"})
    g.add_edge("tools", "reason")
    g.add_edge("finish", END)
    subgraph = g.compile(name=name)

    async def node(state: CopilotState) -> dict:
        """Run the sub-graph and return only the *delta*.

        Gotcha worth knowing: if a compiled sub-graph is added directly as a node, every
        reducer key it returns (``evidence`` with ``operator.add``) is merged into the
        parent *again*, duplicating entries on each revision round. Starting the
        sub-graph with an empty ``evidence`` list and returning just what it produced
        keeps the parent's list correct.
        """
        inner = {**state, "evidence": []}
        result = await subgraph.ainvoke(inner)
        out = {
            "messages": result["messages"][len(state["messages"]) :],
            "evidence": result.get("evidence", []),
            "tool_calls": result.get("tool_calls", 0),
            "draft": result.get("draft", ""),
        }
        if "report" in result:
            out["report"] = result["report"]
        return out

    return node
