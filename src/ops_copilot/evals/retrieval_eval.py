"""Retrieval metrics on the golden set.

* **hit@k** — fraction of questions where at least one expected document appears in
  the top-k retrieved *documents* (we dedupe chunks to documents first).
* **MRR**   — mean of 1/rank of the first expected document (0 if absent). Rewards
  putting the right document *first*, which is what matters for an LLM reading top-k.

``compare_retrievers`` runs the same golden set through BM25-only, vector-only and the
hybrid fusion so the value of hybrid search is a number, not a claim.

Golden items with an empty ``expected_doc_ids`` list are *negatives* (questions the
knowledge base cannot answer); they are skipped here and used by the judge stage to
check that the agent abstains instead of hallucinating.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import asdict, dataclass

from ..rag.models import RetrievedChunk
from ..rag.retriever import HybridRetriever


@dataclass
class QuestionResult:
    id: str
    question: str
    expected_doc_ids: list[str]
    top_doc_ids: list[str]
    hit: bool
    hit_at_1: bool
    reciprocal_rank: float


SearchFn = Callable[[str, int], list[RetrievedChunk]]


def _dedupe_docs(chunks: list[RetrievedChunk], k: int) -> list[str]:
    seen: list[str] = []
    for rc in chunks:
        if rc.chunk.doc_id not in seen:
            seen.append(rc.chunk.doc_id)
        if len(seen) >= k:
            break
    return seen


def top_documents(retriever: HybridRetriever, query: str, k: int) -> list[str]:
    return _dedupe_docs(retriever.search(query, k=k * 3), k)


def evaluate_search(search: SearchFn, golden: list[dict], k: int = 5) -> dict:
    results: list[QuestionResult] = []
    for item in golden:
        if not item.get("expected_doc_ids"):
            continue  # negative case — nothing to retrieve
        docs = _dedupe_docs(search(item["question"], k * 3), k)
        expected = set(item["expected_doc_ids"])
        rank = next((i + 1 for i, d in enumerate(docs) if d in expected), None)
        results.append(
            QuestionResult(
                id=item["id"],
                question=item["question"],
                expected_doc_ids=item["expected_doc_ids"],
                top_doc_ids=docs,
                hit=rank is not None,
                hit_at_1=rank == 1,
                reciprocal_rank=1.0 / rank if rank else 0.0,
            )
        )
    n = len(results) or 1
    return {
        "n": len(results),
        "k": k,
        "hit@1": sum(r.hit_at_1 for r in results) / n,
        f"hit@{k}": sum(r.hit for r in results) / n,
        "mrr": sum(r.reciprocal_rank for r in results) / n,
        "per_question": [asdict(r) for r in results],
    }


def evaluate_retrieval(retriever: HybridRetriever, golden: list[dict], k: int = 5) -> dict:
    return evaluate_search(lambda q, n: retriever.search(q, k=n), golden, k)


def compare_retrievers(retriever: HybridRetriever, golden: list[dict], k: int = 5) -> dict[str, dict]:
    """Same questions, three retrievers. Returns {name: metrics-without-per-question}."""
    variants: dict[str, SearchFn] = {
        "bm25": lambda q, n: retriever.bm25.search(q, n),
        "vector": lambda q, n: retriever.store.query(q, n),
        "hybrid": lambda q, n: retriever.search(q, k=n),
    }
    out = {}
    for name, fn in variants.items():
        m = evaluate_search(fn, golden, k)
        m.pop("per_question")
        out[name] = m
    return out
