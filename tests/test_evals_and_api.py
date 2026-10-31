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


async def test_judge_eval_counts_tokens_of_every_copilot_model_call(local_tools, monkeypatch):
    """Reviewer calls never land in state["messages"]; the token totals must still include them."""
    from langchain_core.messages import AIMessage
    from langchain_core.runnables import RunnableLambda

    from ops_copilot import llm
    from ops_copilot.agents.state import ReviewVerdict
    from ops_copilot.evals import run
    from ops_copilot.evals.judge import JudgeScores

    from .fakes import ScriptedChatModel

    def billed(model: str, n_in: int, n_out: int, content: str = "") -> AIMessage:
        usage = {"input_tokens": n_in, "output_tokens": n_out, "total_tokens": n_in + n_out}
        return AIMessage(content=content, usage_metadata=usage, response_metadata={"model_name": model})

    class BilledStructured(ScriptedChatModel):
        """Like a real model, with_structured_output costs a (metered) model call."""

        def with_structured_output(self, schema, **kwargs):
            return RunnableLambda(lambda msgs: self.invoke(msgs)) | super().with_structured_output(schema)

    strong = ScriptedChatModel(responses=[billed("sonnet", 100, 20, "Rotate it [rb-payments-db-credential-rotation#1].")])
    fast = BilledStructured(
        responses=[billed("haiku", 30, 5), billed("haiku", 400, 50)],  # reviewer, then the eval's judge
        structured=[
            ReviewVerdict(verdict="pass", faithfulness=5, citations_ok=True, completeness=5, feedback="ok"),
            JudgeScores(faithfulness=5, relevance=5, reference_agreement=4, rationale="ok"),
        ],
    )
    monkeypatch.setattr(llm, "get_llm", lambda role="strong": strong if role == "strong" else fast)
    golden = [
        {
            "id": "q1",
            "question": "How do I rotate the payments DB credentials?",
            "expected_doc_ids": ["rb-payments-db-credential-rotation"],
            "reference_answer": "Follow the rotation runbook.",
        }
    ]

    out = await run._run_judge(golden)

    assert (out["total_input_tokens"], out["total_output_tokens"]) == (130, 25)  # specialist + reviewer, not the judge
    assert out["tokens_by_model"] == {
        "sonnet": {"input_tokens": 100, "output_tokens": 20},
        "haiku": {"input_tokens": 30, "output_tokens": 5},
    }
    assert out["faithfulness_avg"] == 5 and out["per_question"][0]["input_tokens"] == 130


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


def test_api_requires_the_access_token_when_one_is_set(local_tools, monkeypatch):
    from fastapi.testclient import TestClient
    from pydantic import SecretStr

    from ops_copilot.agents import build_graph
    from ops_copilot.api import main as api
    from ops_copilot.config import get_settings

    from .fakes import ScriptedChatModel

    monkeypatch.setattr(get_settings(), "api_token", SecretStr("s3cret"))
    monkeypatch.setattr(api.app.state, "graph", build_graph(ScriptedChatModel(), ScriptedChatModel(), local_tools), raising=False)
    client = TestClient(api.app)
    body = {"thread_id": "no-such-thread", "approved": True}

    assert client.post("/approve", json=body).status_code == 401
    assert client.post("/approve", json=body, headers={"Authorization": "Bearer wrong"}).status_code == 401
    assert client.post("/approve", json=body, headers={"Authorization": "Bearer s3cret"}).status_code == 409  # past auth

    # what a host's health check and a first-time visitor need stays public
    health = client.get("/health")
    assert health.status_code == 200 and health.json()["auth_required"] is True
    page = client.get("/")
    assert page.status_code == 200 and "Ops Copilot" in page.text


def test_health_is_503_when_the_tool_servers_stop_answering(monkeypatch):
    from fastapi.testclient import TestClient

    from ops_copilot.api import main as api

    class DeadTools:
        async def tools_healthy(self) -> bool:
            return False

    monkeypatch.setattr(api.app.state, "copilot", DeadTools(), raising=False)
    resp = TestClient(api.app).get("/health")
    assert resp.status_code == 503
    assert resp.json()["status"] == "degraded" and resp.json()["tools_ok"] is False
