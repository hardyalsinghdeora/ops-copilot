"""CLI commands end to end, with scripted models in place of Claude."""

from __future__ import annotations

import json
import re
from contextlib import asynccontextmanager

from langchain_core.messages import AIMessage
from typer.testing import CliRunner

from ops_copilot import cli, runtime
from ops_copilot.agents import build_graph
from ops_copilot.agents.state import ReviewVerdict, TriageReport
from ops_copilot.config import get_settings

from .fakes import ScriptedChatModel

runner = CliRunner()
PASS = ReviewVerdict(verdict="pass", faithfulness=5, citations_ok=True, completeness=5, feedback="ok")


def _use_graph(monkeypatch, strong, fast, tools) -> None:
    graph = build_graph(strong, fast, tools)

    @asynccontextmanager
    async def scripted_copilot(tool_mode="mcp"):
        yield runtime.Copilot(graph)

    monkeypatch.setattr(runtime, "open_copilot", scripted_copilot)


def test_release_notes_end_to_end(local_tools, monkeypatch):
    strong = ScriptedChatModel(
        responses=[
            AIMessage(
                content="",
                tool_calls=[{"id": "c1", "name": "search_tickets", "args": {"fix_version": "2.14.0", "status": "Done"}}],
            ),
            AIMessage(content="# Navigator 2.14.0 — Release notes\n\n## Fixed\n- Faster checkout (NAV-1)"),
        ]
    )
    _use_graph(monkeypatch, strong, ScriptedChatModel(structured=[PASS]), local_tools)

    result = runner.invoke(cli.app, ["release-notes", "--version", "2.14.0", "--tools", "local", "--evidence"])

    assert result.exit_code == 0, result.output
    assert "route: release_notes" in result.output and "Navigator 2.14.0" in result.output
    assert "search_tickets" in result.output  # --evidence prints the raw tool results
    assert strong.calls[0][-1].content == "Write the release notes for Navigator version 2.14.0."


def test_triage_no_flag_rejects_the_drafted_ticket(local_tools, monkeypatch):
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
    _use_graph(monkeypatch, strong, ScriptedChatModel(structured=[PASS]), local_tools)

    result = runner.invoke(cli.app, ["triage", "PagerDuty: payments-api p99 > 2s", "--tools", "local", "--no"])

    assert result.exit_code == 0, result.output
    match = re.search(r"Ticket (DRAFT-\w+): REJECTED", result.output)
    assert match, result.output
    saved = json.loads((get_settings().drafts_dir / f"{match.group(1)}.json").read_text())
    assert saved["status"] == "rejected"


def test_missing_api_key_is_one_clear_line(monkeypatch):
    monkeypatch.setattr(get_settings(), "anthropic_api_key", None)  # even if a local .env has one

    result = runner.invoke(cli.app, ["ask", "How do I rotate the payments DB credentials?", "--tools", "local"])

    assert result.exit_code == 1
    assert "ANTHROPIC_API_KEY is not set" in result.output and "Traceback" not in result.output


def test_ingest_eval_and_mcp_config(tmp_path, monkeypatch):
    assert runner.invoke(cli.app, ["ingest"]).exit_code == 0

    report_path = tmp_path / "report.json"
    result = runner.invoke(cli.app, ["eval", "--compare", "--output", str(report_path)])
    assert result.exit_code == 0, result.output
    report = json.loads(report_path.read_text())
    assert {"hybrid", "bm25", "vector"} == set(report["compare"])

    monkeypatch.setenv("OPS_COPILOT_API_TOKEN", "api-token-for-the-http-api")
    result = runner.invoke(cli.app, ["mcp-config"])
    servers = json.loads(result.output)["mcpServers"]
    assert set(servers) == {"knowledge", "ops"}
    assert all("ANTHROPIC_API_KEY" not in s.get("env", {}) for s in servers.values())
    assert "api-token-for-the-http-api" not in result.output
