# Ops Copilot

**A multi-agent RAG + MCP assistant for on-call engineers — built on Claude, LangGraph and the Model Context Protocol.**

[![ci](https://github.com/hardyalsinghdeora/ops-copilot/actions/workflows/ci.yml/badge.svg)](https://github.com/hardyalsinghdeora/ops-copilot/actions/workflows/ci.yml)
![python](https://img.shields.io/badge/python-3.11%2B-blue)
![license](https://img.shields.io/badge/license-MIT-green)

> It's 2 a.m. PagerDuty says `payments-api` p99 latency is over 2 s. The runbook is somewhere in Confluence, the last deploy is in ArgoCD, a similar incident happened in August, and the on-call rotation changed yesterday. Ops Copilot pulls all of that together, cites its sources, drafts the ticket — and waits for a human before filing it.

## What it does

| Ask it… | Route | What happens |
|---|---|---|
| *"How do I rotate the payments DB credentials?"* | **knowledge** | Hybrid search (BM25 + embeddings) over runbooks/ADRs/postmortems → answer with `[doc#chunk]` citations |
| *"PagerDuty: payments-api p99 > 2 s since 02:10"* | **triage** | Pulls firing alerts, service owner/on-call, last 24 h of deployments, similar past incidents, the matching runbook → severity, suspected cause, next actions, **draft ticket → human approval** |
| *"Release notes for 2.14.0"* | **release_notes** | Gathers Done tickets for the fix version + production deployments → grouped release notes |

Every answer passes through a **reviewer agent** (LLM-as-judge) that checks it against the raw evidence and sends it back for revision if claims are unsupported.

## Architecture

```
 user ──▶ supervisor ──▶ knowledge / triage / release_notes ──▶ reviewer ──▶ human approval ──▶ answer
 (CLI/API)  (Haiku)         (Sonnet, ReAct over MCP tools)      (Haiku)      (interrupt())
                                   │                  │
                          knowledge MCP server   ops MCP server
                          (RAG: Chroma + BM25)   (alerts, deploys, tickets, services)
```

Full diagram and component table: [`docs/architecture.md`](docs/architecture.md).

**Stack:** Python 3.11+ · Anthropic Claude API (`claude-sonnet-5-5` / `claude-haiku-5-5`) · LangGraph 1.x · `langchain-mcp-adapters` · MCP Python SDK (FastMCP) · ChromaDB · rank-bm25 · FastAPI · Typer · pytest.

## Why this is more than a chatbot

- **RAG with hybrid retrieval** — BM25 for exact identifiers (`ERR_CONN_RESET`, `NAV-4821`), dense vectors for paraphrase, fused with Reciprocal Rank Fusion. Heading-aware chunking keeps "Symptoms" and "Mitigation" separate. Pluggable embeddings: local ONNX (default, free), Voyage AI, or hashing (CI).
- **MCP for every tool** — agents never touch data code; they call MCP tools. The same servers plug into Claude Desktop / Claude Code (`ops-copilot mcp-config`). Swapping JSON fixtures for real Jira/PagerDuty is a server change, not an agent change.
- **Multi-agent LangGraph** — supervisor (structured-output routing) → scoped specialists (each sees only the tools it needs) → reviewer (faithfulness + citation check, ≤2 revision rounds) → `interrupt()` for anything that writes to the world.
- **Evals in the repo** — a golden Q/A set, `hit@k` / MRR for retrieval, and an LLM-as-judge for faithfulness, all runnable with `ops-copilot eval`; the retrieval eval runs in CI on every push.
- **Tested offline** — scripted chat models + hashing embeddings mean the whole graph, including the interrupt/resume flow, is unit-tested without an API key.

## Quickstart

```bash
git clone https://github.com/hardyalsinghdeora/ops-copilot && cd ops-copilot
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
cp .env.example .env            # add ANTHROPIC_API_KEY

ops-copilot ingest              # ~30 s: downloads the small ONNX embedding model once, builds the index
ops-copilot ask "How do I rotate the payments DB credentials?"
ops-copilot triage "PagerDuty: payments-api p99 latency > 2s since 02:10"
ops-copilot release-notes --version 2.14.0
ops-copilot eval --compare      # retrieval metrics offline (hybrid vs bm25 vs vector); add --judge for faithfulness
ops-copilot serve               # FastAPI on http://localhost:8000/docs
```

No API key? `ingest`, `eval` (without `--judge`) and the full test-suite work offline. Set `OPS_COPILOT_EMBEDDING_PROVIDER=hashing` if you can't download the embedding model.

A sample of what `ops-copilot eval --compare` prints on the committed golden set (15 questions + 2 negatives) **with the hashing provider that CI uses**:

| metric | hybrid | bm25 only | vector only |
|---|---|---|---|
| hit@1 | 0.467 | 0.733 | 0.333 |
| hit@5 | 1.000 | 1.000 | 0.600 |
| mrr   | 0.700 | 0.844 | 0.413 |

Read that honestly: with *hashing* "embeddings" (a lexical stand-in, no semantics) fusing in the vector list drags the hybrid ranking down — BM25 alone wins. That is the point of the comparison mode: re-run it with the default local ONNX embeddings (or Voyage) and the vector and hybrid columns are what tell you whether semantic retrieval is earning its keep on *your* corpus. `docs/03-rag.md` and `docs/06-evaluation.md` walk through this experiment.

### Use the knowledge base from Claude Desktop / Claude Code

```bash
ops-copilot mcp-config          # prints an mcpServers block to paste into your config
```

## Repository layout

```
src/ops_copilot/
  rag/            loader → chunker → embeddings → ChromaStore + BM25Index → HybridRetriever
  mcp_servers/    knowledge_server.py, ops_server.py (FastMCP, stdio)
  mcp_client.py   MultiServerMCPClient → LangChain tools (or in-process "local" mode)
  agents/         state, supervisor, specialist (ReAct), knowledge/triage/release agents, reviewer, human_approval, graph
  evals/          golden.json, retrieval_eval (hit@k, MRR), judge (faithfulness), run
  api/main.py     FastAPI (/chat /triage /approve /release-notes /health)
  cli.py          Typer CLI
data/
  knowledge_base/ runbooks, adrs, postmortems, services (markdown + front-matter)
  fixtures/       services.json, alerts.json, deployments.json, tickets.json
docs/             learning path (00–07), architecture, ADRs, case study, glossary, reading list, exercises
tests/            offline tests: RAG, MCP servers, graph behaviour, evals, API
```

## Learning path

This repo doubles as a self-study course. Start at [`docs/README.md`](docs/README.md):

`00 Start here` → `01 LLM fundamentals` → `02 Prompting & tool use` → `03 RAG` → `04 MCP` → `05 Multi-agent LangGraph` → `06 Evaluation` → `07 Production`, plus a [4-week exercise plan](docs/exercises.md), a [reading list](docs/reading-list.md) and a [portfolio case study](docs/case-study.md).

## Running the tests

```bash
pytest -q -m "not slow"   # ~10 s, fully offline
pytest -q                 # + MCP stdio round-trip (spawns the servers as subprocesses)
```

## Status & honest limitations

This is a portfolio / learning project with synthetic data for a fictional platform ("Navigator"). The architecture is production-shaped; the data sources are not. See [`docs/07-production.md`](docs/07-production.md) for what changes for real use (real MCP servers, auth, persistence of checkpoints, observability, guardrails).

## License

MIT — see [LICENSE](LICENSE).
