"""Human-in-the-loop gate for side effects.

``interrupt()`` pauses the graph and surfaces a payload to the caller (CLI / API).
The run is checkpointed; the caller later resumes it with
``graph.ainvoke(Command(resume={...}), config)`` and execution continues *inside this
node* with the human's answer. Nothing leaves the system without that answer.
"""

from __future__ import annotations

import json
from pathlib import Path

from langgraph.types import interrupt

from ..config import get_settings
from .state import CopilotState, Evidence


def find_pending_action(evidence: list[Evidence]) -> dict | None:
    """A ``draft_ticket`` tool result means there is something to approve."""
    for ev in reversed(evidence):
        if ev["tool"] == "draft_ticket":
            try:
                payload = json.loads(ev["content"])
            except json.JSONDecodeError:
                continue
            if "draft_id" in payload:
                # the draft's own "type" is the Jira issue type; ours says what approval is for
                return {**payload, "ticket_type": payload.get("type"), "type": "file_ticket"}
    return None


def collect_pending(state: CopilotState) -> dict:
    return {"pending_action": find_pending_action(state.get("evidence", []))}


def human_approval(state: CopilotState) -> dict:
    action = state.get("pending_action") or {}
    decision = interrupt(
        {
            "question": "Approve filing this ticket?",
            "action": action,
            "hint": "Resume with {'approved': true|false, 'comment': '...'}",
        }
    )
    approved = bool((decision or {}).get("approved"))
    comment = (decision or {}).get("comment", "")
    _mark_draft(action.get("draft_id"), "approved" if approved else "rejected", comment)
    outcome = "APPROVED — ticket would now be filed to Jira." if approved else "REJECTED — draft discarded."
    final = (state.get("final") or state.get("draft", "")) + f"\n\n---\nTicket {action.get('draft_id')}: {outcome}"
    if comment:
        final += f" ({comment})"
    return {"approval": {"approved": approved, "comment": comment}, "final": final, "pending_action": None}


def _mark_draft(draft_id: str | None, status: str, comment: str) -> None:
    if not draft_id:
        return
    path: Path = get_settings().drafts_dir / f"{draft_id}.json"
    if not path.exists():
        return
    data = json.loads(path.read_text(encoding="utf-8"))
    data["status"] = status
    data["review_comment"] = comment
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")
