"""Assemble a ready-to-use graph from settings (used by both the CLI and the API)."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph.state import CompiledStateGraph

from .agents import build_graph
from .llm import get_llm
from .mcp_client import ToolMode, load_local_tools, mcp_sessions, tools_from_sessions

if TYPE_CHECKING:
    from mcp import ClientSession

_checkpointer = MemorySaver()  # process-local; swap for SqliteSaver/PostgresSaver in production


@dataclass
class Copilot:
    graph: CompiledStateGraph
    sessions: dict[str, ClientSession] = field(default_factory=dict)  # empty for in-process tools

    async def tools_healthy(self, timeout: float = 5.0) -> bool:
        """True while every MCP server still answers a ping."""
        try:
            for session in self.sessions.values():
                await asyncio.wait_for(session.send_ping(), timeout)
        except Exception:
            return False
        return True


@asynccontextmanager
async def open_copilot(tool_mode: ToolMode = "mcp") -> AsyncIterator[Copilot]:
    """The graph, with its MCP servers running until the block exits (one process per
    server, not one per tool call). Run the graph with ``ainvoke``: MCP tools are async-only."""
    llm_strong, llm_fast = get_llm("strong"), get_llm("fast")  # a missing key fails before servers start
    if tool_mode == "local":
        yield Copilot(build_graph(llm_strong, llm_fast, load_local_tools(), checkpointer=_checkpointer))
        return
    async with mcp_sessions() as sessions:
        tools = await tools_from_sessions(sessions)
        yield Copilot(build_graph(llm_strong, llm_fast, tools, checkpointer=_checkpointer), sessions)
