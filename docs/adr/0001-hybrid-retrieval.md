# ADR 0001 — Hybrid retrieval (BM25 + vectors) fused with Reciprocal Rank Fusion

## Status

Accepted.

## Context

The knowledge base consists of runbooks, ADRs, postmortems and service pages. Two kinds of query dominate on-call use:

- **Exact-identifier queries**: `ERR_CONN_RESET`, `payments-api`, `PG-4412`, a flag name. The user knows the token and wants the document that contains it.
- **Concept queries**: "why does checkout get slow after a deploy", where the document says "p99 latency regression following rollout" and shares almost no words with the question.

Dense vector retrieval handles concept queries but treats rare identifiers as low-information tokens; two different error codes can embed almost identically. Lexical retrieval (BM25) handles identifiers exactly but cannot connect paraphrases. A single method leaves a class of common on-call questions unanswerable, and the failure is silent: the model answers from whatever weaker chunks came back.

## Decision

`ops_copilot.rag.retriever.HybridRetriever` runs both a BM25 index (`rank-bm25`, persisted to `data/.bm25.pkl`) and a vector search (`ChromaStore` at `data/.chroma`) over the same chunks, over-fetches from each, and merges the two ranked lists with **Reciprocal Rank Fusion**:

```
score(chunk) = Σ_r 1 / (k + rank_r(chunk)),   k = 60
```

Metadata filters (`kind`, `service`) apply to both sides. The top-k fused results are returned as `RetrievedChunk(score, chunk)`.

RRF was chosen over weighted score combination because BM25 scores and cosine similarities are on incomparable scales, and because RRF has a single, insensitive parameter. The spec's summary is "needs no tuning".

## Consequences

- Both identifier and concept queries are served; agreement between rankers is rewarded.
- Two indexes are built and kept in sync by `ops_copilot.rag.ingest`; changing the embedding provider requires a full re-ingest.
- Retrieval latency is two lookups plus a cheap merge; fine for this corpus size.
- `rank-bm25` holds the index in memory; at much larger scale this moves to a search engine with native hybrid support behind the same `HybridRetriever` interface.
- Quality is measurable: `ops_copilot.evals.retrieval_eval` reports hit@k and MRR and can compare BM25-only, vector-only and hybrid.

## Alternatives considered

- **Vector-only.** Simplest; fails identifier queries. Rejected.
- **BM25-only.** Exact; fails paraphrase queries. Rejected.
- **Weighted combination of normalised scores.** Needs per-corpus tuning; brittle when one ranker returns few results. Rejected for RRF.
- **Cross-encoder re-ranking instead of fusion.** Improves ordering but still needs a good candidate set, which is fusion's job. Kept as a future upgrade on top of RRF.
