from __future__ import annotations

from ops_copilot.evals.retrieval_eval import evaluate_retrieval
from ops_copilot.evals.run import load_golden


def test_golden_set_is_well_formed(retriever):
    golden = load_golden()
    assert len(golden) >= 10
    known = {d["id"] for d in retriever.list_documents()}
    for item in golden:
        assert {"id", "question", "expected_doc_ids", "reference_answer", "category"} <= set(item)
        assert set(item["expected_doc_ids"]) <= known, item["id"]


def test_retrieval_eval_reports_metrics_in_range(retriever):
    report = evaluate_retrieval(retriever, load_golden(), k=5)
    for key in ("hit@1", "hit@5", "mrr"):
        assert 0.0 <= report[key] <= 1.0
    # Even with the weak hashing embeddings, BM25 should carry most of the golden set.
    assert report["hit@5"] >= 0.6, report["per_question"]
    assert len(report["per_question"]) == report["n"]


def test_api_health_and_schema():
    from fastapi.testclient import TestClient

    from ops_copilot.api import main as api

    # Not used as a context manager → lifespan (which builds the graph and needs an
    # API key + MCP subprocesses) does not run; /health needs neither.
    client = TestClient(api.app, raise_server_exceptions=True)
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"
    schema = api.CopilotResponse.model_json_schema()
    assert "pending_approval" in schema["properties"]
