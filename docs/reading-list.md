# Reading List

Curated resources behind the chapters in this folder. Grouped by topic; within each group, roughly in the order to read them. Entries marked **essential** are the ten I would read first if I had a week. Times are for a careful first read, not mastery.

Only well-known, verifiable sources are listed. Where a canonical documentation site exists it is named; papers are given by title and authors so you can find them on arxiv.org.

## Foundations

- **Attention Is All You Need** — Vaswani et al., 2017 (arxiv.org/abs/1706.03762). The transformer architecture. Read Sections 1 to 3 for the intuition; skip the rest unless curious. Supports Chapter 01. ~1 h.
- **The Illustrated Transformer** — Jay Alammar (blog). The friendliest visual walk-through of the paper above; read it first if the paper is heavy going. ~45 min.
- **Training language models to follow instructions with human feedback** — Ouyang et al., 2022 (arxiv.org/abs/2203.02155). The InstructGPT paper: the clearest account of instruction tuning and RLHF and why assistant models behave as they do. ~1.5 h.
- **Anthropic documentation: Models, Pricing, Context windows** — docs.claude.com. **Essential.** The authoritative reference for tiers, context sizes, token prices and limits; check it rather than memorising numbers. ~30 min.
- **Anthropic documentation: Prompt caching** — docs.claude.com. How the cache works, what counts as a cacheable prefix, and the pricing. Supports Chapters 01 and 07. ~20 min.

## Prompting and tool use

- **Anthropic documentation: Prompt engineering overview and sub-pages** — docs.claude.com. **Essential.** System prompts, examples, chain-of-thought, delimiting data, long-context tips. The practical basis for every prompt in this repo. ~1.5 h.
- **Anthropic documentation: Tool use** — docs.claude.com. **Essential.** The `tool_use` / `tool_result` loop, schema format, forcing tool choice, and how structured output rides on it. Read before writing any agent. ~45 min.
- **ReAct: Synergizing Reasoning and Acting in Language Models** — Yao et al., 2022 (arxiv.org/abs/2210.03629). **Essential.** The paper that named the reason/act/observe loop every specialist here implements. ~1 h.
- **Chain-of-Thought Prompting Elicits Reasoning in Large Language Models** — Wei et al., 2022 (arxiv.org/abs/2201.11903). Why asking for reasoning helps on multi-step tasks and when it does not. ~45 min.
- **Reflexion: Language Agents with Verbal Reinforcement Learning** — Shinn et al., 2023 (arxiv.org/abs/2303.11366). The basis for feeding reviewer feedback into a specialist's retry rather than blindly re-running. ~1 h.
- **Building effective agents** — Anthropic (anthropic.com / docs.claude.com). **Essential.** A short, opinionated piece on keeping agent systems as simple as the task allows and composing from a few patterns (routing, orchestrator-workers, evaluator-optimizer). This project is a direct instance of its advice. ~30 min.

## Retrieval-Augmented Generation

- **Retrieval-Augmented Generation for Knowledge-Intensive NLP Tasks** — Lewis et al., 2020 (arxiv.org/abs/2005.11401). **Essential.** The original RAG paper. The engineering has moved on; the framing has not. ~1 h.
- **Reciprocal Rank Fusion outperforms Condorcet and individual Rank Learning Methods** — Cormack, Clarke and Buettcher, SIGIR 2009. Four pages. The `1/(k+rank)` formula and why it works without tuning. ~20 min.
- **The Probabilistic Relevance Framework: BM25 and Beyond** — Robertson and Zaragoza, 2009. The definitive treatment of BM25. Read the first two sections for intuition. ~1 h.
- **Sentence-BERT: Sentence Embeddings using Siamese BERT-Networks** — Reimers and Gurevych, 2019 (arxiv.org/abs/1908.10084). Where sentence-embedding models like MiniLM come from and why cosine similarity on them means something. ~45 min.
- **Lost in the Middle: How Language Models Use Long Contexts** — Liu et al., 2023 (arxiv.org/abs/2307.03172). **Essential.** Why small k and ordering matter; the empirical argument against stuffing the context. ~45 min.
- **Precise Zero-Shot Dense Retrieval without Relevance Labels (HyDE)** — Gao et al., 2022 (arxiv.org/abs/2212.10496). Retrieve with the embedding of a hypothetical answer. A cheap upgrade to try after re-ranking. ~45 min.
- **An Introduction to Information Retrieval** — Manning, Raghavan and Schütze, 2008 (free online from the authors). Chapters 6 to 8 on scoring, vector space models and evaluation give the classical grounding behind hit@k, MRR and tf-idf. ~3 h for those chapters.
- **ChromaDB documentation** — persistent client, collections, `where` filtering. Only what you need for `ChromaStore`. ~30 min.

## Model Context Protocol

- **Model Context Protocol specification and documentation** — modelcontextprotocol.io. **Essential.** Architecture (host/client/server), tools, resources, prompts, transports (stdio and streamable HTTP), and the authorization section. Read the overview and the tools and transports pages first. ~1.5 h.
- **`mcp` Python SDK README and FastMCP examples** — linked from modelcontextprotocol.io. The decorator API used by both servers in this repo. ~30 min.
- **`langchain-mcp-adapters` README** — configuring `MultiServerMCPClient` for stdio and HTTP servers, and how MCP tools become LangChain tools. ~20 min.
- **Anthropic documentation: MCP in Claude Desktop and Claude Code** — docs.claude.com. The `claude_desktop_config.json` format and `claude mcp add`. ~20 min.
- **MCP Inspector** — modelcontextprotocol.io. Not reading; a tool. Spend thirty minutes pointing it at `ops-copilot mcp knowledge` and watching `tools/list` and `tools/call` on the wire. ~30 min.

## Agents and LangGraph

- **LangGraph documentation: Concepts** — langchain-ai.github.io/langgraph. **Essential.** State, reducers, nodes, edges, conditional edges, checkpointers, threads. Everything `build_graph` relies on. ~1.5 h.
- **LangGraph documentation: Human-in-the-loop** — langchain-ai.github.io/langgraph. `interrupt()`, `Command(resume=...)`, and the persistence requirements. Read before touching `human_approval`. ~45 min.
- **LangGraph documentation: Multi-agent tutorials** — langchain-ai.github.io/langgraph. Supervisor, swarm/handoff and hierarchical patterns with code. Compare with Chapter 05's table. ~1.5 h.
- **LangGraph documentation: Streaming (`astream_events`)** — langchain-ai.github.io/langgraph. Needed for the streaming stretch project. ~30 min.
- **Judging LLM-as-a-Judge with MT-Bench and Chatbot Arena** — Zheng et al., 2023 (arxiv.org/abs/2306.05685). What model judges get right and their known biases (verbosity, position, self-preference). Read before trusting the reviewer or the eval judge. ~1 h.

## Evaluation

- **RAGAS: Automated Evaluation of Retrieval Augmented Generation** — Es et al., 2023 (arxiv.org/abs/2309.15217). **Essential.** Defines faithfulness, answer relevance and context metrics without needing full reference answers. `evals.judge` follows its decomposition. ~45 min.
- **An Introduction to Information Retrieval, Chapter 8: Evaluation in information retrieval** — Manning, Raghavan and Schütze, 2008. Precision, recall, MRR, and why you need more than one metric. ~1 h.
- **Anthropic documentation: Developing test cases and using Claude as a grader** — docs.claude.com. Practical guidance on building eval sets and rubric-based grading. ~30 min.
- **LangSmith documentation: Evaluation and datasets** — linked from the LangGraph docs. If you adopt LangSmith for tracing, this is how production traces become eval cases. ~45 min.

## Production

- **OWASP Top 10 for Large Language Model Applications** — OWASP Foundation. A structured list of risks (prompt injection, insecure output handling, excessive agency, and more) with mitigations. Map each to a layer in this project. ~1 h.
- **Anthropic documentation: Rate limits, Errors, Streaming** — docs.claude.com. What 429 and 529 mean, retry guidance, and how to stream. ~30 min.
- **LangGraph documentation: Persistence and deployment** — langchain-ai.github.io/langgraph. Checkpointer backends (SQLite, Postgres) and deployment options. Needed before `serve` is more than a demo. ~45 min.
- **Model Context Protocol: Authorization** — modelcontextprotocol.io. The OAuth-based flow for remote servers over streamable HTTP. Read when moving a server off stdio. ~45 min.
- **OpenTelemetry documentation: Python instrumentation** — opentelemetry.io. If you choose OTel over LangSmith for traces. ~45 min.

## Suggested order for a week

If you have about seven hours over a week and want the highest return: Anthropic prompt engineering and tool use pages; *Building effective agents*; ReAct; the RAG paper; *Lost in the Middle*; the MCP specification overview; LangGraph concepts and human-in-the-loop; RAGAS. Then run `ops-copilot eval` and read Chapter 06 with the numbers in front of you.
