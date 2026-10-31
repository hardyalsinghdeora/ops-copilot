# Case Study: Ops Copilot

*Who this is for: anyone reviewing this project as a portfolio piece, and me preparing to talk about it. Time to read: about 10 minutes.*

**Author:** Hardyal Singh Deora, backend engineer learning applied AI.
**Repository:** `ops-copilot` — Engineering Ops Copilot, a multi-agent RAG + MCP assistant for on-call engineers.
**Stack:** Python 3.11, Anthropic Claude via `langchain-anthropic`, LangGraph 1.x, Model Context Protocol (`mcp` SDK / FastMCP, `langchain-mcp-adapters`), ChromaDB, `rank-bm25`, ONNX sentence embeddings, Pydantic, FastAPI, Typer.

## Problem

On-call engineers lose time at the worst moment hunting for tribal knowledge spread across runbooks, ADRs, postmortems, service catalogues, ticket trackers and deployment history. After an incident, the same people write status updates and follow-up tickets; at release time, someone turns a sprint's tickets into release notes. A general-purpose LLM cannot help directly: it has not seen the private documents, cannot see live system state, and will confidently invent answers.

## Approach

Combine four techniques so that each covers the others' weaknesses:

1. **Retrieval-Augmented Generation** over the team's markdown knowledge base, using hybrid lexical plus semantic search so that both exact identifiers and paraphrased concepts are found, with every answer citing its sources.
2. **Model Context Protocol** as the only way agents reach data: a knowledge server exposing the retriever, and an ops server exposing service, alert, deployment and ticket tools. Fixtures today; real Jira/PagerDuty/ArgoCD servers are a swap.
3. **A supervisor / specialist / reviewer multi-agent graph** in LangGraph: a fast router, three focused specialists (knowledge Q&A, incident triage, release notes) each with a scoped tool set, and an independent LLM-as-judge reviewer that can send a draft back for revision.
4. **Human-in-the-loop** for side effects: the only write action, filing a ticket, pauses the graph with `interrupt()` and waits for explicit approval.

Plus an **evaluation harness in the repo** so retrieval and faithfulness are measured on every change.

## Architecture

See the diagram and component table in [`architecture.md`](architecture.md). In one paragraph: a CLI or FastAPI request enters a LangGraph `StateGraph`; the supervisor (Haiku, structured output) routes to one of three specialists (Sonnet, ReAct loop over `bind_tools`); specialists call tools that are served by two FastMCP servers over stdio and loaded through `MultiServerMCPClient`; the knowledge server fronts a RAG pipeline (front-matter loader → heading-aware chunker → pluggable embeddings → Chroma + BM25 → Reciprocal Rank Fusion); the triage path interrupts at `human_approval` before a drafted ticket is filed; every draft passes through a reviewer that checks faithfulness and citation integrity against the gathered evidence, with at most two revision rounds.

## What I built

- **RAG pipeline** (`ops_copilot.rag`): YAML front-matter loader; heading-aware chunker with token-based sizing and overlap; `EmbeddingProvider` protocol with three implementations (local ONNX MiniLM default, Voyage, deterministic hashing for CI); persistent `ChromaStore`; `HybridRetriever` fusing BM25 and vector ranks with RRF and supporting metadata filters; `ingest` pipeline persisting both indexes.
- **Two MCP servers** (`ops_copilot.mcp_servers`): `knowledge_server` (`search_knowledge`, `get_document`, `list_documents`) and `ops_server` (`get_service`, `get_active_alerts`, `list_recent_deployments`, `search_tickets`, `get_ticket`, `draft_ticket`), both built with FastMCP decorators and runnable standalone for Claude Desktop or Claude Code.
- **MCP client integration** (`ops_copilot.mcp_client`): long-lived sessions (one process per server) turned into LangChain tools via `langchain-mcp-adapters`.
- **Multi-agent graph** (`ops_copilot.agents`): `CopilotState` TypedDict with message reducer; structured-output `supervisor`; `knowledge_agent`, `triage_agent` (producing a typed `TriageReport`), `release_agent`; `reviewer` LLM-as-judge with pass/revise and a two-round cap; `human_approval` interrupt node; `build_graph` wiring with a checkpointer.
- **Interfaces**: Typer CLI (`ingest`, `ask`, `triage`, `release-notes`, `eval`, `serve`, `mcp`) and FastAPI service (`POST /chat`, `POST /triage`, `POST /approve`, `POST /release-notes`, `GET /health`, chat page at `/`), deployable to Render from `render.yaml`.
- **Evaluation harness** (`ops_copilot.evals`): hand-written golden set including negatives; `retrieval_eval` computing hit@k and MRR; `judge` computing faithfulness and answer relevance with the fast model; token and latency accounting.
- **Synthetic knowledge base and fixtures** for a fictional e-learning platform, realistic enough to exercise identifier-heavy and concept-heavy retrieval.
- **Documentation**: this docs folder, written as a self-study course, plus five ADRs.

## Results

The eval harness is the result. It measures, on the golden set in `src/ops_copilot/evals/golden.json`:

- **Retrieval:** hit@1, hit@3, hit@5 and MRR for the hybrid retriever, and for BM25-only and vector-only when run in comparison mode, so the value of fusion is a number rather than a claim.
- **Generation:** faithfulness (fraction of claims supported by retrieved evidence) and answer relevance per route, plus the pass rate on negative cases (questions the knowledge base cannot answer).
- **Operational:** input/output tokens, tool-call count and wall-clock latency per route.

Current figures: see the output of `ops-copilot eval --compare` on the committed knowledge base and golden set; the harness is the source of truth and numbers move with every chunking or prompt change. One snapshot is worth recording because of what it teaches. With the **hashing** provider (the offline stand-in CI uses, which has no semantics):

| metric | hybrid | bm25 only | vector only |
|---|---|---|---|
| hit@1 | 0.562 | 0.812 | 0.250 |
| hit@5 | 1.000 | 1.000 | 0.625 |
| mrr   | 0.760 | 0.896 | 0.347 |

BM25 alone beats the hybrid here: fusing in a noisy ranked list *hurts*. That is the correct behaviour of RRF given a bad second retriever, and it is why the comparison mode exists — the decision to run hybrid has to be re-justified per corpus and per embedding model, with the local ONNX or Voyage provider, not assumed. The interview question "why hybrid?" has the data-driven answer, including the case where the answer is "don't".

## What I learned

- **Retrieval quality is most of answer quality.** Almost every bad answer traced back to the right chunk not being in the top five, not to the model misreading it. Hybrid search with RRF fixed the identifier cases that pure vector search kept missing.
- **Structured output everywhere code branches.** Parsing prose from the router or reviewer was fragile; Pydantic schemas via `with_structured_output` made the graph deterministic to reason about.
- **Tool descriptions are prompts.** The single highest-leverage edit to agent behaviour was rewriting tool docstrings to say when to call them.
- **A separate reviewer catches things the author cannot.** Asking the same context "are you sure?" returns yes. A fresh context with only draft and evidence returns useful objections.
- **Bound everything.** Attempt counters, tool-call caps and small k are not optional; without them cost and latency are unbounded.
- **Evals change how you work.** Once `ops-copilot eval` existed, tuning became an experiment loop instead of guesswork.
- **MCP's value is the boundary.** Writing the servers took no longer than writing plain tool functions, and in return the same knowledge server works inside Claude Desktop with zero changes.

## Resume bullet points

- Designed and built a supervisor / specialist / reviewer multi-agent system in LangGraph with human-in-the-loop approval for side-effecting actions, using Claude Sonnet for specialists and Haiku for routing and judging.
- Implemented a hybrid retrieval pipeline (heading-aware chunking, pluggable ONNX/Voyage/hashing embeddings, ChromaDB plus BM25 fused with Reciprocal Rank Fusion, metadata filtering) with per-claim `[doc_id#chunk]` citations.
- Exposed knowledge search and operational data (services, alerts, deployments, tickets) as two Model Context Protocol servers with FastMCP, consumed in-app via `langchain-mcp-adapters` and directly from Claude Desktop.
- Built an LLM-as-judge reviewer enforcing faithfulness and citation integrity with a bounded revision loop, and an evaluation harness measuring hit@k, MRR, faithfulness, answer relevance, tokens and latency against a hand-built golden set.
- Shipped CLI (Typer) and HTTP (FastAPI) interfaces, offline-capable CI via deterministic embeddings, and documentation including five architecture decision records.
- Authored a self-study curriculum on applied LLM engineering (fundamentals, prompting and tool use, RAG, MCP, multi-agent systems, evaluation, production) using the project as the worked example.

## Interview talking points

**Why hybrid retrieval instead of just vector search?**
Runbooks are full of exact identifiers: service names, error codes, ticket ids. Embedding models treat those as rare tokens with little semantic weight, so two different error codes can look identical in vector space. BM25 nails exact matches but cannot connect "slow after deploys" to "p99 latency regression". Running both and fusing with Reciprocal Rank Fusion gets the best of each, and RRF uses only ranks so there is no score normalisation to tune. The eval harness lets me show the difference as hit@k numbers rather than argue it.

**Why MCP rather than ordinary LangChain tools?**
The agents never import data-access code; every capability is a tool served by a separate process over a standard protocol. That gives three things: the tool list is the complete, auditable description of what agents can do; swapping fixtures for a real Jira server changes no agent code; and the same knowledge server plugs into Claude Desktop or Claude Code with a config entry. The cost was roughly zero: FastMCP turns a decorated function into a tool.

**How do you stop it hallucinating?**
In layers. Retrieval puts the facts in the prompt. The knowledge agent's prompt requires answering only from evidence, citing each claim, and saying "not found" when the evidence is missing. A separate reviewer with a fresh context checks each claim against the evidence and each citation against the retrieved ids, and sends the draft back with feedback up to twice. The eval judge measures faithfulness on a golden set that includes unanswerable questions, so a prompt change that increases invention shows up as a number. None of these is sufficient alone.

**Why multiple agents instead of one?**
Prompt focus, tool scoping and veto. Three short prompts each with one job are followed more reliably than one long conditional prompt. Each specialist gets only the tools it needs, which also limits what a prompt-injected document could make it do. And a reviewer that did not write the draft can reject it; the same context asked to self-check will usually approve itself. The trade-off is coordination, which is what LangGraph's state graph, reducers and conditional edges handle.

**What would you change for production?**
Incremental ingestion with ACL-aware retrieval; a persistent checkpointer so `interrupt()` survives restarts and multiple replicas; one MCP server per real system with scoped credentials over streamable HTTP with auth; tracing via LangSmith or OpenTelemetry with token and cost metrics per route; prompt caching for the long system prompts and tool schemas; streaming progress events; and a larger golden set grown from real production questions. I have a chapter in the docs that goes through each with the specific change.

**How do you know a change made it better?**
`ops-copilot eval`. Retrieval metrics run in seconds without a model; the judge runs over the full pipeline. I change one variable, re-ingest if needed, run the evals, and look at the cases that flipped rather than only the averages. Chunk size, overlap, RRF k, embedding provider and prompt wording were all tuned that way.
