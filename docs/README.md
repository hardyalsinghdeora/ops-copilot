# Ops Copilot — Documentation and Self-Study Guide

This folder is two things at once:

1. **Project documentation** for Ops Copilot, a multi-agent RAG + MCP assistant for on-call engineers.
2. **A self-study course** in applied LLM engineering, using this repository as the worked example.

Every chapter follows the same shape: *concept → why it matters → how this project implements it → common pitfalls → exercises → further reading*. The implementation sections reference the exact modules, functions and tool names from [`architecture.md`](architecture.md), which is the authoritative spec for the codebase.

Author: Hardyal Singh Deora. Audience: experienced software engineers who are new to LLM engineering.

## Prerequisites

- Comfortable with Python 3.11+ (type hints, `async`/`await`, dataclasses or Pydantic).
- Have built or operated a backend service; know what a runbook, an on-call rotation and a postmortem are.
- No prior ML or LLM experience is assumed. Chapter 01 covers the minimum you need.
- An Anthropic API key for the chapters that call the model. Retrieval, ingestion and the retrieval evals run offline with the hashing or local embedding provider.

## How to use this guide

Read → run → do the exercises. In that order.

1. **Read** the chapter. Each is written to be finished in one sitting.
2. **Run** the commands in the "How this project implements it" section against this repo. Reading about Reciprocal Rank Fusion is not the same as watching `ops-copilot ask` pull a chunk that BM25 found and the vector search missed.
3. **Do the exercises.** They are small, hands-on, and live inside this repo. They are where the learning actually happens.

If you are following the four-week evening plan, use [`exercises.md`](exercises.md) as your schedule; it tells you which chapters and exercises to do each week and gives one stretch project per week.

## Learning path

| # | Chapter | What you will be able to do afterwards | Est. time |
|---|---|---|---|
| — | [`00-start-here.md`](00-start-here.md) | Explain the problem, the solution shape, and run the system end to end | 1 h |
| 01 | [`01-llm-fundamentals.md`](01-llm-fundamentals.md) | Reason about tokens, context, temperature, hallucination, cost and model tiers | 2 h |
| 02 | [`02-prompting-and-tool-use.md`](02-prompting-and-tool-use.md) | Write system prompts, get structured output, implement a tool-calling (ReAct) loop | 3 h |
| 03 | [`03-rag.md`](03-rag.md) | Build and tune a hybrid retrieval pipeline with citations | 4 h |
| 04 | [`04-mcp.md`](04-mcp.md) | Write an MCP server, consume it from LangChain and from Claude Desktop | 3 h |
| 05 | [`05-multi-agent-langgraph.md`](05-multi-agent-langgraph.md) | Design a supervisor/specialist/reviewer graph with human-in-the-loop | 4 h |
| 06 | [`06-evaluation.md`](06-evaluation.md) | Measure retrieval and answer quality instead of guessing | 2.5 h |
| 07 | [`07-production.md`](07-production.md) | Know what changes between a portfolio project and a production system | 2 h |
| — | [`case-study.md`](case-study.md) | Describe the project in a portfolio or interview | 0.5 h |
| — | [`glossary.md`](glossary.md) | Look up terms as you go | reference |
| — | [`reading-list.md`](reading-list.md) | Go deeper on any topic | reference |
| — | [`exercises.md`](exercises.md) | Four-week evening study plan | reference |
| — | [`adr/`](adr/) | Understand why the five key design decisions were made | 1 h |

Total guided reading and exercises: roughly 23 hours, which is the basis of the four-week plan at about one hour per weekday.

## Map: which chapter explains which part of the architecture

The diagram in [`architecture.md`](architecture.md) has four regions. Here is where each is explained.

```
                        ┌─────────────────────────────────────────────┐
  user (CLI / HTTP)     │              LangGraph  (graph.py)           │   ← Chapter 05
  ─────────────────────▶│  supervisor ──▶ knowledge ─┐                 │   ← 05 (supervisor), 02 (ReAct specialists)
   ↑ 00, 07             │      │          triage    ─┼▶ reviewer ──▶ END   ← 05 (reviewer), 06 (LLM-as-judge)
                        │      │          release   ─┘      │          │
                        │      └──────── (routing) ◀──retry─┘          │   ← 05 (retry loop, interrupt)
                        └───────────┬───────────────────┬─────────────┘
                                    │ MCP (stdio)       │ MCP (stdio)        ← Chapter 04
                         ┌──────────▼──────────┐ ┌──────▼─────────────┐
                         │  knowledge_server   │ │    ops_server      │   ← 04 (FastMCP servers)
                         └──────────┬──────────┘ └──────┬─────────────┘
                         ┌──────────▼──────────┐        │ JSON fixtures     ← 07 (what replaces fixtures)
                         │  RAG pipeline       │        │
                         │ loader → chunker →  │                           ← Chapter 03
                         │ embeddings → Chroma │
                         │ + BM25 → RRF fusion │
                         └──────────┬──────────┘
                                    │ data/knowledge_base/**/*.md           ← 00 (data), 03 (loader)
```

| Architecture element | Module(s) | Chapter |
|---|---|---|
| `Settings`, model roles `strong` / `fast` | `ops_copilot.config`, `ops_copilot.llm` | 01 |
| Loader, chunker, embeddings, `ChromaStore`, `HybridRetriever`, `ingest` | `ops_copilot.rag.*` | 03 |
| `knowledge_server`, `ops_server`, `load_mcp_tools` | `ops_copilot.mcp_servers.*`, `ops_copilot.mcp_client` | 04 |
| `CopilotState`, `supervisor`, specialists, `reviewer`, `human_approval`, `build_graph` | `ops_copilot.agents.*` | 05 (and 02 for the ReAct loop inside each specialist) |
| `golden.json`, `retrieval_eval`, `judge` | `ops_copilot.evals` | 06 |
| `cli`, `api.main` | `ops_copilot.cli`, `ops_copilot.api.main` | 00 (usage), 07 (deployment) |
| Design decisions | — | `adr/0001` to `adr/0005` |

## Conventions used in these docs

- Code snippets are small and illustrative. They use the real names from the spec but are not the full implementation; read the source for that.
- Commands are shown as the installed console script, `ops-copilot …`. If you have not run `pip install -e ".[dev]"`, substitute `python -m ops_copilot.cli …`.
- Model roles are referred to as **strong** (Sonnet, model ID `claude-sonnet-5-5`) and **fast** (Haiku, model ID `claude-haiku-5-5`). The IDs live in `Settings`; the docs only ever mention them in Chapter 01.
- "Evidence" means the list of retrieved chunks and tool results stored in `CopilotState.evidence`. "Grounded" means an answer whose claims are supported by evidence.
