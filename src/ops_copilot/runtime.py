"""Assemble a ready-to-use graph from settings (used by both the CLI and the API)."""

from __future__ import annotations

import asyncio
from functools import lru_cache
from typing import Literal

from langgraph.checkpoint.memory import MemorySaver

from .agents import build_graph
from .llm import get_llm
from .mcp_client import ToolMode, load_local_tools, load_mcp_tools

_checkpointer = MemorySaver()  # process-local; swap for SqliteSaver/PostgresSaver in production


def _load_tools_sync(mode: ToolMode):
    if mode == "local":
        return load_local_tools()
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(load_mcp_tools())
    # already inside an event loop (FastAPI): caller should use the async variant
    raise RuntimeError("use get_graph_async inside an event loop")


@lru_cache
def get_graph(tool_mode: Literal["mcp", "local"] = "mcp"):
    tools = _load_tools_sync(tool_mode)
    return build_graph(get_llm("strong"), get_llm("fast"), tools, checkpointer=_checkpointer)


_async_graph = None


async def get_graph_async(tool_mode: ToolMode = "mcp"):
    global _async_graph
    if _async_graph is None:
        tools = load_local_tools() if tool_mode == "local" else await load_mcp_tools()
        _async_graph = build_graph(get_llm("strong"), get_llm("fast"), tools, checkpointer=_checkpointer)
    return _async_graph
