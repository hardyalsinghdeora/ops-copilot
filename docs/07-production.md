# 07 — From Portfolio Project to Production

*Who this is for: engineers who have the system running and are asked "could we actually use this?" Time to complete: about 2 hours.*

The architecture is designed so that most of what follows is a server swap, a config change or an added layer, not a rewrite. This chapter goes through each area honestly: what the repo does now, what real use requires, and what the project's structure buys you.

## Real tool servers instead of fixtures

**Now.** `ops_copilot.mcp_servers.ops_server` reads `services.json`, `alerts.json`, `deployments.json`, `tickets.json`. The tool names and shapes mirror what the real systems expose.

**Production.** Replace the one fixture server with one MCP server per system:

| Tool today | Real backing | Server |
|---|---|---|
| `get_service` | Service catalogue (Backstage, OpsLevel, a YAML repo) | catalogue server |
| `get_active_alerts` | PagerDuty / Opsgenie incidents API | PagerDuty server |
| `list_recent_deployments` | ArgoCD application history, or your CD system's API | ArgoCD server |
| `search_tickets`, `get_ticket`, `draft_ticket` | Jira (JQL search, issue get, issue create) | Jira server |

Because the agents only know tool names and schemas, keeping the names stable means `triage_agent` and `release_agent` do not change. Several of these exist as community or vendor MCP servers already; the knowledge server stays yours.

Keep the fixture server. It is what tests and evals run against, and it is what lets a new contributor run the system without credentials.

## Authentication and secrets

**Now.** One `ANTHROPIC_API_KEY` and optionally `VOYAGE_API_KEY` in `.env`, read by `Settings`. The MCP servers run as the local user over stdio. The API can require one shared bearer token (`OPS_COPILOT_API_TOKEN`); there is no per-user identity.

**Production.**

- **Model API key** in a secrets manager, injected as an environment variable at deploy time, never in an image or repo. Use a per-environment key so you can see spend and revoke independently.
- **Tool credentials** belong to the MCP server, not the agent. The Jira server holds a Jira token scoped to the projects it needs. Prefer a service account with the minimum role; `draft_ticket` needs create on one project, not admin.
- **User identity.** The knowledge base has ACLs in real life. Either the retriever filters by the requesting user's permitted spaces (pass identity through `POST /chat` and into `search_knowledge` as a filter), or you only index documents everyone may read. Ignoring this makes the copilot a way to read documents you are not allowed to see.
- **Remote MCP** (streamable HTTP) needs the MCP authorization flow (OAuth, per the spec at modelcontextprotocol.io) and TLS. stdio was fine because it inherited the local user; a shared server has no such shortcut.

## Observability

**Now.** Console output from the CLI and FastAPI. The eval harness records token counts per run.

**Production.** Chapter 06 listed what to log; here is how.

- **Tracing.** Either LangSmith (set a few environment variables and every LangGraph run, node, model call and tool call is traced with inputs, outputs and tokens) or OpenTelemetry via the LangChain/LangGraph instrumentation, exported to whatever your platform already uses. Pick one; do not run both.
- **Structured logs** per request with `thread_id`, route, attempts, review verdict, tool call count, total tokens, latency, and the human decision if any.
- **Metrics** worth a dashboard: requests per route, p50/p95 latency per route, token spend per route per day, review `revise` rate, approval/rejection rate at `human_approval`, retrieval hit@k on a sampled set of production questions (where you can label them).
- **Alerting** on `revise` rate climbing (a prompt or data change hurt faithfulness), on error rate from any MCP server, and on spend.

Make traces loadable as golden-set cases. A production question that produced a bad answer should become `golden.json` entry number N within a day.

## Rate limits, retries and timeouts

**Now.** Default client behaviour.

**Production.**

- The Anthropic API returns 429 on rate limit and 529 on overload. `langchain-anthropic` has `max_retries`; set it, and add exponential backoff with jitter around the whole graph invocation for the cases that exhaust it.
- Set a **timeout per model call** and a **budget per request** (wall clock and tokens). A triage run that has made 20 tool calls and is still going should stop and return what it has with a warning, which the per-run tool-call cap in each specialist already approximates.
- MCP servers need the same: a timeout per tool call, retries on transient errors, and a circuit breaker so a dead Jira does not take down knowledge Q&A.
- Queue, do not drop. If you are rate-limited, a 202 with a job id is better than a 500.

## Caching

Three layers, each independent:

1. **Prompt caching** at the API (Chapter 01). System prompts and the MCP tool definitions are long and identical across calls; mark them as cacheable and keep them at the front of the prompt. This is the biggest single cost reduction available for the agent loops.
2. **Retrieval caching.** Identical or near-identical questions within a short window (during an incident, several people ask the same thing) can reuse the retrieved chunk set. Key on normalised query plus filter plus index version.
3. **Embedding caching.** At ingest, cache embeddings by content hash so re-ingesting an unchanged document is free. This also makes incremental ingestion cheap.

Do not cache final answers across users unless you have solved the ACL problem above.

## Streaming

**Now.** `POST /chat` returns the final answer when the graph finishes. A triage run can take tens of seconds.

**Production.** Stream two things:

- **Progress events**: "routing", "searching knowledge base", "checking recent deployments", "reviewing". LangGraph's `astream_events` emits node start/end and tool call events you can forward as server-sent events. This alone makes a 30-second run feel acceptable.
- **Token streaming** for the final answer, once the reviewer has passed. Streaming the draft before review would show the user text that may be revised; stream status during the run and tokens at the end.

Adding SSE to the FastAPI service is one of the stretch projects in [`exercises.md`](exercises.md).

## Guardrails

**Prompt injection from documents.** Covered in Chapter 02; in production, add:

- An ingestion-time scan that flags documents containing instruction-like text aimed at assistants, for human review before they enter the index.
- Clear delimiting of document content in `search_knowledge` results.
- Keep the write path (`human_approval`) non-negotiable. It is the backstop when the other layers fail.

**PII.** Postmortems and tickets contain names, emails, sometimes customer identifiers.

- Decide what the knowledge base may contain and enforce it at ingest (redaction or rejection).
- Tool results from Jira and PagerDuty will contain names; that is usually fine internally, but log scrubbing should remove them before traces leave your boundary.
- Do not send the knowledge base to a third-party embedding API (`VoyageEmbeddings`) without checking your data agreement; `LocalMiniLMEmbeddings` exists partly for this reason.

**Output guardrails.** The reviewer checks faithfulness. For a customer-facing status update you would add a second check for tone and for leaking internal detail.

## Cost controls

- **Per-request budget** (tokens and tool calls) enforced in the graph.
- **Per-user and per-day quotas** at the API layer.
- **Route-aware model choice.** The two-role design already puts the fast model on routing and judging. Watch for specialists that could drop a tier for simple questions, and for the reverse.
- **Smaller k, fewer retries.** Each is a multiplier on input tokens.
- **Prompt caching**, above.
- **Report spend per route per day.** You cannot control what you cannot see.

## Deployment

**Now.** `ops-copilot serve` runs uvicorn on `:8000` (or `$PORT`) with a chat page at `/` and `POST /chat`, `POST /triage`, `POST /approve`, `POST /release-notes`, `GET /health`. `render.yaml` deploys the Docker image to Render as one always-on instance (see the README). The checkpointer is in memory.

**Production.**

- **Container.** The repo's `Dockerfile` installs the package (pinned by `requirements.lock`), runs `ops-copilot ingest` at build time so the index and ONNX embedding model are baked in (startup does not download), runs as a non-root user with a `HEALTHCHECK`, and starts `ops-copilot serve`; CI builds it and probes `/health`. In production the ingest step can also be an init container. The MCP servers run as subprocesses inside the same container over stdio; that is fine until you want to share them, at which point they become their own deployments over streamable HTTP.
- **Persistent checkpointer.** `interrupt()` with `MemorySaver` cannot survive a restart or a second replica. Use the Postgres or SQLite checkpointer from LangGraph so an approval started on one replica can resume on another.
- **Index as an artefact.** Build `data/.index/chroma` and `data/.index/bm25.pkl` in CI, version them with the knowledge-base commit, mount them read-only. Rebuild on knowledge-base change, not on API deploy.
- **Health.** `GET /health` pings each MCP server and answers 503 when one stops responding, so the host restarts the service; it also reports whether the index exists.
- **Human approval over HTTP.** The API already works this way: when triage drafts a ticket, `POST /triage` returns the `thread_id` and `pending_approval` (the drafted ticket); `POST /approve` with that thread id and the decision resumes the graph with `Command(resume=...)`, and new messages on the thread get a 409 until then. In Slack this is two buttons.

## Honest limitations

- **Batch ingestion only.** No incremental updates, no deletion handling, no freshness signal in ranking. A runbook edited this morning is invisible until someone re-ingests.
- **No access control** in retrieval. Everything indexed is visible to every caller.
- **Single-tenant, single-process.** In-memory checkpointer; subprocess MCP servers; no horizontal scaling story without the changes above.
- **Small-corpus assumptions.** BM25 via `rank-bm25` holds the whole index in memory and pickles it; fine for hundreds of documents, not for a 100,000-page wiki. Chroma embedded mode has similar limits. At scale you move to a search engine with native hybrid support (OpenSearch, Elasticsearch, Vespa, or a managed vector database) and keep the `HybridRetriever` interface.
- **The reviewer is a model.** It catches most unsupported claims; it does not catch all, and it adds a model call to every request. The two-round cap means a persistently bad answer is shown with a warning, not blocked.
- **Evals are small and synthetic.** The golden set is written by the author against a synthetic knowledge base. Real questions are messier.
- **No streaming, no async job model**, and only one shared access token on the API (no per-user identity or quotas).
- **Fixture data is not real data.** The triage flow has never been run against a real PagerDuty incident with real noise.

None of these are surprises; they are the gap between a project that demonstrates an architecture and a service a platform team runs. The point of listing them is that you can name every one in an interview and say what you would do about it.

## Common pitfalls

- Deploying the API with `MemorySaver` and discovering approvals vanish on restart.
- Indexing documents with ACLs and discovering the copilot leaks them.
- Running both LangSmith and OTel and paying twice for the same traces.
- Treating the fixture server as throwaway and losing the ability to run tests offline.
- Adding a real `create_ticket` tool without routing it through `human_approval`.

## Exercises

1. **Dockerfile.** Build the repo's image (`docker build -t ops-copilot .`), run it with your API key and confirm `GET /health` responds. Then run the ingest step with `OPS_COPILOT_EMBEDDING_PROVIDER=hashing` (and the container with the same setting: an index refuses a different provider) and compare image size and build time with the default local ONNX provider.
2. **Persistent checkpointer.** Swap `MemorySaver` for LangGraph's SQLite checkpointer in `serve`. Start a triage via `POST /triage`, restart the process, and resume the approval. Confirm the ticket is filed only after the resume.
3. **Progress streaming.** Add an SSE endpoint that forwards LangGraph node start/end events for a `POST /chat` run. Measure perceived latency with and without it.
4. **Threat model.** Write a one-page list of the ways an attacker who can edit one runbook could influence the system, and which existing layer (prompt, scoping, reviewer, human approval) stops each. Identify the one that nothing currently stops.

## Further reading

- Anthropic documentation: rate limits, errors and retries, prompt caching, streaming (docs.claude.com).
- LangGraph documentation: persistence and checkpointers, `astream_events`, deployment options (langchain-ai.github.io/langgraph).
- Model Context Protocol specification: transports and authorization (modelcontextprotocol.io).
- OWASP Top 10 for Large Language Model Applications (OWASP Foundation), for a structured list of risks including prompt injection and excessive agency.
- *Building effective agents* guidance from Anthropic (docs.claude.com / anthropic.com), for the argument to keep agent systems as simple as the task allows.
