"""Assemble a ready-to-use graph from settings (used by both the CLI and the API)."""

from __future__ import annotations

from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph.state import CompiledStateGraph

from .agents import build_graph
from .llm import get_llm
from .mcp_client import ToolMode, load_tools

_checkpointer = MemorySaver()  # process-local; swap for SqliteSaver/PostgresSaver in production
_graphs: dict[ToolMode, CompiledStateGraph] = {}


async def get_graph_async(tool_mode: ToolMode = "mcp") -> CompiledStateGraph:
    """Build the graph once per tool mode. Run it with ``ainvoke``: MCP tools are async-only."""
    if tool_mode not in _graphs:
        tools = await load_tools(tool_mode)
        _graphs[tool_mode] = build_graph(get_llm("strong"), get_llm("fast"), tools, checkpointer=_checkpointer)
    return _graphs[tool_mode]
