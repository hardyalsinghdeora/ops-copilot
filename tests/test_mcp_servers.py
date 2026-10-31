"""MCP servers: tool contracts, exercised in-process via FastMCP and over the LangChain adapter."""

from __future__ import annotations

import json

import pytest

from ops_copilot.mcp_servers import knowledge_server, ops_server


async def _call(server, name, **args):
    result = await server.mcp.call_tool(name, args)
    blocks = result[0] if isinstance(result, tuple) else result
    return "\n".join(b.text for b in blocks if getattr(b, "type", "") == "text")


async def test_knowledge_server_lists_expected_tools(local_tools):
    names = {t.name for t in await knowledge_server.mcp.list_tools()}
    assert names == {"search_knowledge", "get_document", "list_documents"}


async def test_search_knowledge_returns_citable_chunks(local_tools):
    out = json.loads(await _call(knowledge_server, "search_knowledge", query="rotate payments database credentials", k=3))
    assert 1 <= len(out) <= 3
    assert {"id", "doc_id", "title", "kind", "heading", "score", "text"} <= set(out[0])
    assert "#" in out[0]["id"]
    assert any(o["doc_id"] == "rb-payments-db-credential-rotation" for o in out)


async def test_get_document_unknown_id_is_helpful(local_tools):
    out = await _call(knowledge_server, "get_document", doc_id="rb-does-not-exist")
    assert "No document" in out and "list_documents" in out


async def test_ops_server_tools(local_tools):
    names = {t.name for t in await ops_server.mcp.list_tools()}
    assert names == {"get_service", "get_active_alerts", "list_recent_deployments", "search_tickets", "get_ticket", "draft_ticket"}

    svc = json.loads(await _call(ops_server, "get_service", service_id="payments-api"))
    assert svc["id"] == "payments-api" and svc["oncall_now"] and svc["runbook_ids"]

    unknown = json.loads(await _call(ops_server, "get_service", service_id="nope"))
    assert "error" in unknown and "payments-api" in unknown["known_services"]

    firing = json.loads(await _call(ops_server, "get_active_alerts"))
    assert firing and all(a["status"] == "firing" for a in firing)
    assert any(a["service"] == "payments-api" for a in firing)

    deps = json.loads(await _call(ops_server, "list_recent_deployments", service_id="payments-api", hours=48))
    assert deps and all(d["service"] == "payments-api" for d in deps)
    assert deps == sorted(deps, key=lambda d: d["deployed_at"], reverse=True)

    done = json.loads(await _call(ops_server, "search_tickets", fix_version="2.14.0", status="Done"))
    assert len(done) >= 8 and len({t["service"] for t in done}) >= 3

    one = json.loads(await _call(ops_server, "get_ticket", key=done[0]["key"]))
    assert one["key"] == done[0]["key"]


async def test_draft_ticket_writes_draft_not_ticket(local_tools):
    from ops_copilot.config import get_settings

    out = json.loads(await _call(ops_server, "draft_ticket", summary="s", description="d", service_id="payments-api"))
    assert out["status"] == "draft" and out["draft_id"].startswith("DRAFT-")
    assert (get_settings().drafts_dir / f"{out['draft_id']}.json").exists()


def test_fixtures_are_cross_consistent():
    services = {s["id"] for s in ops_server._load("services")}
    runbooks = {d["id"] for d in knowledge_server._get_retriever().list_documents(kind="runbook")}
    for a in ops_server._load("alerts"):
        assert a["service"] in services and a["runbook_id"] in runbooks, a["id"]
    for d in ops_server._load("deployments"):
        assert d["service"] in services, d["id"]
    for s in ops_server._load("services"):
        assert set(s["runbook_ids"]) <= runbooks, s["id"]
        assert set(s["dependencies"]) <= services, s["id"]


def test_mcp_servers_do_not_receive_the_anthropic_key(monkeypatch):
    from ops_copilot.mcp_client import mcp_server_config

    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
    monkeypatch.setenv("VOYAGE_API_KEY", "pa-test")
    for server in mcp_server_config().values():
        assert "ANTHROPIC_API_KEY" not in server["env"]
        assert server["env"]["VOYAGE_API_KEY"] == "pa-test"  # the knowledge server may embed with Voyage


def test_local_tools_mirror_mcp_tools(local_tools):
    names = {t.name for t in local_tools}
    assert names == {
        "search_knowledge", "get_document", "list_documents",
        "get_service", "get_active_alerts", "list_recent_deployments", "search_tickets", "get_ticket", "draft_ticket",
    }
    search = next(t for t in local_tools if t.name == "search_knowledge")
    out = json.loads(search.invoke({"query": "kafka consumer lag", "k": 2}))
    assert out and out[0]["kind"]


@pytest.mark.slow
async def test_mcp_over_stdio_round_trip(tmp_path, monkeypatch):
    """Real protocol path: spawn the servers as subprocesses and call a tool."""
    from ops_copilot.rag import ingest

    monkeypatch.setenv("OPS_COPILOT_INDEX_DIR", str(tmp_path / "idx"))
    from ops_copilot.config import get_settings

    get_settings.cache_clear()
    ingest()
    from ops_copilot.mcp_client import load_mcp_tools

    tools = await load_mcp_tools()
    assert len(tools) == 9
    search = next(t for t in tools if t.name == "search_knowledge")
    result = await search.ainvoke({"query": "jwt signing key rotation", "k": 2})
    text = result if isinstance(result, str) else "".join(b.get("text", "") for b in result)
    assert "auth" in text.lower()
    get_settings.cache_clear()


@pytest.mark.slow
async def test_graph_calls_mcp_tools(tmp_path, monkeypatch):
    """The CLI/API default: a specialist calls the async-only MCP tools from inside the graph."""
    from langchain_core.messages import AIMessage

    from ops_copilot.agents import build_graph, initial_state, thread_config
    from ops_copilot.agents.state import ReviewVerdict
    from ops_copilot.config import get_settings
    from ops_copilot.mcp_client import load_mcp_tools
    from ops_copilot.rag import ingest

    from .fakes import ScriptedChatModel

    monkeypatch.setenv("OPS_COPILOT_INDEX_DIR", str(tmp_path / "idx"))
    get_settings.cache_clear()
    ingest()
    strong = ScriptedChatModel(
        responses=[
            AIMessage(
                content="",
                tool_calls=[{"id": "c1", "name": "search_knowledge", "args": {"query": "jwt signing key rotation", "k": 2}}],
            ),
            AIMessage(content="answer"),
        ]
    )
    fast = ScriptedChatModel(
        structured=[ReviewVerdict(verdict="pass", faithfulness=5, citations_ok=True, completeness=5, feedback="ok")]
    )
    g = build_graph(strong, fast, await load_mcp_tools())
    out = await g.ainvoke(initial_state("How do I rotate the JWT signing key?", route="knowledge"), thread_config("mcp"))

    content = out["evidence"][0]["content"]
    assert not content.startswith("Error"), content
    assert json.loads(content) and "auth" in content.lower()
    get_settings.cache_clear()
