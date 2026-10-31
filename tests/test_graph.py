"""Multi-agent graph behaviour with scripted models (no API key needed)."""

from __future__ import annotations

import json

from langchain_core.messages import AIMessage
from langgraph.types import Command

from ops_copilot.agents import build_graph, final_answer, initial_state, thread_config
from ops_copilot.agents.state import ReviewVerdict, RouteDecision, TriageReport

from .fakes import ScriptedChatModel


def _tc(name, args, cid="c1"):
    return {"id": cid, "name": name, "args": args}


def test_supervisor_routes_and_knowledge_agent_cites(local_tools):
    strong = ScriptedChatModel(
        responses=[
            AIMessage(content="", tool_calls=[_tc("search_knowledge", {"query": "rotate payments db credentials", "k": 3})]),
            AIMessage(content="Rotate via the secrets manager, then restart pods [rb-payments-db-credential-rotation#1]."),
        ]
    )
    fast = ScriptedChatModel(
        structured=[
            RouteDecision(route="knowledge", reason="procedure question"),
            ReviewVerdict(verdict="pass", faithfulness=5, citations_ok=True, completeness=5, feedback="ok"),
        ]
    )
    g = build_graph(strong, fast, local_tools)
    out = g.invoke(initial_state("How do I rotate the payments DB credentials?"), thread_config("k1"))

    assert out["route"] == "knowledge"
    assert [e["tool"] for e in out["evidence"]] == ["search_knowledge"]
    assert json.loads(out["evidence"][0]["content"])  # real retriever output was captured
    assert out["review"]["verdict"] == "pass"
    assert "rb-payments-db-credential-rotation" in final_answer(out)
    # the knowledge agent must only see knowledge tools (least privilege)
    sys_prompt = strong.calls[0][0].content
    assert "Knowledge specialist" in sys_prompt


def test_reviewer_revise_loop_then_pass_without_duplicating_evidence(local_tools):
    strong = ScriptedChatModel(
        responses=[
            AIMessage(content="", tool_calls=[_tc("search_knowledge", {"query": "kafka lag", "k": 2})]),
            AIMessage(content="Scale consumers to 50."),  # unsupported number → revise
            AIMessage(content="Scale consumers per the runbook [rb-assessment-kafka-consumer-lag#2]."),
        ]
    )
    fast = ScriptedChatModel(
        structured=[
            RouteDecision(route="knowledge", reason="q"),
            ReviewVerdict(verdict="revise", faithfulness=2, citations_ok=False, completeness=3, feedback="'50' is not in the evidence"),
            ReviewVerdict(verdict="pass", faithfulness=5, citations_ok=True, completeness=4, feedback="ok"),
        ]
    )
    g = build_graph(strong, fast, local_tools, max_review_rounds=2)
    out = g.invoke(initial_state("assessment engine consumer lag, what do I do?"), thread_config("r1"))

    assert out["attempts"] == 1
    assert [e["tool"] for e in out["evidence"]] == ["search_knowledge"]  # not duplicated by the retry
    assert any("REVIEWER FEEDBACK" in m.content for m in out["messages"] if m.type == "human")
    assert final_answer(out).startswith("Scale consumers per the runbook")


def test_review_rounds_are_capped(local_tools):
    strong = ScriptedChatModel(responses=[AIMessage(content="draft v1"), AIMessage(content="draft v2")])
    fast = ScriptedChatModel(
        structured=[
            RouteDecision(route="knowledge", reason="q"),
            ReviewVerdict(verdict="revise", faithfulness=1, citations_ok=False, completeness=1, feedback="bad"),
            ReviewVerdict(verdict="revise", faithfulness=1, citations_ok=False, completeness=1, feedback="still bad"),
        ]
    )
    g = build_graph(strong, fast, local_tools, max_review_rounds=1)
    out = g.invoke(initial_state("anything"), thread_config("cap"))
    assert out["attempts"] == 1
    assert "max revision rounds" in out["review"]["note"]
    assert final_answer(out) == "draft v2"


def test_tool_budget_forces_final_answer(local_tools):
    strong = ScriptedChatModel(
        responses=[
            AIMessage(content="", tool_calls=[_tc("list_documents", {})]),
            AIMessage(content="", tool_calls=[_tc("list_documents", {}, "c2")]),
            AIMessage(content="Final from evidence."),
        ]
    )
    fast = ScriptedChatModel(
        structured=[RouteDecision(route="knowledge", reason="q"), ReviewVerdict(verdict="pass", faithfulness=4, citations_ok=True, completeness=3, feedback="ok")]
    )
    g = build_graph(strong, fast, local_tools, max_tool_calls=2)
    out = g.invoke(initial_state("list everything"), thread_config("budget"))
    assert out["tool_calls"] == 2
    assert final_answer(out) == "Final from evidence."
    assert "Tool budget exhausted" in strong.calls[-1][-1].content


def test_triage_interrupts_for_ticket_approval_and_resumes(local_tools):
    strong = ScriptedChatModel(
        responses=[
            AIMessage(
                content="",
                tool_calls=[
                    _tc("get_active_alerts", {"service_id": "payments-api"}, "c1"),
                    _tc("draft_ticket", {"summary": "payments p99 latency", "description": "...", "service_id": "payments-api"}, "c2"),
                ],
            ),
            AIMessage(content="SEV2, latency after deploy, drafted a ticket."),
        ],
        structured=[
            TriageReport(
                severity="SEV2",
                severity_rationale="p99 SLO breached, checkout degraded",
                what_we_know=["ALT-1042 firing for payments-api"],
                suspected_cause="connection pool change in the latest deploy",
                runbook_id="rb-payments-p99-latency-spike",
                runbook_first_steps=["check pool saturation", "roll back"],
                next_actions=["@Priya roll back DEP", "escalate if no recovery in 15 min"],
                draft_ticket_id="DRAFT-?",
                citations=["rb-payments-p99-latency-spike#1"],
            )
        ],
    )
    fast = ScriptedChatModel(structured=[ReviewVerdict(verdict="pass", faithfulness=5, citations_ok=True, completeness=4, feedback="ok")])
    g = build_graph(strong, fast, local_tools)
    cfg = thread_config("t1")

    paused = g.invoke(initial_state("PagerDuty: payments-api p99 > 2s", route="triage"), cfg)
    assert paused["route"] == "triage"  # route pre-set → supervisor skipped (no RouteDecision consumed)
    assert paused["pending_action"]["type"] == "file_ticket"
    assert paused["report"]["severity"] == "SEV2"  # typed TriageReport captured
    assert paused["draft"].startswith("## Severity\n**SEV2**")  # rendered for humans/reviewer
    assert paused["__interrupt__"][0].value["question"].startswith("Approve")

    done = g.invoke(Command(resume={"approved": False, "comment": "duplicate of NAV-4821"}), cfg)
    assert done["approval"] == {"approved": False, "comment": "duplicate of NAV-4821"}
    assert "REJECTED" in final_answer(done) and "duplicate of NAV-4821" in final_answer(done)
    assert done["pending_action"] is None


def test_release_agent_cannot_draft_tickets(local_tools):
    from ops_copilot.agents.release_agent import build_release_agent
    from ops_copilot.agents.specialist import build_specialist  # noqa: F401  (import check)

    captured = {}

    class Spy(ScriptedChatModel):
        def bind_tools(self, tools, **kwargs):
            captured["tools"] = {t.name for t in tools}
            return self

    build_release_agent(Spy(responses=[]), local_tools)
    assert "draft_ticket" not in captured["tools"]
    assert {"search_tickets", "list_recent_deployments"} <= captured["tools"]


def test_each_specialist_sees_only_its_tools(local_tools):
    """Least privilege: the spec's 3 / 8 / 5 split (ADR 0003)."""
    from ops_copilot.agents.knowledge_agent import build_knowledge_agent
    from ops_copilot.agents.release_agent import build_release_agent
    from ops_copilot.agents.triage_agent import build_triage_agent
    from ops_copilot.mcp_client import split_tools

    seen: dict[str, set[str]] = {}

    def spy(name):
        class Spy(ScriptedChatModel):
            def bind_tools(self, tools, **kwargs):
                seen[name] = {t.name for t in tools}
                return self

        return Spy(responses=[])

    knowledge_tools, _ = split_tools(local_tools)
    build_knowledge_agent(spy("knowledge"), knowledge_tools)
    build_triage_agent(spy("triage"), local_tools)
    build_release_agent(spy("release"), local_tools)

    assert seen["knowledge"] == {"search_knowledge", "get_document", "list_documents"}
    assert len(seen["triage"]) == 8 and "draft_ticket" in seen["triage"] and "list_documents" not in seen["triage"]
    assert len(seen["release"]) == 5 and "draft_ticket" not in seen["release"]
