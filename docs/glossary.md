# Glossary

Terms used across these docs and in the Ops Copilot codebase, alphabetically. Where a term maps to a specific module or name in the spec, it is given in parentheses.

**Agent.** A loop in which a model repeatedly decides whether to call a tool or answer, with tool results fed back into its context. Each specialist in `ops_copilot.agents` is one.

**Answer relevance.** An LLM-as-judge metric for whether an answer addresses the question asked, independent of whether it is faithful to the evidence. Computed by `ops_copilot.evals.judge`.

**Attention.** The transformer mechanism by which each position in the input computes how much every other position should influence it. The reason models can use context, and the reason long contexts dilute it.

**Autoregressive.** Generating output one token at a time, each conditioned on all previous tokens. Why output tokens are slower and cost more than input tokens.

**BM25.** A lexical ranking function that scores documents by term frequency, inverse document frequency and length normalisation. Excellent at exact identifiers; used via `rank-bm25` in `HybridRetriever`.

**Checkpointer.** A LangGraph component that persists graph state after every node, keyed by thread id, enabling interrupts, resumption and multi-turn memory. Passed to `build_graph`.

**Chunk / chunking.** Splitting a document into passages small enough to embed and retrieve individually. `ops_copilot.rag.chunker` does heading-aware chunking with overlap and produces `Chunk(id, doc_id, heading, text, metadata)`.

**Citation.** An inline reference from an answer to the evidence supporting it; here in the form `[doc_id#chunk]`. Checked by the reviewer and by evals.

**Conditional edge.** A LangGraph edge whose target is chosen at runtime by a function of the state. Used after the supervisor (routing) and after the reviewer (pass, retry or end).

**Context window.** The maximum number of tokens a model can see in one call, including system prompt, history, tool definitions, tool results and its own output.

**Cosine similarity.** The cosine of the angle between two vectors; the standard measure of closeness between embeddings.

**Cross-encoder / re-ranker.** A model that scores a query and a candidate passage together, more accurately than embeddings but more expensively. Not in the spec; the first stretch project.

**Embedding.** A fixed-length vector representation of text such that semantically similar texts are close together. Produced by an `EmbeddingProvider` in `ops_copilot.rag.embeddings`.

**Evidence.** The retrieved chunks and tool results gathered during a run, stored in `CopilotState.evidence`; what the reviewer checks the draft against.

**Faithfulness.** The fraction of claims in an answer that are supported by the provided evidence. The reviewer's main check and the judge's main metric.

**FastMCP.** The high-level server API in the `mcp` Python SDK; `@mcp.tool()` turns a function into an MCP tool using its signature and docstring. Used in both MCP servers.

**Few-shot.** Including worked examples of input and desired output in a prompt. Used in the supervisor's routing prompt.

**Front-matter.** YAML metadata at the top of a markdown file (`id`, `title`, `kind`, `service`, `owner`, `updated`). Parsed by `ops_copilot.rag.loader`.

**Golden set.** A hand-written set of questions with expected documents and reference answers used for evaluation (`src/ops_copilot/evals/golden.json`).

**Grounding.** Constraining a model to answer from supplied evidence rather than from its training data. Achieved by retrieval plus prompt plus review.

**Hallucination.** Fluent, confident output that is not supported by any fact the model actually has. The failure RAG, citations and the reviewer exist to prevent and detect.

**Hashing embeddings.** A deterministic, model-free embedding via feature hashing of tokens (`HashingEmbeddings`). Not semantic; used so tests and CI run offline.

**Hit@k.** The fraction of eval questions for which an expected document appears in the top k retrieved results. Computed by `retrieval_eval`.

**Host / client / server (MCP).** The application the user talks to (host), the connector inside it that holds one server connection (client), and the process exposing tools, resources and prompts (server).

**Human-in-the-loop.** Pausing an automated flow for a person to decide. Implemented by `ops_copilot.agents.human_approval` with `interrupt()` before a ticket is filed.

**HyDE.** Hypothetical Document Embeddings: generate a hypothetical answer, embed it, and retrieve with that vector. A technique for bridging phrasing gaps between questions and documents.

**Hybrid retrieval.** Combining lexical (BM25) and semantic (vector) search. `HybridRetriever` fuses them with RRF.

**Ingestion.** The offline pipeline that loads, chunks, embeds and indexes the knowledge base (`ops_copilot.rag.ingest`, run by `ops-copilot ingest`).

**Instruction tuning.** Fine-tuning a pretrained model on prompt/response pairs so it follows instructions and behaves as an assistant.

**interrupt() / Command(resume=...).** LangGraph primitives: `interrupt(payload)` pauses the graph inside a node and surfaces the payload; invoking with `Command(resume=value)` continues it with that value returned from `interrupt`.

**LLM-as-judge.** Using a model to evaluate another model's output against a rubric. The reviewer (in the graph) and `evals.judge` (in the harness) are both instances.

**Lost in the Middle.** The finding (Liu et al., 2023) that models recall information placed in the middle of a long context worse than at the start or end. Argues for small k and careful ordering.

**MCP (Model Context Protocol).** An open JSON-RPC protocol for exposing tools, resources and prompts to LLM applications over stdio or streamable HTTP. The only way agents in this project reach data.

**MessagesState.** LangGraph's prebuilt state type holding a `messages` list with the `add_messages` reducer. `CopilotState` follows the same pattern with extra fields.

**Metadata filter.** Restricting retrieval to chunks whose metadata matches (for example `kind=runbook`, `service=payments-api`). Supported by `HybridRetriever.search` and exposed through `search_knowledge`.

**MRR (Mean Reciprocal Rank).** The average over eval questions of 1 divided by the rank of the first correct result (0 if absent). Rewards ranking the right document high, not just somewhere in the top k.

**Node.** A function in a LangGraph graph that takes the state and returns a partial update.

**ONNX.** An open format for running trained models with a lightweight runtime; `LocalMiniLM` runs a sentence-embedding model this way on CPU without a deep-learning framework.

**Overlap.** Repeating the tail of one chunk at the start of the next so that sentences cut at a boundary remain whole in at least one chunk.

**Pretraining.** Training a model on a large text corpus to predict the next token, before any instruction tuning.

**Prompt caching.** An API feature that reuses the processed prefix of a prompt across calls at reduced cost. Applies naturally to stable system prompts and tool definitions.

**Prompt injection.** Text in data (a document, a tool result) that the model mistakes for instructions. A real risk when retrieved documents enter the prompt; mitigated by prompting, delimiting, tool scoping, review and human approval.

**RAG (Retrieval-Augmented Generation).** Retrieving relevant passages at query time and including them in the prompt so the model answers from them. The pattern behind `ops_copilot.rag` and the knowledge agent.

**RAGAS.** A framework and paper (Es et al., 2023) defining reference-light metrics for RAG systems, including faithfulness and answer relevance. The judge follows its decomposition.

**ReAct.** Reasoning and Acting: interleaving a short thought, a tool call and an observation in a loop (Yao et al., 2022). What each specialist implements via `bind_tools` and a tool node.

**Reducer.** A function attached to a state field that tells LangGraph how to merge a node's update into the existing value (for `messages`, append rather than replace).

**Reflexion.** Feeding verbal feedback about a failed attempt into the next attempt (Shinn et al., 2023). Why the reviewer's feedback is included in the specialist's retry.

**Resource (MCP).** Read-only data a server exposes by URI, chosen by the host rather than the model. Not used in v1.

**Reviewer.** `ops_copilot.agents.reviewer`: an LLM-as-judge node that checks the draft's faithfulness and citations against the evidence and returns `pass` or `revise` with feedback, at most two rounds.

**RLHF.** Reinforcement learning from human feedback; training a model towards responses people rate highly. One stage in producing the assistant models used here.

**Route / routing.** The supervisor's decision about which specialist handles a request: `knowledge`, `triage` or `release_notes`. Stored in `CopilotState.route`.

**RRF (Reciprocal Rank Fusion).** Merging several ranked lists by summing `1 / (k + rank)` for each item across lists, with `k` conventionally 60. Scale-free and needs no tuning; used in `HybridRetriever`.

**Specialist.** An agent with one job and a scoped tool set: `knowledge_agent`, `triage_agent`, `release_agent`.

**StateGraph.** LangGraph's graph builder: define a state type, add nodes and edges, compile with an optional checkpointer. Used in `build_graph`.

**stdio transport.** Running an MCP server as a subprocess and exchanging JSON-RPC over stdin/stdout. Used by both servers here.

**Streamable HTTP transport.** The MCP transport for remote servers over HTTP with optional streaming; requires authentication in practice.

**Structured output.** Asking the model to return data matching a schema (here, Pydantic models via `with_structured_output`) instead of free text. Used by the supervisor, triage agent and reviewer.

**Supervisor.** `ops_copilot.agents.supervisor`: the fast-model router at the entry of the graph. Also the name of the multi-agent pattern in which one router dispatches to specialists.

**System prompt.** Standing instructions for a conversation: role, rules, format. Each agent has its own.

**Temperature.** A sampling parameter that scales the next-token distribution; 0 is near-deterministic, higher values are more varied. Routing and judging run at 0.

**Thread.** A checkpointer key identifying one conversation or run; required to resume after an interrupt.

**Token.** The sub-word unit models read and produce; roughly four characters of English. Context limits and prices are in tokens.

**Tool / tool call.** A function the model may request by name with JSON arguments; the application runs it and returns a result. Nine MCP tools exist in this project.

**Tool scoping.** Giving each agent only the tools its task requires. Both a quality and a security measure.

**TriageReport.** The structured output of the triage agent: severity, suspected cause, matching runbook, recent changes, similar incidents, next steps, draft ticket.

**Vector store.** A database indexing embeddings for nearest-neighbour search with metadata filtering. `ChromaStore` wraps ChromaDB at `data/.index/chroma`.
