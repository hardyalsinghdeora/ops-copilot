# 04 — The Model Context Protocol

*Who this is for: engineers who have written a tool-calling loop (Chapter 02) and want tools that outlive one codebase. Time to complete: about 3 hours.*

Chapter 02 showed the model asking for a tool by name and you running it. The question MCP answers is: **who defines the tool, where does it run, and how does any client find it?** Without a standard, every assistant re-implements a Jira connector, a PagerDuty connector and a docs search, each with its own schema and auth handling. MCP is the standard.

## What MCP is

The **Model Context Protocol** (modelcontextprotocol.io) is an open, JSON-RPC-based protocol between three roles:

- **Host.** The application the user is talking to: Claude Desktop, Claude Code, an IDE, or in our case the LangGraph app.
- **Client.** The component inside the host that holds one connection to one server, does the handshake, and relays requests. The host may run many clients.
- **Server.** A process that exposes capabilities. Ours are `ops_copilot.mcp_servers.knowledge_server` and `ops_copilot.mcp_servers.ops_server`.

A server can expose three kinds of things:

| Primitive | What it is | Who triggers it | Our use |
|---|---|---|---|
| **Tools** | Functions the model can call, with a JSON schema for arguments | The model | All nine of ours: `search_knowledge`, `get_document`, `list_documents`, `get_service`, `get_active_alerts`, `list_recent_deployments`, `search_tickets`, `get_ticket`, `draft_ticket` |
| **Resources** | Read-only data addressed by URI, like files | The host/application | Not used in v1; a natural fit for exposing each knowledge-base document by id |
| **Prompts** | Reusable prompt templates with parameters | The user, via the host | Not used in v1 |

On connect, the client sends `initialize`, the server replies with its capabilities, and the client can then call `tools/list` to discover tools and `tools/call` to run them. The model never speaks MCP directly; the host translates the model's `tool_use` into a `tools/call` and the result back into a `tool_result`.

### Transports

- **stdio.** The host launches the server as a subprocess and talks JSON-RPC over stdin/stdout. Simple, local, no network, no auth to design. Both our servers use it, which is what `MCP (stdio)` in the architecture diagram means.
- **Streamable HTTP.** The server is a web endpoint; the client POSTs requests and can receive streamed responses. This is what you use for a shared, remote server, and it is where auth (OAuth in the spec) comes in. Chapter 07 covers moving to it.

## Why MCP beats bespoke tool code

Before MCP, the natural shape of this project would have been a `tools.py` with Python functions decorated for LangChain, importing the retriever and reading the fixture files. It would work. It would also mean:

- Only this app can use those tools. Claude Desktop cannot search your runbooks.
- Swapping fixtures for real Jira means editing the agent code.
- Every tool's schema, error handling and auth is hand-rolled.

With MCP the boundary is a process and a protocol. The spec's design decision is explicit: **agents never import data access code directly; everything an agent can do is an MCP tool.** Consequences:

- `knowledge_server` is one process that serves both this app and Claude Desktop.
- Replacing `ops_server` with a real PagerDuty MCP server that exposes the same tool names requires no change in `triage_agent`.
- The tool list is the complete, inspectable list of what the agents can do. That is a security property.

See [`adr/0002-mcp-for-all-tools.md`](adr/0002-mcp-for-all-tools.md).

## Building a server with FastMCP

The `mcp` Python SDK ships **FastMCP**, which turns decorated functions into tools: the function signature becomes the JSON schema, the docstring becomes the tool description, and the return value is serialised for the client.

### `knowledge_server`

```python
# ops_copilot/mcp_servers/knowledge_server.py (illustrative)
from mcp.server.fastmcp import FastMCP
from ops_copilot.config import Settings
from ops_copilot.rag.retriever import HybridRetriever

mcp = FastMCP("ops-copilot-knowledge")
_retriever: HybridRetriever | None = None   # loaded lazily from data/.index/chroma + data/.index/bm25.pkl

@mcp.tool()
def search_knowledge(query: str, kind: str | None = None, service: str | None = None, k: int = 5) -> list[dict]:
    """Search runbooks, ADRs, postmortems and service pages.

    Use this first for any 'how do I' or 'what happened when' question. Returns up to k chunks,
    each with `chunk_id` (cite it as [chunk_id]), `doc_id`, `title`, `heading`, `text`, `score`.
    `kind` filters to runbook|adr|postmortem|service; `service` filters by service name.
    If a filtered search returns nothing, retry without filters.
    """
    filt = {k_: v for k_, v in {"kind": kind, "service": service}.items() if v}
    hits = _get_retriever().search(query, k=k, filter=filt or None)
    return [{"chunk_id": h.chunk.id, "doc_id": h.chunk.doc_id, "title": h.chunk.metadata["title"],
             "heading": h.chunk.heading, "text": h.chunk.text, "score": h.score} for h in hits]

@mcp.tool()
def get_document(doc_id: str) -> dict:
    """Return the full text and metadata of one document by its doc_id. Use after search_knowledge
    when a chunk is relevant but you need the surrounding procedure."""
    ...

@mcp.tool()
def list_documents(kind: str | None = None) -> list[dict]:
    """List documents (id, title, kind, service, updated), optionally filtered by kind."""
    ...

if __name__ == "__main__":
    mcp.run(transport="stdio")
```

Notice that the docstrings are written for the model, not for a Python reader. They say *when* to call the tool and what the result fields mean. This is the single biggest lever on whether the agent uses the tool well (Chapter 02, pitfall one).

### `ops_server`

Same shape over JSON fixtures in `data/fixtures/` (`services.json`, `alerts.json`, `deployments.json`, `tickets.json`):

```python
# ops_copilot/mcp_servers/ops_server.py (illustrative)
mcp = FastMCP("ops-copilot-ops")

@mcp.tool()
def get_service(name: str) -> dict:
    """Service catalogue entry: owner team, on-call, tier, dependencies, dashboards, runbook ids."""

@mcp.tool()
def get_active_alerts(service: str | None = None) -> list[dict]:
    """Currently firing alerts, optionally for one service."""

@mcp.tool()
def list_recent_deployments(service: str, hours: int = 24) -> list[dict]:
    """Deployments to `service` in the last `hours`, newest first, with version and change summary."""

@mcp.tool()
def search_tickets(query: str, status: str | None = None, limit: int = 10) -> list[dict]:
    """Keyword search over tickets (id, title, status, labels, service). Use for 'has this happened before'."""

@mcp.tool()
def get_ticket(ticket_id: str) -> dict:
    """Full ticket including description, comments and linked deployments."""

@mcp.tool()
def draft_ticket(title: str, description: str, service: str, severity: str, labels: list[str] = []) -> dict:
    """Prepare a ticket for filing. Does NOT file it; the result must be approved by a human.
    Returns the draft with a draft_id."""
```

The names mirror what Jira, PagerDuty and ArgoCD adapters would expose, so the fixtures can be swapped for real servers later. `draft_ticket` is the only tool with a side effect, and even it only produces a draft; the actual write to `data/.drafts/` happens after `human_approval` (Chapter 05).

Run either standalone with `ops-copilot mcp knowledge` or `ops-copilot mcp ops` (the `cli` wraps `mcp.run`).

## Consuming the servers: `ops_copilot.mcp_client`

`langchain-mcp-adapters` provides `MultiServerMCPClient`, which connects to several servers, lists their tools, and wraps each as a LangChain `BaseTool` that `ChatAnthropic.bind_tools` accepts:

```python
# ops_copilot/mcp_client.py (illustrative)
import sys
from langchain_mcp_adapters.client import MultiServerMCPClient

SERVERS = {
    "knowledge": {
        "command": sys.executable,
        "args": ["-m", "ops_copilot.mcp_servers.knowledge_server"],
        "transport": "stdio",
    },
    "ops": {
        "command": sys.executable,
        "args": ["-m", "ops_copilot.mcp_servers.ops_server"],
        "transport": "stdio",
    },
}

async def load_mcp_tools():
    client = MultiServerMCPClient(SERVERS)
    tools = await client.get_tools()          # one LangChain tool per MCP tool, all nine
    return tools
```

`build_graph(llm_strong, llm_fast, tools, checkpointer)` receives that list and each specialist filters it to its own scope by name. From the agent's point of view there is no difference between an MCP tool and a local Python tool: it has a name, a schema, and an `ainvoke`.

Two practical notes:

- MCP clients are async. The CLI runs the graph inside `asyncio.run(...)`.
- Each server is a subprocess spawned on startup. If `knowledge_server` cannot find `data/.index/chroma`, the error surfaces as a tool error at first call, not at connect time. Run `ops-copilot ingest` first.

## Plugging the knowledge server into Claude Desktop and Claude Code

This is the payoff of the protocol: the same server, no code change, inside a different host.

### Claude Desktop

Edit `claude_desktop_config.json` (on macOS: `~/Library/Application Support/Claude/claude_desktop_config.json`; on Windows: `%APPDATA%\Claude\claude_desktop_config.json`) and add an entry under `mcpServers`:

```json
{
  "mcpServers": {
    "ops-copilot-knowledge": {
      "command": "/absolute/path/to/your/venv/bin/python",
      "args": ["-m", "ops_copilot.mcp_servers.knowledge_server"],
      "env": {
        "OPS_COPILOT_INDEX_DIR": "/absolute/path/to/ops-copilot/data/.index",
        "OPS_COPILOT_EMBEDDING_PROVIDER": "local"
      }
    }
  }
}
```

Points that trip people up:

- Use the **absolute path to the Python interpreter** of the virtualenv where you ran `pip install -e .`. Claude Desktop does not inherit your shell's `PATH` or activated environment.
- The server must be able to find the index. `Settings` defaults `index_dir` to `<repo>/data/.index`, so if the server runs from an installed package elsewhere, set `OPS_COPILOT_INDEX_DIR` to the absolute path of the index you built with `ops-copilot ingest`. `ops-copilot mcp-config` prints a ready-made block with the right interpreter path.
- Restart Claude Desktop after editing. Tools appear in the tools panel; ask "search our runbooks for the payments latency procedure" and watch `search_knowledge` get called.
- The knowledge server only needs the embedding provider, not an Anthropic key; the reasoning happens in the host.

### Claude Code

```bash
claude mcp add ops-copilot-knowledge -- /absolute/path/to/venv/bin/python -m ops_copilot.mcp_servers.knowledge_server
```

Add `--scope project` to write it to a `.mcp.json` in the repo so teammates get it too. Then, inside Claude Code, `/mcp` shows the server and its tools. Check the Claude Code MCP documentation on docs.claude.com for current flags.

## Security considerations

An MCP server is code that runs with your credentials and whose behaviour is steered by a model that reads untrusted documents. Treat it like an API you are exposing.

- **Least privilege per server.** `knowledge_server` can read the index and nothing else. `ops_server` reads fixtures. Neither has network access in v1. In production, the Jira server should have a scoped API token, not an admin's.
- **Read-only by default.** Eight of nine tools are reads. The one that is not, `draft_ticket`, deliberately does not perform the write; it returns a draft.
- **Human approval for side effects.** Filing the ticket happens only after `human_approval` interrupts the graph and a person says yes. This is the design decision in [`adr/0004-human-in-the-loop-side-effects.md`](adr/0004-human-in-the-loop-side-effects.md). If you add a real `create_ticket` tool later, it must sit behind the same gate.
- **Tool scoping at the agent.** Even though the client loads all nine tools, each specialist is bound to a subset. The knowledge agent cannot be injected into drafting a ticket because it does not have the tool.
- **Validate inputs in the server.** Tool arguments come from the model. `get_document(doc_id)` must not let `../../.env` through if ids ever map to paths. `list_recent_deployments(hours=10**9)` should be clamped.
- **Treat tool output as untrusted when it goes back to the model.** Document text can contain injection (Chapter 02). The server can help by clearly labelling returned content as document data.
- **Transport.** stdio is local and inherits the user's identity; fine for a laptop. Streamable HTTP needs authentication and TLS, and the MCP spec's authorization section describes the OAuth flow.
- **Log every tool call** with arguments and caller. Chapter 07.

## Common pitfalls

- **Relative paths in the server.** The host decides the working directory. Resolve paths from `Settings`, not `os.getcwd()`.
- **Vague docstrings.** They are the tool description the model sees.
- **Returning huge payloads.** `get_document` on a 20-page postmortem puts 5,000 tokens into the context in one call. Return what is needed; offer `search_knowledge` for the rest.
- **Blocking I/O in an async client.** Loading the ONNX model inside the first tool call is fine; doing it on every call is not. Cache it at module level.
- **Forgetting the server is a separate process.** Debugging prints go to the server's stderr, not the host's console. Log to stderr, never to stdout, which is the transport.
- **Giving the model a write tool "for convenience".** Every write needs a gate.

## Exercises

1. **Inspect the protocol.** Run `ops-copilot mcp knowledge` and use the MCP Inspector (`npx @modelcontextprotocol/inspector`, from modelcontextprotocol.io) to connect over stdio, call `tools/list`, and invoke `search_knowledge`. Look at the JSON schema FastMCP generated from the function signature.
2. **Add a resource.** Expose each knowledge-base document as an MCP resource at `kb://{doc_id}` using FastMCP's resource decorator. Verify in the Inspector. Reflect: when would a host prefer a resource over calling `get_document`?
3. **Attach to Claude Desktop.** Follow the config above, restart, and ask a question that your knowledge base answers. Then ask one it does not. Does Claude Desktop admit it does not know, and how does that compare with what `ops_copilot.agents.knowledge_agent` does with its grounded prompt?
4. **Break least privilege, then fix it.** Temporarily add `draft_ticket` to the knowledge agent's tool scope and ask a knowledge question phrased as "...and file a ticket about it". Observe. Then restore the scope and confirm the agent now says it cannot.

## Further reading

- The Model Context Protocol specification and documentation (modelcontextprotocol.io): architecture, tools, resources, prompts, transports, and the authorization section.
- The `mcp` Python SDK README and FastMCP examples (linked from modelcontextprotocol.io).
- `langchain-mcp-adapters` README for `MultiServerMCPClient` configuration (stdio and HTTP).
- Anthropic documentation, "Model Context Protocol" pages for Claude Desktop and Claude Code (docs.claude.com).
- Anthropic documentation, "Tool use" (docs.claude.com), to see what the host translates MCP tool calls into.
