"""Reviewer: LLM-as-judge that checks the draft against the captured evidence.

This is the "Reflexion"-style self-correction loop, with two production guards:
* a hard cap on revision rounds (``max_rounds``) — the graph always terminates;
* the reviewer sees the *raw evidence*, not the specialist's summary of it, so it can
  catch fabricated ids, commands and numbers.
"""

from __future__ import annotations

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import HumanMessage, SystemMessage

from .prompts import REVIEWER, reviewer_input, revision_request
from .state import CopilotState, ReviewVerdict

MAX_EVIDENCE_CHARS = 40_000  # keep the judge call bounded


def render_evidence(state: CopilotState, limit: int = MAX_EVIDENCE_CHARS) -> str:
    parts = []
    for i, ev in enumerate(state.get("evidence", []), start=1):
        parts.append(f"--- evidence {i}: {ev['tool']}({ev['args']})\n{ev['content']}")
    text = "\n\n".join(parts) or "(no tool results were retrieved)"
    return text[:limit] + ("\n…[truncated]" if len(text) > limit else "")


def build_reviewer(llm: BaseChatModel, max_rounds: int = 2):
    judge = llm.with_structured_output(ReviewVerdict)

    def reviewer(state: CopilotState) -> dict:
        draft = state.get("draft", "")
        verdict: ReviewVerdict = judge.invoke(
            [
                SystemMessage(content=REVIEWER),
                HumanMessage(content=reviewer_input(state.get("task", ""), draft, render_evidence(state))),
            ]
        )
        attempts = state.get("attempts", 0)
        review = verdict.model_dump()
        if verdict.verdict == "revise" and attempts < max_rounds:
            return {
                "review": review,
                "attempts": attempts + 1,
                "tool_calls": 0,  # fresh tool budget for the revision
                "messages": [HumanMessage(content=revision_request(verdict.feedback, attempts + 1))],
            }
        # pass — or out of retries: ship what we have but keep the verdict visible
        if verdict.verdict == "revise":
            review["note"] = f"max revision rounds ({max_rounds}) reached; returning best effort"
        return {"review": review, "final": draft}

    return reviewer


def after_review(state: CopilotState) -> str:
    """Conditional edge: retry the specialist, pause for approval, or finish."""
    review = state.get("review") or {}
    if review.get("verdict") == "revise" and "final" not in state:
        return "retry"
    if state.get("pending_action"):
        return "approval"
    return "done"
