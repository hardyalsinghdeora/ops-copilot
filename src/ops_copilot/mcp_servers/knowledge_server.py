"""MCP server: the team's knowledge base (runbooks, ADRs, postmortems, service pages).

Run standalone (stdio) with::

    python -m ops_copilot.mcp_servers.knowledge_server
    # or: ops-copilot mcp knowledge

Any MCP host — Claude Desktop, Claude Code, Cursor, or this project's LangGraph
agents — can then call ``search_knowledge``, ``get_document`` and ``list_documents``.

The tool functions are plain Python so they can also be used in-process (see
``ops_copilot.mcp_client.load_local_tools``) — one implementation, two transports.
"""

from __future__ import annotations

import json
import logging
from functools import lru_cache

from mcp.server.fastmcp import FastMCP

from ..rag.ingest import load_retriever
from ..rag.retriever import HybridRetriever

log = logging.getLogger(__name__)

mcp = FastMCP(
    "ops-copilot-knowledge",
    instructions=(
        "Search the engineering team's runbooks, ADRs, postmortems and service pages. "
        "Always cite chunk ids like [rb-xyz#2] when you use a result."
    ),
)

_retriever_override: HybridRetriever | None = None


def set_retriever(r: HybridRetriever | None) -> None:
    """Inject a retriever (tests / in-memory usage)."""
    global _retriever_override
    _retriever_override = r
    _get_retriever.cache_clear()


@lru_cache
def _get_retriever() -> HybridRetriever:
    return _retriever_override or load_retriever()


@mcp.tool()
def search_knowledge(query: str, kind: str | None = None, service: str | None = None, k: int = 6) -> str:
    """Hybrid (BM25 + vector) search over runbooks, ADRs, postmortems and service pages.

    Args:
        query: Natural-language question or symptom, e.g. "payments p99 latency spike".
        kind: Optional filter: runbook | adr | postmortem | service.
        service: Optional service id filter, e.g. "payments-api".
        k: Number of chunks to return (1-12).
    Returns:
        JSON list of {id, doc_id, title, kind, service, heading, score, sources, text}.
        Cite results as [id], e.g. [rb-payments-latency#2].
    """
    k = max(1, min(int(k), 12))
    results = _get_retriever().search(query, k=k, kind=kind or None, service=service or None)
    return json.dumps(
        [
            {
                "id": r.chunk.id,
                "doc_id": r.chunk.doc_id,
                "title": r.chunk.metadata.get("title", ""),
                "kind": r.chunk.metadata.get("kind", ""),
                "service": r.chunk.metadata.get("service", ""),
                "heading": r.chunk.heading,
                "score": round(r.score, 4),
                "sources": r.sources,
                "text": r.chunk.text,
            }
            for r in results
        ],
        ensure_ascii=False,
    )


@mcp.tool()
def get_document(doc_id: str) -> str:
    """Return a whole document by id (e.g. "rb-payments-db-credential-rotation"),
    with every chunk labelled by its citation id. Use after search_knowledge when you
    need the full procedure rather than a fragment."""
    doc = _get_retriever().get_document(doc_id)
    return doc or f"No document with id '{doc_id}'. Use list_documents to see valid ids."


@mcp.tool()
def list_documents(kind: str | None = None, service: str | None = None) -> str:
    """List available documents (id, title, kind, service, owner, updated), optionally
    filtered by kind (runbook|adr|postmortem|service) and/or service id."""
    return json.dumps(_get_retriever().list_documents(kind or None, service or None), ensure_ascii=False)


TOOLS = [search_knowledge, get_document, list_documents]


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
