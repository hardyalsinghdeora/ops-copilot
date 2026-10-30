from __future__ import annotations

from ops_copilot.rag import (
    Chunk,
    Document,
    HashingEmbeddings,
    RetrievedChunk,
    chunk_document,
    load_knowledge_base,
    reciprocal_rank_fusion,
)
from ops_copilot.rag.loader import parse_front_matter
from ops_copilot.rag.retriever import tokenize

from .conftest import KB_DIR


def test_front_matter_parses_scalars_and_lists():
    meta, body = parse_front_matter("---\nid: x\ntitle: Hello\ntags: [a, b]\n---\nbody text\n")
    assert meta["id"] == "x" and meta["title"] == "Hello" and meta["tags"] == ["a", "b"]
    assert body.strip() == "body text"


def test_knowledge_base_loads_with_unique_ids_and_required_fields():
    docs = load_knowledge_base(KB_DIR)
    assert len(docs) >= 20
    assert len({d.id for d in docs}) == len(docs)
    kinds = {d.kind for d in docs}
    assert kinds == {"runbook", "adr", "postmortem", "service"}
    for d in docs:
        assert d.id.split("-")[0] in {"rb", "adr", "pm", "svc"}, d.id
        assert d.owner and d.updated and d.text


def test_chunker_is_heading_aware_and_prefixes_context():
    doc = Document(
        id="rb-x", title="X runbook", kind="runbook", service="svc",
        text="Intro.\n\n## Symptoms\nSome symptom text.\n\n## Mitigation\nDo the thing.",
    )
    chunks = chunk_document(doc, chunk_size=100, overlap=10)
    assert [c.heading for c in chunks] == ["X runbook", "Symptoms", "Mitigation"]
    assert chunks[1].text.startswith("X runbook > Symptoms")
    assert chunks[1].id == "rb-x#1" and chunks[1].metadata["kind"] == "runbook"


def test_chunker_splits_oversized_sections():
    long = "## Big\n" + " ".join(f"sentence{i} word word word." for i in range(300))
    doc = Document(id="d", title="T", kind="runbook", service="s", text=long)
    chunks = chunk_document(doc, chunk_size=120, overlap=20)
    assert len(chunks) > 3
    assert all(len(c.text.split()) < 200 for c in chunks)


def test_hashing_embeddings_are_deterministic_and_normalised():
    e = HashingEmbeddings()
    a, b = e.embed_query("payments api latency"), e.embed_query("payments api latency")
    assert a == b
    assert abs(sum(x * x for x in a) - 1.0) < 1e-6


def test_tokenizer_keeps_identifiers_and_emits_parts():
    toks = tokenize("Alert PaymentsP99High on payments-api v2.14.0")
    assert "payments-api" in toks and "payments" in toks and "api" in toks and "v2.14.0" in toks


def test_rrf_prefers_items_found_by_both_retrievers():
    def rc(cid, src):
        return RetrievedChunk(chunk=Chunk(id=cid, doc_id=cid, index=0, heading="", text=""), score=1, sources=[src])

    fused = reciprocal_rank_fusion([[rc("a", "bm25"), rc("b", "bm25")], [rc("b", "vector"), rc("c", "vector")]])
    assert fused[0].chunk.id == "b" and fused[0].sources == ["bm25", "vector"]


def test_hybrid_search_finds_runbook_by_symptom_and_filters(retriever):
    results = retriever.search("payments p99 latency spike", k=5, kind="runbook")
    assert results, "no results"
    assert all(r.chunk.metadata["kind"] == "runbook" for r in results)
    assert any(r.chunk.doc_id == "rb-payments-p99-latency-spike" for r in results)

    svc = retriever.search("what does this service do", k=5, kind="service", service="auth-service")
    assert svc and all(r.chunk.metadata["service"] == "auth-service" for r in svc)


def test_document_helpers(retriever):
    docs = retriever.list_documents(kind="adr")
    assert docs and all(d["kind"] == "adr" for d in docs)
    full = retriever.get_document(docs[0]["id"])
    assert full and full.startswith("# ") and f"[{docs[0]['id']}#0]" in full
    assert retriever.get_document("nope") is None
