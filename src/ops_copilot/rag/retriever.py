"""Hybrid retrieval: BM25 (lexical) + vector (semantic), fused with Reciprocal Rank Fusion.

Why hybrid?  Ops documents are full of exact identifiers — ``payments-api``,
``ERR_CONN_RESET``, ``NAV-4821`` — where lexical matching is unbeatable, *and* full
of paraphrase — "payments are slow" vs "p99 latency spike" — where embeddings shine.
RRF combines the two ranked lists without needing to calibrate their scores::

    rrf(d) = Σ_r  w_r / (k + rank_r(d))        (k = 60 by convention)

A document ranked highly by either retriever ends up near the top; one ranked
moderately by both beats one ranked highly by only one. No tuning required.
"""

from __future__ import annotations

import pickle
import re
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path

from rank_bm25 import BM25Okapi

from .models import Chunk, RetrievedChunk
from .store import ChromaStore

_TOKEN = re.compile(r"[a-z0-9]+(?:[_\-\.][a-z0-9]+)*")
RRF_K = 60


def tokenize(text: str) -> list[str]:
    """Lowercase, keep identifiers like ``payments-api`` and ``v2.14.0`` whole,
    and also emit their sub-parts so ``payments`` matches ``payments-api``."""
    toks: list[str] = []
    for t in _TOKEN.findall(text.lower()):
        toks.append(t)
        if any(sep in t for sep in "-_."):
            toks.extend(p for p in re.split(r"[-_.]", t) if p)
    return toks


@dataclass
class BM25Index:
    chunks: list[Chunk]
    _bm25: BM25Okapi = field(init=False, repr=False)

    def __post_init__(self) -> None:
        self._bm25 = BM25Okapi([tokenize(c.text) for c in self.chunks] or [["empty"]])

    def search(self, query: str, k: int, where: dict | None = None) -> list[RetrievedChunk]:
        if not self.chunks:
            return []
        scores = self._bm25.get_scores(tokenize(query))
        ranked = sorted(range(len(self.chunks)), key=lambda i: scores[i], reverse=True)
        out: list[RetrievedChunk] = []
        for i in ranked:
            c = self.chunks[i]
            if where and any(c.metadata.get(key) != val for key, val in where.items()):
                continue
            if scores[i] <= 0:
                break
            out.append(RetrievedChunk(chunk=c, score=float(scores[i]), sources=["bm25"]))
            if len(out) >= k:
                break
        return out

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(pickle.dumps([c.model_dump() for c in self.chunks]))

    @classmethod
    def load(cls, path: Path) -> BM25Index:
        return cls([Chunk(**d) for d in pickle.loads(path.read_bytes())])


def reciprocal_rank_fusion(
    ranked_lists: Sequence[Sequence[RetrievedChunk]], weights: Sequence[float] | None = None, k: int = RRF_K
) -> list[RetrievedChunk]:
    weights = weights or [1.0] * len(ranked_lists)
    fused: dict[str, RetrievedChunk] = {}
    for lst, w in zip(ranked_lists, weights, strict=False):
        for rank, rc in enumerate(lst, start=1):
            entry = fused.get(rc.chunk.id)
            contrib = w / (k + rank)
            if entry is None:
                fused[rc.chunk.id] = RetrievedChunk(chunk=rc.chunk, score=contrib, sources=list(rc.sources))
            else:
                entry.score += contrib
                entry.sources = sorted(set(entry.sources) | set(rc.sources))
    return sorted(fused.values(), key=lambda r: r.score, reverse=True)


class HybridRetriever:
    def __init__(
        self,
        store: ChromaStore,
        bm25: BM25Index,
        bm25_weight: float = 1.0,
        vector_weight: float = 1.0,
    ) -> None:
        self.store = store
        self.bm25 = bm25
        self.bm25_weight = bm25_weight
        self.vector_weight = vector_weight

    @staticmethod
    def _where(kind: str | None, service: str | None) -> dict | None:
        where = {}
        if kind:
            where["kind"] = kind
        if service:
            where["service"] = service
        return where or None

    @staticmethod
    def _chroma_where(where: dict | None) -> dict | None:
        """Chroma needs ``$and`` for multiple equality filters."""
        if not where:
            return None
        if len(where) == 1:
            return where
        return {"$and": [{k: v} for k, v in where.items()]}

    def search(
        self, query: str, k: int = 6, kind: str | None = None, service: str | None = None
    ) -> list[RetrievedChunk]:
        where = self._where(kind, service)
        candidates = max(k * 3, 10)  # over-fetch, fusion trims
        lexical = self.bm25.search(query, candidates, where)
        semantic = self.store.query(query, candidates, self._chroma_where(where))
        return reciprocal_rank_fusion([lexical, semantic], [self.bm25_weight, self.vector_weight])[:k]

    # -- document-level helpers used by the MCP knowledge server -----------------
    def get_document(self, doc_id: str) -> str | None:
        chunks = [c for c in self.bm25.chunks if c.doc_id == doc_id]
        if not chunks:
            return None
        chunks.sort(key=lambda c: c.index)
        title = chunks[0].metadata.get("title", doc_id)
        body = "\n\n".join(f"{c.citation}\n{c.text}" for c in chunks)
        return f"# {title} ({doc_id})\n\n{body}"

    def list_documents(self, kind: str | None = None, service: str | None = None) -> list[dict[str, str]]:
        seen: dict[str, dict[str, str]] = {}
        for c in self.bm25.chunks:
            if kind and c.metadata.get("kind") != kind:
                continue
            if service and c.metadata.get("service") != service:
                continue
            seen.setdefault(
                c.doc_id,
                {
                    "id": c.doc_id,
                    "title": c.metadata.get("title", ""),
                    "kind": c.metadata.get("kind", ""),
                    "service": c.metadata.get("service", ""),
                    "owner": c.metadata.get("owner", ""),
                    "updated": c.metadata.get("updated", ""),
                },
            )
        return sorted(seen.values(), key=lambda d: (d["kind"], d["id"]))
