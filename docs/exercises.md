# Four-Week Self-Study Plan

*Who this is for: an engineer studying in the evenings, about one hour per weekday, who wants to finish with a working understanding of every part of Ops Copilot and one meaningful extension. Time to complete: four weeks, roughly 20 to 24 hours.*

The plan assumes Monday to Friday, one hour each, with weekends free. If you have more time on a weekend, spend it on the stretch project rather than reading ahead. Each week has reading, exercises taken from the chapters, and one stretch project that adds something real to the repo. The stretch projects are ordered by difficulty; each one is independent, so skip or swap as you like.

Before Week 1, complete the quickstart in [`00-start-here.md`](00-start-here.md) so that `ops-copilot ingest`, `ask` and `eval` all run on your machine.

## Week 1 — Foundations, prompting, and the tool loop

**Goal:** understand what a model call costs and contains, and be able to write and debug a tool-calling loop.

| Day | Read | Do |
|---|---|---|
| Mon | [`00-start-here.md`](00-start-here.md) in full; run every quickstart command | Note which commands needed the API key and which did not |
| Tue | [`01-llm-fundamentals.md`](01-llm-fundamentals.md) | Exercise 01.1 (token budgeting) |
| Wed | Finish Chapter 01; skim the Anthropic "Models" and "Pricing" pages | Exercise 01.4 (structured output by hand) |
| Thu | [`02-prompting-and-tool-use.md`](02-prompting-and-tool-use.md) up to "How the specialists implement the loop" | Exercise 02.2 (trace the loop) |
| Fri | Finish Chapter 02 | Exercise 02.1 (describe a tool badly, then well) |

**Stretch project — a scratch ReAct agent.** Outside the main graph, write a 60-line script that uses `get_llm("strong").bind_tools([...])` with two of the knowledge tools loaded via `load_mcp_tools()`, and a hand-written while-loop (no LangGraph) that runs until the model answers. Add a hard cap of six tool calls. You will understand `ToolNode` much better after writing the loop it replaces.

## Week 2 — Retrieval

**Goal:** be able to explain and tune every stage of the RAG pipeline, and measure the result.

| Day | Read | Do |
|---|---|---|
| Mon | [`03-rag.md`](03-rag.md), steps 1 to 3 (loader, chunker, embeddings) | Read `ops_copilot/rag/loader.py` and `chunker.py` alongside |
| Tue | Chapter 03, steps 4 to 6 (store, BM25, RRF) | Exercise 03.1 (see the two rankers disagree) |
| Wed | Chapter 03 to the end; [`adr/0001-hybrid-retrieval.md`](adr/0001-hybrid-retrieval.md) | Exercise 03.3 (provider swap with hashing) |
| Thu | [`06-evaluation.md`](06-evaluation.md), "Retrieval metrics" section only | Exercise 06.2 (reproduce the worked hit@k / MRR example) |
| Fri | [`adr/0005-pluggable-embeddings.md`](adr/0005-pluggable-embeddings.md); re-read the RRF section | Exercise 03.2 (chunk-size A/B); record results in a note |

**Stretch project — add a re-ranker.** Add an optional cross-encoder re-ranking step to `HybridRetriever`: over-fetch 20 candidates via RRF, score each (query, chunk) pair with a small cross-encoder model run through ONNX or `sentence-transformers`, return the top k. Gate it behind a `Settings` flag so the default path is unchanged. Then run the retrieval evals with it off and on and write down hit@1, hit@5 and MRR for both. Whether it helps on this corpus is the point of the exercise.

## Week 3 — MCP and the multi-agent graph

**Goal:** be able to write an MCP server, consume it from two different hosts, and explain every node and edge in `build_graph`.

| Day | Read | Do |
|---|---|---|
| Mon | [`04-mcp.md`](04-mcp.md) to "Consuming the servers" | Exercise 04.1 (inspect the protocol with the MCP Inspector) |
| Tue | Finish Chapter 04; [`adr/0002-mcp-for-all-tools.md`](adr/0002-mcp-for-all-tools.md) | Exercise 04.3 (attach `knowledge_server` to Claude Desktop or Claude Code) |
| Wed | [`05-multi-agent-langgraph.md`](05-multi-agent-langgraph.md) to "The project's graph" | Exercise 05.1 (print state after every node) |
| Thu | Chapter 05, reviewer and failure modes; [`adr/0003`](adr/0003-supervisor-specialists-reviewer.md) and [`adr/0004`](adr/0004-human-in-the-loop-side-effects.md) | Exercise 05.3 (reject a ticket, inspect the checkpoint) |
| Fri | Finish Chapter 05; LangGraph human-in-the-loop docs | Exercise 02.3 (inject and defend), now that you understand both the tool boundary and the reviewer |

**Stretch project — a real GitHub MCP server.** Add a third server to the connection map in `ops_copilot.mcp_client.mcp_server_config()` that exposes a GitHub integration: either the official GitHub MCP server (configured with a token scoped to one repository) or a small FastMCP server of your own with `list_recent_prs(repo, days)` and `get_pr(repo, number)` over the GitHub REST API. Give `release_agent` access to the PR tools so release notes can link tickets to merged pull requests. Keep every tool read-only. Add one golden-set case that depends on PR data.

## Week 4 — Evaluation, production, and extending the system

**Goal:** be able to say how good the system is, what would change for real use, and ship one new capability end to end.

| Day | Read | Do |
|---|---|---|
| Mon | [`06-evaluation.md`](06-evaluation.md) in full | Exercise 06.1 (write five golden cases, including a negative) |
| Tue | Re-read the judge section; Zheng et al. on LLM-as-judge biases | Exercise 06.3 (judge the judge) |
| Wed | [`07-production.md`](07-production.md) to "Guardrails" | Exercise 07.4 (threat model) |
| Thu | Finish Chapter 07 | Exercise 07.2 (persistent checkpointer) |
| Fri | [`case-study.md`](case-study.md); draft your own version of the resume bullets in your words | Exercise 06.4 (cost report per route) |

**Stretch project — choose one.**

- **Add streaming to the API.** An SSE endpoint on the FastAPI service that forwards LangGraph `astream_events` (node start/end, tool calls) as progress events during a `POST /chat` or `POST /triage` run, then streams the final answer tokens after the reviewer passes. Measure perceived latency before and after.
- **Add a fourth agent.** A `status_update` specialist that writes a short customer-facing incident update from a `TriageReport` and current alerts. Define its tool scope (it should need only `get_active_alerts`, `get_service` and `search_knowledge`), add few-shot examples to the supervisor, wire it into `build_graph` through the reviewer, and add two golden cases. Add a tone check to the reviewer prompt for this route only.

## How to tell you are done

By the end of Week 4 you should be able to, without notes:

- Draw the architecture diagram from `architecture.md` and name the module behind each box.
- Explain why the retriever uses both BM25 and vectors, and write the RRF formula.
- Describe the `tool_use` / `tool_result` loop and where `bind_tools` and `ToolNode` fit.
- Write a FastMCP tool and say what the model sees from it.
- Explain `interrupt()` and why the checkpointer matters for it.
- Quote the current hit@5, MRR and faithfulness from your last `ops-copilot eval` run and say what you changed to move them.
- Answer every question in the "Interview talking points" section of [`case-study.md`](case-study.md) in your own words.

## If you fall behind

Drop the stretch projects before dropping the exercises, and drop exercises before dropping reading. The chapters are designed to be useful on their own; the exercises are where the understanding sticks; the stretch projects are for portfolio depth. One finished stretch project is worth more than three half-done ones.
