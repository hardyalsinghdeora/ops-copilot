# Architecture

Ops Copilot is a **multi-agent, retrieval-grounded assistant for on-call engineers**. It answers "how does X work / what do I do when Y breaks" from the team's own runbooks, ADRs and postmortems, pulls live operational facts (alerts, deployments, tickets, ownership) through **MCP** tool servers, and routes each request across specialist **LangGraph** agents with a **reviewer** that checks every answer against the evidence before it is shown.

```
                        ┌─────────────────────────────────────────────┐
  user (CLI / HTTP)     │              LangGraph  (graph.py)           │
  ─────────────────────▶│  supervisor ──▶ knowledge ─┐                 │
                        │      │          triage    ─┼▶ reviewer ──▶ END
                        │      │          release   ─┘      │          │
                        │      └──────── (routing) ◀──retry─┘          │
                        └───────────┬───────────────────┬─────────────┘
                                    │ MCP (stdio)       │ MCP (stdio)
                         ┌──────────▼──────────┐ ┌──────▼─────────────┐
                         │  knowledge_server   │ │    ops_server      │
                         │ search_knowledge    │ │ get_service        │
                         │ get_document        │ │ get_active_alerts  │
                         │ list_documents      │ │ list_recent_deploys│
                         └──────────┬──────────┘ │ search_tickets     │
                                    │            │ get_ticket         │
                         ┌──────────▼──────────┐ │ draft_ticket       │
                         │  RAG pipeline       │ └──────┬─────────────┘
                         │ loader → chunker →  │        │ JSON fixtures
                         │ embeddings → Chroma │        │ (data/fixtures)
                         │ + BM25 → RRF fusion │
                         └──────────┬──────────┘
                                    │ data/knowledge_base/**/*.md
```

## Components

| Layer | Module | Responsibility |
|---|---|---|
| Config | `ops_copilot.config` | `Settings` (pydantic-settings): model names, paths, embedding provider, API key. Reads `.env`. |
| LLM | `ops_copilot.llm` | `get_llm(role)` → `ChatAnthropic`. Two roles: `"strong"` (Sonnet) for specialists, `"fast"` (Haiku) for routing & judging. |
| RAG | `ops_copilot.rag.loader` | Load markdown with YAML front-matter → `Document(id, title, kind, service, text, metadata)`. |
| | `ops_copilot.rag.chunker` | Heading-aware chunking with token-ish size + overlap → `Chunk(id, doc_id, heading, text, metadata)`. |
| | `ops_copilot.rag.embeddings` | `EmbeddingProvider` protocol; `LocalMiniLMEmbeddings` (ONNX, default), `VoyageEmbeddings`, `HashingEmbeddings` (deterministic, offline — tests/CI). |
| | `ops_copilot.rag.store` | `ChromaStore` persistent vector store (`data/.index/chroma`). |
| | `ops_copilot.rag.retriever` | `HybridRetriever`: BM25 (lexical) + vector (semantic) fused with Reciprocal Rank Fusion; optional metadata filter; returns `RetrievedChunk(score, chunk)`. |
| | `ops_copilot.rag.ingest` | `ingest(kb_dir)` pipeline → store + BM25 index saved to disk. |
| MCP | `ops_copilot.mcp_servers.knowledge_server` | FastMCP server (stdio) exposing the retriever: `search_knowledge(query, kind?, service?, k)`, `get_document(doc_id)`, `list_documents(kind?)`. |
| | `ops_copilot.mcp_servers.ops_server` | FastMCP server (stdio) over JSON fixtures: `get_service`, `get_active_alerts`, `list_recent_deployments`, `search_tickets`, `get_ticket`, `draft_ticket`. Mirrors what Jira / PagerDuty / ArgoCD adapters would expose. |
| | `ops_copilot.mcp_client` | `mcp_sessions()` + `tools_from_sessions()`: one long-lived process per server (what the CLI and API use); `load_mcp_tools()`: stateless, a fresh server per call (for scripts). Both via `langchain-mcp-adapters` `MultiServerMCPClient`. |
| Agents | `ops_copilot.agents.state` | `CopilotState` (TypedDict): `messages`, `route`, `evidence`, `draft`, `review`, `attempts`, `final`, `pending_action`. |
| | `ops_copilot.agents.supervisor` | Structured-output router → `knowledge` / `triage` / `release_notes`. |
| | `ops_copilot.agents.knowledge_agent` | ReAct loop over knowledge tools; answers with `[doc_id#chunk]` citations. |
| | `ops_copilot.agents.triage_agent` | ReAct loop over ops + knowledge tools → `TriageReport` (severity, suspected cause, matching runbook, recent changes, similar incidents, next steps, draft ticket). |
| | `ops_copilot.agents.release_agent` | ReAct loop over tickets/deploys → release notes markdown grouped by capability. |
| | `ops_copilot.agents.reviewer` | LLM-as-judge: faithfulness to evidence + citation check → `pass` or `revise` (feedback, max 2 rounds). |
| | `ops_copilot.agents.human_approval` | `interrupt()` node — a drafted ticket is only filed after a human approves (human-in-the-loop). |
| | `ops_copilot.agents.graph` | `build_graph(llm_strong, llm_fast, tools, checkpointer)` wires it all. |
| Interfaces | `ops_copilot.cli` | `ingest`, `ask`, `triage`, `release-notes`, `eval`, `serve`, `mcp` (run a server). |
| | `ops_copilot.api.main` | FastAPI: `POST /chat`, `POST /triage`, `POST /approve`, `POST /release-notes` (bearer token when `OPS_COPILOT_API_TOKEN` is set), `GET /health` (503 if an MCP server stops answering), and a chat page at `GET /`. |
| Evals | `ops_copilot.evals` | `golden.json` Q/A set; `retrieval_eval` (hit@k, MRR); `judge` (faithfulness / answer-relevance via Haiku). |

## Request lifecycle (knowledge question)

1. `cli ask "How do I rotate the payments DB credentials?"`
2. **supervisor** classifies → `knowledge`.
3. **knowledge agent** calls `search_knowledge` (MCP → HybridRetriever) → top-k chunks appended to `state.evidence`; may call `get_document` for full context; drafts an answer with citations.
4. **reviewer** checks each claim is supported by `evidence` and that citations exist. On `revise` it writes feedback and the graph loops back to the specialist (max 2).
5. Final answer + citations returned.

## Request lifecycle (triage)

1. `cli triage "PagerDuty: payments-api p99 latency > 2s since 02:10"`
2. supervisor → `triage`.
3. triage agent: `get_active_alerts` → `get_service(payments-api)` (owner, on-call, dependencies) → `list_recent_deployments(service, hours=24)` → `search_tickets(similar)` → `search_knowledge(runbook for latency)`.
4. Produces a typed `TriageReport` (rendered to markdown); if it called `draft_ticket`, `collect_pending` records the draft as `pending_action`.
5. **reviewer** validates the report against the evidence (revise loop as above).
6. Graph **interrupts** at `human_approval`; the human approves or rejects in the CLI / `POST /approve` → draft marked in `data/.drafts/` → END.

## Key design decisions (see `docs/adr/`)

- **Hybrid retrieval over pure vector** — runbooks are full of exact identifiers (`payments-api`, `ERR_CONN_RESET`) where BM25 wins; concepts ("latency spike") where vectors win. RRF fusion needs no tuning.
- **MCP for all tools** — agents never import data access code directly; everything an agent can do is an MCP tool. Swapping fixtures for real Jira/PagerDuty is a server change, not an agent change.
- **Supervisor + specialists + reviewer** rather than one mega-agent — smaller prompts, clearer tool scopes, and a reviewer that can veto.
- **Human-in-the-loop for side effects** — anything that writes to the outside world goes through `interrupt()`.
- **Pluggable embeddings** — local ONNX by default (no key, no cost), Voyage for quality, hashing for CI.
- **Evals are part of the repo** — a golden set and judge so changes to chunking/prompts are measured, not vibes.

## Data

- `data/knowledge_base/` — synthetic but realistic markdown: runbooks, ADRs, postmortems, service pages for a fictional e-learning platform ("Navigator"). Front-matter: `id`, `title`, `kind`, `service`, `owner`, `updated`.
- `data/fixtures/` — `services.json`, `alerts.json`, `deployments.json`, `tickets.json`.
- `data/.index/chroma/`, `data/.index/bm25.pkl`, `data/.drafts/` — generated at runtime, git-ignored.

## Running

```
cp .env.example .env            # add ANTHROPIC_API_KEY
pip install -c requirements.lock -e ".[dev]"
ops-copilot ingest              # build index
ops-copilot ask "…"             # knowledge Q&A
ops-copilot triage "…"          # incident triage (asks approval before filing ticket)
ops-copilot release-notes --version 2.14.0
ops-copilot eval                # retrieval + faithfulness metrics
ops-copilot serve               # FastAPI on :8000
ops-copilot mcp knowledge       # run an MCP server standalone (e.g. for Claude Desktop)
```
