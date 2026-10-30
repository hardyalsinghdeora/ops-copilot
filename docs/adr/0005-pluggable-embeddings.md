# ADR 0005 — Pluggable embedding providers with a local default

## Status

Accepted.

## Context

The vector half of hybrid retrieval needs an embedding model, and the candidates pull in different directions:

- A **hosted API** (Voyage or similar) gives the best quality on technical text but needs a key, costs per token, requires network, and sends the knowledge base to a third party. For a project that must run on a reviewer's laptop in ten minutes, and for teams with confidential runbooks, each is a barrier.
- A **local model** through a full deep-learning framework runs offline but adds a multi-gigabyte dependency and slow cold starts.
- **Tests and CI** need something deterministic and instant: no download, no key, identical vectors every run.

No single provider fits all three, and the best small model will change over the project's life.

## Decision

`ops_copilot.rag.embeddings` defines a small `EmbeddingProvider` protocol (`embed_documents`, `embed_query`) and three implementations, selected by `Settings.embedding_provider` (from `.env`):

| Provider | Default for | Notes |
|---|---|---|
| `LocalMiniLM` | Development and demos (**default**) | A small sentence-transformer exported to ONNX and run with the ONNX runtime on CPU. No key, no cost, offline after first download, small dependency footprint. |
| `VoyageEmbeddings` | Quality when a key is available | Hosted; installed via the `voyage` extra. Better recall on code-heavy and long passages. |
| `HashingEmbeddings` | Tests and CI | Deterministic feature hashing of tokens into a fixed-size vector. Not semantic; exists so the full pipeline runs with no model at all. |

The rest of the pipeline (`ChromaStore`, `HybridRetriever`, `ingest`) depends only on the protocol.

## Consequences

- `pip install -e . && ops-copilot ingest` works with no credentials and no network beyond the first ONNX download.
- CI runs retrieval evals with `EMBEDDING_PROVIDER=hashing` in seconds; those numbers measure pipeline correctness and lexical retrieval, not semantic quality, and must not be quoted as such.
- Switching providers invalidates the vector index; `ingest` must be re-run, and query and document embeddings must come from the same provider.
- Comparing providers is a config change plus `ops-copilot eval`: a measured decision rather than a guess.
- The local default is good, not best; teams with strict recall needs will move to a hosted provider after checking their data agreement.

## Alternatives considered

- **Hosted embeddings only.** Blocks offline use, costs on every ingest, leaks documents. Rejected as default.
- **Local model via a full framework.** Heavy install for marginal gain over ONNX. Rejected.
- **Hashing only.** Not semantic; defeats the vector half of hybrid retrieval. Kept for CI only.
- **Chroma's built-in embedding function.** Hides the provider choice inside the store. Rejected in favour of an explicit protocol.
