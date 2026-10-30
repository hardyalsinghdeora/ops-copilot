"""Ingestion pipeline: markdown → documents → chunks → (Chroma, BM25) on disk.

Run with ``ops-copilot ingest``. Re-running rebuilds the index from scratch, which is
the right behaviour for a knowledge base this size; incremental updates are a
production concern discussed in docs/07-production.md.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from ..config import Settings, get_settings
from .chunker import chunk_documents
from .embeddings import EmbeddingProvider, get_embeddings
from .loader import load_knowledge_base
from .retriever import BM25Index, HybridRetriever
from .store import ChromaStore


@dataclass
class IngestResult:
    documents: int
    chunks: int
    embedding_provider: str
    index_dir: Path


def ingest(
    kb_dir: Path | None = None,
    settings: Settings | None = None,
    embeddings: EmbeddingProvider | None = None,
) -> IngestResult:
    s = settings or get_settings()
    kb_dir = kb_dir or s.knowledge_base_dir
    embeddings = embeddings or get_embeddings()

    docs = load_knowledge_base(kb_dir)
    chunks = chunk_documents(docs, s.chunk_size, s.chunk_overlap)

    s.index_dir.mkdir(parents=True, exist_ok=True)
    store = ChromaStore(s.chroma_dir, embeddings)
    store.reset()
    store.add(chunks)
    BM25Index(chunks).save(s.bm25_path)

    return IngestResult(len(docs), len(chunks), embeddings.name, s.index_dir)


def load_retriever(settings: Settings | None = None, embeddings: EmbeddingProvider | None = None) -> HybridRetriever:
    """Open an existing index. Raises a helpful error if ``ingest`` has not run."""
    s = settings or get_settings()
    if not s.bm25_path.exists():
        raise FileNotFoundError(
            f"No index at {s.index_dir}. Run `ops-copilot ingest` first."
        )
    embeddings = embeddings or get_embeddings()
    store = ChromaStore(s.chroma_dir, embeddings)
    return HybridRetriever(store, BM25Index.load(s.bm25_path), s.bm25_weight, s.vector_weight)


def build_in_memory_retriever(kb_dir: Path, embeddings: EmbeddingProvider, chunk_size: int = 350, overlap: int = 50) -> HybridRetriever:
    """Ephemeral retriever (no disk) — used by tests and the eval harness."""
    docs = load_knowledge_base(kb_dir)
    chunks = chunk_documents(docs, chunk_size, overlap)
    store = ChromaStore(None, embeddings)
    store.add(chunks)
    return HybridRetriever(store, BM25Index(chunks))
