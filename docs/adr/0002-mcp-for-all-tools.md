# ADR 0002 — All agent capabilities are exposed as MCP tools

## Status

Accepted.

## Context

The agents need two kinds of capability: searching the knowledge base, and reading operational state (services, alerts, deployments, tickets) plus drafting a ticket. The straightforward implementation would be a module of Python functions decorated as LangChain tools, importing `HybridRetriever` and reading the JSON fixtures directly.

That design has three problems. The tools are usable only from this application; the same runbook search is unavailable inside Claude Desktop or Claude Code. Replacing fixtures with real Jira, PagerDuty and ArgoCD integrations means changing code the agents import. And the set of things an agent can do is scattered across imports rather than being one inspectable list.

The Model Context Protocol (modelcontextprotocol.io) standardises how a host discovers and calls tools exposed by a separate server, over stdio or streamable HTTP. FastMCP in the `mcp` SDK makes a server a set of decorated functions; `langchain-mcp-adapters` turns any MCP server's tools into LangChain tools.

## Decision

Agents never import data-access code. Every capability is an MCP tool served by one of two FastMCP servers:

- `ops_copilot.mcp_servers.knowledge_server`: `search_knowledge`, `get_document`, `list_documents`, fronting the retriever.
- `ops_copilot.mcp_servers.ops_server`: `get_service`, `get_active_alerts`, `list_recent_deployments`, `search_tickets`, `get_ticket`, `draft_ticket`, over `data/fixtures/`, with names mirroring what real Jira / PagerDuty / ArgoCD adapters would expose.

`ops_copilot.mcp_client.load_mcp_tools()` connects to both over stdio with `MultiServerMCPClient` and hands the resulting LangChain tools to `build_graph`. Each specialist binds a named subset.

Both servers are runnable standalone via `ops-copilot mcp knowledge|ops` so they can be attached to other MCP hosts.

## Consequences

- Swapping fixtures for a real system is a server change; agents are untouched if tool names and schemas are kept.
- The knowledge server works inside Claude Desktop and Claude Code with a config entry.
- The agents' complete capability surface is the union of two `tools/list` responses: auditable and scopable.
- Two subprocesses start with the application; debug output must go to stderr because stdout is the transport.
- Tool docstrings are model-facing descriptions and must be written as such.
- Moving a server to streamable HTTP for shared use requires the MCP authorization flow.

## Alternatives considered

- **Plain LangChain `@tool` functions in-process.** Less setup. Rejected for coupling and non-reusability.
- **A bespoke RPC or REST layer for tools.** Decouples, but reinvents schema discovery and host integration MCP already provides. Rejected.
- **MCP for ops tools only, direct import for knowledge search.** Loses the knowledge server's reuse in other hosts. Rejected.
