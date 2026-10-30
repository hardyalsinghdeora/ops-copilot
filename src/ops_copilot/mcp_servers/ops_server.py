"""MCP server: live operational data (services, alerts, deployments, tickets).

In this repo the data comes from JSON fixtures in ``data/fixtures`` so the project
runs anywhere. In production each tool body would call PagerDuty / Jira / ArgoCD —
the *agents would not change at all*, which is the whole point of putting tools
behind MCP (see docs/adr/0002-mcp-for-all-tools.md).

Every tool is read-only except ``draft_ticket``, which only writes a draft to
``data/.drafts`` — the LangGraph graph requires human approval before anything is
"filed" (docs/adr/0004-human-in-the-loop-side-effects.md).

Run standalone: ``python -m ops_copilot.mcp_servers.ops_server``
"""

from __future__ import annotations

import json
import logging
import uuid
from datetime import UTC, datetime, timedelta
from functools import lru_cache
from pathlib import Path

from mcp.server.fastmcp import FastMCP

from ..config import get_settings

log = logging.getLogger(__name__)

mcp = FastMCP(
    "ops-copilot-ops",
    instructions="Read-only access to services, alerts, deployments and tickets for the Navigator platform.",
)

_fixtures_override: Path | None = None


def set_fixtures_dir(path: Path | None) -> None:
    global _fixtures_override
    _fixtures_override = path
    _load.cache_clear()


@lru_cache
def _load(name: str) -> list[dict]:
    base = _fixtures_override or get_settings().fixtures_dir
    return json.loads((base / f"{name}.json").read_text(encoding="utf-8"))


def _dump(obj) -> str:
    return json.dumps(obj, ensure_ascii=False, indent=None)


def _parse_ts(ts: str) -> datetime:
    dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
    return dt if dt.tzinfo else dt.replace(tzinfo=UTC)


def _now() -> datetime:
    """'Now' for the fixtures = the newest timestamp in the data, so demos stay stable."""
    stamps = [_parse_ts(a["started_at"]) for a in _load("alerts")] + [
        _parse_ts(d["deployed_at"]) for d in _load("deployments")
    ]
    return max(stamps) if stamps else datetime.now(UTC)


@mcp.tool()
def get_service(service_id: str) -> str:
    """Service catalogue entry: owner, on-call, dependencies, dashboards, SLOs, runbook ids,
    Slack channel. service_id e.g. "payments-api"."""
    for s in _load("services"):
        if s["id"] == service_id:
            return _dump(s)
    ids = [s["id"] for s in _load("services")]
    return _dump({"error": f"unknown service '{service_id}'", "known_services": ids})


@mcp.tool()
def get_active_alerts(service_id: str | None = None, include_resolved: bool = False) -> str:
    """Alerts currently firing (optionally for one service). Set include_resolved=true to
    also see recently resolved alerts. Each alert carries the runbook_id to consult."""
    alerts = _load("alerts")
    out = [
        a
        for a in alerts
        if (include_resolved or a["status"] == "firing") and (not service_id or a["service"] == service_id)
    ]
    out.sort(key=lambda a: a["started_at"], reverse=True)
    return _dump(out)


@mcp.tool()
def list_recent_deployments(service_id: str | None = None, hours: int = 24, limit: int = 10) -> str:
    """Production deployments in the last N hours (newest first). The first thing to check
    in any incident: what changed?"""
    cutoff = _now() - timedelta(hours=max(1, int(hours)))
    deps = [
        d
        for d in _load("deployments")
        if _parse_ts(d["deployed_at"]) >= cutoff and (not service_id or d["service"] == service_id)
    ]
    deps.sort(key=lambda d: d["deployed_at"], reverse=True)
    return _dump(deps[: max(1, int(limit))])


@mcp.tool()
def search_tickets(
    query: str | None = None,
    service_id: str | None = None,
    status: str | None = None,
    fix_version: str | None = None,
    ticket_type: str | None = None,
    limit: int = 20,
) -> str:
    """Search Jira-style tickets by free text (summary/description/labels), service,
    status (To Do|In Progress|In Review|Done), fix_version (e.g. "2.14.0") or type
    (Bug|Story|Task|Incident). Use fix_version to gather release-notes content."""
    q = (query or "").lower().split()
    out = []
    for t in _load("tickets"):
        if service_id and t.get("service") != service_id:
            continue
        if status and t.get("status") != status:
            continue
        if fix_version and t.get("fix_version") != fix_version:
            continue
        if ticket_type and t.get("type") != ticket_type:
            continue
        hay = " ".join([t.get("summary", ""), t.get("description", ""), " ".join(t.get("labels", []))]).lower()
        if q and not all(term in hay for term in q):
            continue
        out.append(t)
    out.sort(key=lambda t: t.get("updated", ""), reverse=True)
    return _dump(out[: max(1, int(limit))])


@mcp.tool()
def get_ticket(key: str) -> str:
    """Full ticket by key, e.g. "NAV-4821"."""
    for t in _load("tickets"):
        if t["key"].lower() == key.lower():
            return _dump(t)
    return _dump({"error": f"no ticket '{key}'"})


@mcp.tool()
def draft_ticket(summary: str, description: str, service_id: str, priority: str = "High", ticket_type: str = "Incident") -> str:
    """Create a DRAFT ticket. Nothing is filed: the draft is saved locally and must be
    approved by a human before it would be sent to Jira. Returns the draft id."""
    s = get_settings()
    s.drafts_dir.mkdir(parents=True, exist_ok=True)
    draft_id = f"DRAFT-{uuid.uuid4().hex[:8]}"
    payload = {
        "draft_id": draft_id,
        "summary": summary,
        "description": description,
        "service": service_id,
        "priority": priority,
        "type": ticket_type,
        "status": "draft",
        "created_at": datetime.now(UTC).isoformat(),
    }
    (s.drafts_dir / f"{draft_id}.json").write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return _dump(payload)


TOOLS = [get_service, get_active_alerts, list_recent_deployments, search_tickets, get_ticket, draft_ticket]


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
