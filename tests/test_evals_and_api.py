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


def test_api_triage_approval_flow_blocks_new_turns_while_pending(local_tools, monkeypatch):
    from fastapi.testclient import TestClient
    from langchain_core.messages import AIMessage

    from ops_copilot.agents import build_graph
    from ops_copilot.agents.state import ReviewVerdict, TriageReport
    from ops_copilot.api import main as api

    from .fakes import ScriptedChatModel

    draft = {"summary": "payments p99", "description": "d", "service_id": "payments-api"}
    strong = ScriptedChatModel(
        responses=[
            AIMessage(content="", tool_calls=[{"id": "c1", "name": "draft_ticket", "args": draft}]),
            AIMessage(content="triage done"),
        ],
        structured=[
            TriageReport(severity="SEV2", severity_rationale="r", what_we_know=["x"], suspected_cause="c", next_actions=["a"])
        ],
    )
    fast = ScriptedChatModel(
        structured=[ReviewVerdict(verdict="pass", faithfulness=5, citations_ok=True, completeness=5, feedback="ok")]
    )
    # lifespan does not run without a context manager, so install a scripted graph directly
    monkeypatch.setattr(api.app.state, "graph", build_graph(strong, fast, local_tools), raising=False)
    client = TestClient(api.app)

    paused = client.post("/triage", json={"alert": "PagerDuty: payments-api p99 > 2s"}).json()
    assert paused["pending_approval"]["action"]["type"] == "file_ticket"
    thread_id = paused["thread_id"]

    blocked = client.post("/chat", json={"message": "anything else?", "thread_id": thread_id})
    assert blocked.status_code == 409

    done = client.post("/approve", json={"thread_id": thread_id, "approved": False})
    assert done.status_code == 200 and "REJECTED" in done.json()["answer"]
    assert client.post("/approve", json={"thread_id": thread_id, "approved": True}).status_code == 409
