"""Shared graph state.

LangGraph passes one state object through every node. Each node returns a *partial*
update; keys with a reducer (``Annotated[..., reducer]``) are merged, the rest are
overwritten. ``messages`` uses ``add_messages`` so nodes append rather than replace,
and ``evidence`` uses ``merge_evidence`` so every tool result is kept for the reviewer.
"""

from __future__ import annotations

from typing import Annotated, Literal, TypedDict

from langchain_core.messages import AnyMessage
from langgraph.graph.message import add_messages
from pydantic import BaseModel, Field

Route = Literal["knowledge", "triage", "release_notes"]


class Evidence(TypedDict):
    tool: str
    args: dict
    content: str


def merge_evidence(current: list[Evidence] | None, update: list[Evidence] | None) -> list[Evidence]:
    """Append tool results. ``None`` clears the list: ``initial_state`` sends it so a new
    turn on an existing thread does not inherit the previous turn's evidence."""
    if update is None:
        return []
    return (current or []) + update


class CopilotState(TypedDict, total=False):
    messages: Annotated[list[AnyMessage], add_messages]
    task: str
    route: Route | None
    route_reason: str
    evidence: Annotated[list[Evidence], merge_evidence]
    tool_calls: int
    draft: str
    review: dict | None
    attempts: int
    report: dict | None  # typed specialist output (e.g. TriageReport.model_dump()), when the specialist has a schema
    pending_action: dict | None
    approval: dict | None
    final: str


# ----------------------------------------------------------------- structured outputs
class RouteDecision(BaseModel):
    """Supervisor output."""

    route: Route = Field(description="Which specialist should handle the request.")
    reason: str = Field(description="One sentence explaining the choice.")


class TriageReport(BaseModel):
    """Typed output of the triage specialist (rendered to markdown for humans)."""

    severity: Literal["SEV1", "SEV2", "SEV3", "SEV4"]
    severity_rationale: str
    what_we_know: list[str] = Field(description="Facts with citations / record ids.")
    suspected_cause: str
    runbook_id: str | None = Field(default=None, description="Doc id of the matching runbook, if any.")
    runbook_first_steps: list[str] = Field(default_factory=list)
    recent_changes: list[str] = Field(default_factory=list, description="Deployments / config changes, with DEP ids.")
    similar_incidents: list[str] = Field(default_factory=list)
    next_actions: list[str] = Field(description="Numbered, owner-tagged steps incl. escalation.")
    draft_ticket_id: str | None = None
    citations: list[str] = Field(default_factory=list, description="All [doc#chunk] ids used.")

    def to_markdown(self) -> str:
        def bullets(items: list[str]) -> str:
            return "\n".join(f"- {i}" for i in items) or "- (none)"

        def numbered(items: list[str]) -> str:
            return "\n".join(f"{n}. {i}" for n, i in enumerate(items, 1)) or "1. (none)"

        runbook = f"**{self.runbook_id}**\n{numbered(self.runbook_first_steps)}" if self.runbook_id else "No matching runbook found."
        return (
            f"## Severity\n**{self.severity}** — {self.severity_rationale}\n\n"
            f"## What we know\n{bullets(self.what_we_know)}\n\n"
            f"## Suspected cause\n{self.suspected_cause}\n\n"
            f"## Recent changes\n{bullets(self.recent_changes)}\n\n"
            f"## Runbook\n{runbook}\n\n"
            f"## Similar incidents\n{bullets(self.similar_incidents)}\n\n"
            f"## Next actions\n{numbered(self.next_actions)}\n\n"
            f"## Draft ticket\n{self.draft_ticket_id or 'none'}\n\n"
            f"Sources: {', '.join(self.citations) or 'tool records only'}"
        )


class ReviewVerdict(BaseModel):
    """Reviewer (LLM-as-judge) output."""

    verdict: Literal["pass", "revise"]
    faithfulness: int = Field(ge=1, le=5, description="5 = every claim is supported by the evidence.")
    citations_ok: bool = Field(description="Claims drawn from documents carry [doc#chunk] citations.")
    completeness: int = Field(ge=1, le=5, description="5 = fully answers the user's task.")
    feedback: str = Field(description="If 'revise': concrete, actionable fixes. If 'pass': one line.")
