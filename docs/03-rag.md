# 03 — Retrieval-Augmented Generation

*Who this is for: engineers who want to make a model answer from private documents, correctly, with citations. Time to complete: about 4 hours.*

RAG is the answer to "the model has never seen our runbooks". Instead of training the model on your documents, you find the few passages relevant to the question at request time, put them in the prompt, and ask the model to answer from them. The idea was formalised in *Retrieval-Augmented Generation for Knowledge-Intensive NLP Tasks* (Lewis et al., 2020); the engineering around it is where most of the work is.

This chapter walks the pipeline exactly as `ops_copilot.rag` implements it:

```
data/knowledge_base/**/*.md
   │  loader        → Document(id, title, kind, service, text, metadata)
   │  chunker       → Chunk(id, doc_id, heading, text, metadata)
   │  embeddings    → vectors
   ├─ store         → ChromaStore (data/.index/chroma)
   └─ BM25 index    → data/.index/bm25.pkl
                      │
   query ──▶ HybridRetriever: BM25 ranks + vector ranks → RRF → filter → RetrievedChunk(score, chunk)
```

## Step 1: Loading documents (`ops_copilot.rag.loader`)

The knowledge base is markdown with YAML front-matter:

```markdown
---
id: runbook-payments-api-latency
title: payments-api — high latency runbook
kind: runbook
service: payments-api
owner: payments-team
updated: 2026-09-14
---

## Symptoms
p99 latency above 2s on /charge ...
```

The loader parses the front-matter into a `Document(id, title, kind, service, text, metadata)`. Three fields matter downstream:

- `id` becomes the `doc_id` in citations (`[runbook-payments-api-latency#3]`). It must be stable across re-ingests, which is why it is declared in the file rather than derived from the path.
- `kind` (`runbook`, `adr`, `postmortem`, `service`) and `service` are **metadata filters**. "Show me the runbook for payments-api" should not have to compete with postmortems.
- `updated` lets you prefer or flag stale material later.

Why front-matter and not a database: the knowledge base is text in git. Engineers edit it in pull requests. The ingestion pipeline should not require them to learn anything new.

## Step 2: Chunking (`ops_copilot.rag.chunker`)

You cannot embed a whole runbook as one vector; the vector would be an average of everything in it and match nothing well. You cannot embed each sentence either; a sentence has too little context to be understood on its own. **Chunking** is the compromise.

`ops_copilot.rag.chunker` is **heading-aware with overlap**:

1. Split on markdown headings first, so a chunk never straddles "## Symptoms" and "## Rollback procedure".
2. Within a section, split further to a target size measured in approximate tokens (the chunker uses a cheap token estimate, not a full tokeniser).
3. Carry a fixed **overlap** of the previous chunk's tail into the next chunk, so a sentence cut at a boundary still appears whole somewhere.
4. Prefix each chunk's text with its document title and heading path, so the embedding knows what the chunk is about even when the body does not say.

```python
# ops_copilot/rag/chunker.py (illustrative)
@dataclass
class Chunk:
    id: str          # f"{doc_id}#{index}"
    doc_id: str
    heading: str     # "payments-api — high latency runbook > Rollback procedure"
    text: str
    metadata: dict   # kind, service, owner, updated, copied from the Document

def chunk_document(doc: Document, size_tokens: int = 400, overlap_tokens: int = 60) -> list[Chunk]:
    ...
```

**Why chunk size matters** (and why Chapter 06 tells you to A/B it):

| Chunks too small | Chunks too large |
|---|---|
| Each lacks context; "restart the pod" with no service name | One vector averages several topics and matches none of them well |
| Many near-duplicate hits for one section | The prompt fills with irrelevant text; cost up, attention down |
| Fine-grained citations | Coarse citations |

There is no universal right answer; 300 to 600 estimated tokens with 10 to 20 percent overlap is a sane default for runbook-style text, and the only honest way to tune it is to measure hit@k on your own golden set.

## Step 3: Embeddings (`ops_copilot.rag.embeddings`)

An **embedding** is a fixed-length vector (a few hundred to a few thousand floats) produced by a model trained so that texts with similar meaning land near each other. "DB connection pool exhausted" and "ran out of database connections" share almost no words but have close embeddings.

Similarity is measured by **cosine similarity**: the cosine of the angle between two vectors, 1.0 for identical direction, 0 for orthogonal. Vector stores index embeddings so that "find the k nearest to this query vector" is fast.

The project defines a small protocol and three implementations:

```python
# ops_copilot/rag/embeddings.py (illustrative)
class EmbeddingProvider(Protocol):
    def embed_documents(self, texts: list[str]) -> list[list[float]]: ...
    def embed_query(self, text: str) -> list[float]: ...

class LocalMiniLM:        # default: small sentence-transformer exported to ONNX, runs on CPU, no API key
class VoyageEmbeddings:   # hosted, higher quality; needs VOYAGE_API_KEY and the `voyage` extra
class HashingEmbeddings:  # deterministic feature hashing of tokens; no model, no download; for tests/CI
```

| Provider | When | Trade-off |
|---|---|---|
| `LocalMiniLM` (ONNX) | Default. Laptop, demo, small corpus | Free, offline, decent. Weaker on long or very technical passages. |
| `VoyageEmbeddings` | When quality matters and you have a key | Better recall, especially on code-heavy text. Network, cost, data leaves the machine. |
| `HashingEmbeddings` | Tests and CI | Deterministic and instant. Not semantic at all; it is a bag-of-words projection. It exists so the pipeline can be tested without a model. |

Two rules that follow from how embeddings work:

- **Query and documents must use the same provider.** Vectors from different models are not comparable. Changing `OPS_COPILOT_EMBEDDING_PROVIDER` means re-running `ops-copilot ingest`.
- **Embed the chunk with its heading prefix.** The context the chunker added is exactly what the embedding model needs.

See [`adr/0005-pluggable-embeddings.md`](adr/0005-pluggable-embeddings.md) for the decision record.

## Step 4: The vector store (`ops_copilot.rag.store`)

`ChromaStore` wraps a persistent ChromaDB collection at `data/.index/chroma`. It stores for each chunk the vector, the text, and the metadata dict, and answers `query(vector, k, where=...)` with the nearest chunks. The `where` clause is how metadata filters reach the vector side.

Chroma is used because it is embedded (no server to run), persistent, and supports metadata filtering. For a portfolio project that is the right trade. Chapter 07 discusses what you would move to at scale.

## Step 5: Lexical search with BM25

Vectors are bad at one thing runbooks are full of: **exact identifiers**. `ERR_CONN_RESET`, `payments-api`, `PG-4412`, a specific flag name. An embedding model sees these as rare tokens with little semantic weight; two different error codes may embed almost identically.

**BM25** is the classic lexical ranking function behind most search engines. It scores a document for a query by term frequency (how often the query words appear) adjusted by inverse document frequency (rare words count more) and document length. It does not understand meaning at all, which is exactly why it is perfect for identifiers.

The project uses `rank-bm25` over the same chunk list, tokenised simply, and pickles the index to `data/.index/bm25.pkl` at ingest time.

## Step 6: Why hybrid, and Reciprocal Rank Fusion (`ops_copilot.rag.retriever`)

| Query | BM25 | Vector |
|---|---|---|
| `ERR_CONN_RESET on payments-api` | Excellent: exact term match | Poor: error code carries little meaning |
| "why does checkout get slow after deploys" | Poor: no overlap with "latency", "p99", "rollout" | Excellent: semantic match |
| "rotate the payments DB credentials" | Good: "rotate", "credentials" | Good |

Neither alone is good enough. **Hybrid retrieval** runs both and merges. The merge method in `HybridRetriever` is **Reciprocal Rank Fusion (RRF)**:

```
RRF(chunk) = Σ over each ranker r:   1 / (k + rank_r(chunk))
```

where `rank_r` is the chunk's 1-based position in ranker `r`'s list (absent chunks contribute nothing) and `k` is a constant, conventionally 60. A chunk ranked 1st by BM25 and 3rd by vectors scores `1/61 + 1/63 ≈ 0.0323`. A chunk ranked 1st by vectors only scores `1/61 ≈ 0.0164`.

Why RRF rather than weighting raw scores:

- BM25 scores and cosine similarities are on **different, incomparable scales**. RRF only uses ranks.
- Agreement is rewarded: a chunk both rankers like beats a chunk one ranker loves.
- It has **one parameter** (`k`) and is insensitive to it. The spec's phrase is "needs no tuning".

```python
# ops_copilot/rag/retriever.py (illustrative)
@dataclass
class RetrievedChunk:
    score: float
    chunk: Chunk

class HybridRetriever:
    def __init__(self, store: ChromaStore, bm25, chunks: list[Chunk], embedder: EmbeddingProvider, rrf_k: int = 60): ...

    def retrieve(self, query: str, k: int = 5, filter: dict | None = None) -> list[RetrievedChunk]:
        lexical = self._bm25_rank(query, n=k * 4)              # list[chunk_id] in rank order
        semantic = self._vector_rank(query, n=k * 4, where=filter)
        scores: dict[str, float] = {}
        for ranked in (lexical, semantic):
            for rank, chunk_id in enumerate(ranked, start=1):
                scores[chunk_id] = scores.get(chunk_id, 0.0) + 1.0 / (self.rrf_k + rank)
        fused = sorted(scores.items(), key=lambda kv: kv[1], reverse=True)
        fused = [(cid, s) for cid, s in fused if self._passes_filter(cid, filter)]
        return [RetrievedChunk(score=s, chunk=self._by_id[cid]) for cid, s in fused[:k]]
```

Note the over-fetch (`k * 4` from each ranker) before fusing; fusing only the top-k of each list throws away exactly the agreement signal RRF is looking for.

See [`adr/0001-hybrid-retrieval.md`](adr/0001-hybrid-retrieval.md).

## Step 7: Metadata filtering

`search_knowledge(query, kind?, service?, k)` on the MCP server passes `kind` and `service` through to `HybridRetriever.search(filter=...)`. On the vector side this is Chroma's `where`; on the BM25 side it is a post-filter over chunk metadata.

Filters are powerful and dangerous. "runbooks for payments-api" with `kind=runbook, service=payments-api` gives precise results. But a filter the model guesses wrongly (`service="payments"` when the catalogue says `payments-api`) returns nothing. The knowledge agent's prompt tells it to retry without the filter when a filtered search returns no results.

## Step 8: Citations and grounded answering

Every `Chunk.id` is `doc_id#index`. The `search_knowledge` tool returns chunks with their ids, and the knowledge agent is instructed to cite them inline as `[doc_id#chunk]`:

> Rotate the credentials via the `payments-db-rotate` job, then restart `payments-api` so the new secret is picked up [runbook-payments-db-credentials#2]. The rotation job requires the on-call to hold the `db-admin` role [runbook-payments-db-credentials#1].

Citations do three jobs:

1. **For the user:** a link back to the source, which is what an on-call engineer actually wants at 2am.
2. **For the reviewer:** `ops_copilot.agents.reviewer` checks that every cited id exists in `state["evidence"]` and that the claim it is attached to is supported by that chunk's text.
3. **For evals:** `retrieval_eval` checks whether the expected `doc_id` appeared in the retrieved set.

The prompt contract for grounded answering is in Chapter 02. The short version: only from evidence, cite everything, say "not found" rather than guess.

## "Lost in the Middle"

*Lost in the Middle: How Language Models Use Long Contexts* (Liu et al., 2023) showed that models recall information placed at the start or end of a long context much better than information in the middle, and that performance degrades as more documents are added even when the relevant one is present.

Implications for this pipeline:

- **Keep k small.** Five good chunks beat twenty mixed ones.
- **Order matters.** Put the highest-scoring chunk first or last, not buried at position 7 of 15.
- **Re-ranking** (below) is partly about ordering, not just filtering.

## Re-ranking: the obvious next upgrade

RRF gives you a good top-20. A **cross-encoder re-ranker** reads the query and each candidate chunk together and produces a much more accurate relevance score than either BM25 or a bi-encoder embedding can, at the cost of one model forward pass per candidate. The standard pattern is: retrieve 20–50 with hybrid search, re-rank, keep the top 5.

It is not in the spec. It is the first stretch project in [`exercises.md`](exercises.md), and the eval harness is how you would prove it helped.

A related technique is **HyDE** (*Precise Zero-Shot Dense Retrieval without Relevance Labels*, Gao et al., 2022): ask the model to write a hypothetical answer, embed that instead of the question, and search with it. It helps when questions and documents are phrased very differently.

## Evaluation preview

You cannot tune any of the above by feel. Chapter 06 covers the harness; here is what it measures for retrieval, using `src/ops_copilot/evals/golden.json` (questions paired with the `doc_id` that should answer them):

- **hit@k:** fraction of questions where the expected document appears in the top k results.
- **MRR (mean reciprocal rank):** average over questions of `1 / rank` of the first correct result, 0 if absent. Rewards putting the right document first, not just somewhere in the top k.

Run `ops-copilot eval` after any change to chunk size, overlap, embedding provider or RRF `k`. If hit@5 drops, the change was bad regardless of how the answers "look".

## Common pitfalls

- **Changing the embedding provider without re-ingesting.** Query vectors from one model against document vectors from another are noise.
- **Chunking without heading context.** A chunk that says "set it to 50 and restart" is unretrievable.
- **Fusing only the top-k of each ranker.** You lose the overlap signal. Over-fetch, then fuse, then cut.
- **Weighting BM25 and cosine scores directly.** Different scales; use ranks.
- **Overly specific filters.** Fall back to unfiltered search when a filter returns nothing.
- **Trusting the vector store's "distance" as a confidence.** It is not calibrated. Use it for ranking only.
- **Citing document titles instead of chunk ids.** The reviewer cannot verify a title.

## Exercises

1. **See the two rankers disagree.** Add a debug flag (or a small script) that prints the BM25 top-5 and the vector top-5 separately before fusion. Run it on `ERR_CONN_RESET` and on "what causes latency spikes". Write down which ranker found the right chunk in each case.
2. **Chunk-size A/B.** Run `ops-copilot ingest` and `ops-copilot eval` at chunk sizes 200, 400 and 800 (overlap 15 percent). Record hit@5 and MRR for each. Which won? Look at one question that changed from miss to hit and explain why.
3. **Provider swap.** Set `OPS_COPILOT_EMBEDDING_PROVIDER=hashing`, re-ingest, run the retrieval evals. Then switch back to `local`. The gap is the value semantic embeddings add on this corpus. If you have a Voyage key, add it as a third data point.
4. **Break a citation.** Hand-edit a knowledge agent answer (or mock the draft) so it cites `[nonexistent-doc#1]` and run it through the reviewer. Confirm the reviewer returns `revise` and that its feedback names the bad citation.

## Further reading

- *Retrieval-Augmented Generation for Knowledge-Intensive NLP Tasks*, Lewis et al., 2020 (arxiv.org/abs/2005.11401). The original RAG paper.
- *Lost in the Middle: How Language Models Use Long Contexts*, Liu et al., 2023 (arxiv.org/abs/2307.03172).
- *Precise Zero-Shot Dense Retrieval without Relevance Labels* (HyDE), Gao et al., 2022 (arxiv.org/abs/2212.10496).
- *Reciprocal Rank Fusion outperforms Condorcet and individual Rank Learning Methods*, Cormack, Clarke and Buettcher, SIGIR 2009. The RRF paper; short and readable.
- *The Probabilistic Relevance Framework: BM25 and Beyond*, Robertson and Zaragoza, 2009. The definitive BM25 reference.
- *Sentence-BERT: Sentence Embeddings using Siamese BERT-Networks*, Reimers and Gurevych, 2019 (arxiv.org/abs/1908.10084). The lineage of the MiniLM sentence embedding models.
- ChromaDB documentation (metadata filtering, persistent client).
- Anthropic documentation, prompt engineering guidance on long-context prompting and citing sources (docs.claude.com).
