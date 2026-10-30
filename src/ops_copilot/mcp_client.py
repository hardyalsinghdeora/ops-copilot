"""Turn MCP servers into LangChain tools the agents can call.

Two modes, selected by ``tool_mode``:

* ``"mcp"``   — spawn the two servers as stdio subprocesses through
                ``MultiServerMCPClient`` (langchain-mcp-adapters). This is the real
                protocol path and what you'd use with remote servers in production.
* ``"local"`` — wrap the very same tool functions in-process with ``StructuredTool``.
                No subprocess, no protocol hop; used by the test-suite and handy
                when debugging prompts. The agents cannot tell the difference —
                which is exactly the property MCP is meant to give you.
"""

from __future__ import annotations

import os
import sys
from typing import Literal

from langchain_core.tools import BaseTool, StructuredTool

ToolMode = Literal["mcp", "local"]

KNOWLEDGE_TOOL_NAMES = {"search_knowledge", "get_document", "list_documents"}
OPS_TOOL_NAMES = {"get_service", "get_active_alerts", "list_recent_deployments", "search_tickets", "get_ticket", "draft_ticket"}


def _server_env() -> dict[str, str]:
    """MCP stdio clients start servers with a *minimal* environment on purpose (least
    privilege). Forward only what our servers need: settings, keys, import path."""
    keep_prefixes = ("OPS_COPILOT_",)
    keep_exact = {"ANTHROPIC_API_KEY", "VOYAGE_API_KEY", "PYTHONPATH", "PATH", "HOME", "VIRTUAL_ENV"}
    return {
        k: v
        for k, v in os.environ.items()
        if k in keep_exact or k.startswith(keep_prefixes)
    }


def mcp_server_config() -> dict:
    """Connection map for MultiServerMCPClient (also handy to paste into Claude Desktop)."""
    env = _server_env()
    return {
        "knowledge": {
            "transport": "stdio",
            "command": sys.executable,
            "args": ["-m", "ops_copilot.mcp_servers.knowledge_server"],
            "env": env,
        },
        "ops": {
            "transport": "stdio",
            "command": sys.executable,
            "args": ["-m", "ops_copilot.mcp_servers.ops_server"],
            "env": env,
        },
    }


async def load_mcp_tools() -> list[BaseTool]:
    from langchain_mcp_adapters.client import MultiServerMCPClient

    client = MultiServerMCPClient(mcp_server_config())
    return await client.get_tools()


def load_local_tools() -> list[BaseTool]:
    from .mcp_servers import knowledge_server, ops_server

    tools: list[BaseTool] = []
    for fn in [*knowledge_server.TOOLS, *ops_server.TOOLS]:
        tools.append(StructuredTool.from_function(fn, name=fn.__name__, description=fn.__doc__ or fn.__name__))
    return tools


async def load_tools(mode: ToolMode = "mcp") -> list[BaseTool]:
    return await load_mcp_tools() if mode == "mcp" else load_local_tools()


def split_tools(tools: list[BaseTool]) -> tuple[list[BaseTool], list[BaseTool]]:
    """(knowledge_tools, ops_tools) — each specialist gets only what it needs."""
    knowledge = [t for t in tools if t.name in KNOWLEDGE_TOOL_NAMES]
    ops = [t for t in tools if t.name in OPS_TOOL_NAMES]
    return knowledge, ops
