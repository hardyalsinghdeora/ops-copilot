"""Vector store on top of ChromaDB.

We pass our own embeddings (so the provider is swappable) and store chunk metadata
alongside the vectors so the retriever can filter by ``kind`` / ``service`` without a
second lookup.
"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

import chromadb

from .embeddings import EmbeddingProvider
from .models import Chunk, RetrievedChunk

COLLECTION = "ops_knowledge"


class ChromaStore:
    def __init__(self, path: Path | None, embeddings: EmbeddingProvider) -> None:
        self.embeddings = embeddings
        self.client = chromadb.PersistentClient(path=str(path)) if path else chromadb.EphemeralClient()
        self.collection = self.client.get_or_create_collection(
            COLLECTION,
            metadata={"hnsw:space": "cosine", "embedding_provider": embeddings.name},
        )

    # -- write -------------------------------------------------------------------
    def reset(self) -> None:
        self.client.delete_collection(COLLECTION)
        self.collection = self.client.get_or_create_collection(
            COLLECTION,
            metadata={"hnsw:space": "cosine", "embedding_provider": self.embeddings.name},
        )

    def add(self, chunks: Sequence[Chunk], batch_size: int = 128) -> None:
        for i in range(0, len(chunks), batch_size):
            batch = chunks[i : i + batch_size]
            self.collection.add(
                ids=[c.id for c in batch],
                documents=[c.text for c in batch],
                embeddings=self.embeddings.embed_documents([c.text for c in batch]),
                metadatas=[{**c.metadata, "doc_id": c.doc_id, "index": c.index} for c in batch],
            )

    # -- read --------------------------------------------------------------------
    @property
    def embedding_provider(self) -> str | None:
        """Provider the collection was built with (Chroma keeps the metadata it was created with)."""
        return (self.collection.metadata or {}).get("embedding_provider")

    def count(self) -> int:
        return self.collection.count()

    def query(self, text: str, k: int = 6, where: dict | None = None) -> list[RetrievedChunk]:
        if self.count() == 0:
            return []
        res = self.collection.query(
            query_embeddings=[self.embeddings.embed_query(text)],
            n_results=min(k, self.count()),
            where=where or None,
            include=["documents", "metadatas", "distances"],
        )
        out: list[RetrievedChunk] = []
        for cid, doc, meta, dist in zip(
            res["ids"][0], res["documents"][0], res["metadatas"][0], res["distances"][0], strict=False
        ):
            meta = dict(meta or {})
            chunk = Chunk(
                id=cid,
                doc_id=str(meta.pop("doc_id")),
                index=int(meta.pop("index")),
                heading=str(meta.get("heading", "")),
                text=doc,
                metadata={k: str(v) for k, v in meta.items()},
            )
            out.append(RetrievedChunk(chunk=chunk, score=1.0 - float(dist), sources=["vector"]))
        return out

    def get_by_doc(self, doc_id: str) -> list[Chunk]:
        res = self.collection.get(where={"doc_id": doc_id}, include=["documents", "metadatas"])
        chunks: list[Chunk] = []
        for cid, doc, meta in zip(res["ids"], res["documents"], res["metadatas"], strict=False):
            meta = dict(meta or {})
            chunks.append(
                Chunk(
                    id=cid,
                    doc_id=str(meta.pop("doc_id")),
                    index=int(meta.pop("index")),
                    heading=str(meta.get("heading", "")),
                    text=doc,
                    metadata={k: str(v) for k, v in meta.items()},
                )
            )
        return sorted(chunks, key=lambda c: c.index)
