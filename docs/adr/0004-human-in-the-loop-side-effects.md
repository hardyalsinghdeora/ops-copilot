# ADR 0004 — Human approval before any side effect

## Status

Accepted.

## Context

Eight of the nine tools read. One action writes to the outside world: filing a ticket at the end of triage. In production that creates a Jira issue visible to a team, may page someone, and is hard to retract.

The model's decision to take that action is shaped by everything in its context, including retrieved documents and tool results the operator does not control. Prompt injection via a runbook is a realistic path to a bad ticket; so is a triage agent misreading an alert and filing a sev1 against the wrong service at 2am.

Drafting the ticket is still valuable: the agent has already gathered the alert, owner, recent deployments and similar incidents. The goal is to keep the drafting and gate the filing.

LangGraph's `interrupt()` pauses a graph inside a node, surfaces a payload, persists state via the checkpointer, and resumes with `Command(resume=...)`.

## Decision

Anything that writes to the outside world goes through `interrupt()`.

- The MCP tool `draft_ticket` in `ops_copilot.mcp_servers.ops_server` **does not file anything**. It saves a draft to `data/.drafts/` and returns it with a `draft_id`. The `collect_pending` node finds that tool result in `evidence` and stores it in `CopilotState.pending_action`.
- The reviewer validates the triage report first; a draft that fails review is revised before anyone is asked to approve a ticket based on it.
- `ops_copilot.agents.human_approval` is a graph node that calls `interrupt({"question": ..., "action": {"type": "file_ticket", "draft_id": ..., ...}, "hint": ...})`. The CLI prints the action and asks for approval; the API returns the payload as `pending_approval` with the `thread_id`.
- On `Command(resume={"approved": True, "comment": ...})` the node marks the draft `approved` (in v1 that is where a real tracker call would go). On rejection it marks it `rejected`. Either way the decision is appended to the final answer and the graph ends.
- Any future write tool (`create_ticket`, `acknowledge_alert`, `post_status_update`) must follow the same pattern: tool returns an intent, a node behind `interrupt()` executes it.

## Consequences

- No ticket is created without a person seeing the exact draft and saying yes. This is the backstop when prompting, scoping and review all fail.
- Triage becomes a two-step interaction; the UX must accommodate a pause.
- The checkpointer is load-bearing: in-memory is fine for the CLI, but the API needs a persistent one so a pause on one process can resume on another.
- Approval decisions are recorded in state and auditable.
- Read-only paths (knowledge Q&A) gain no friction.

## Alternatives considered

- **Let the agent file tickets directly.** Unacceptable risk of wrong or injected writes. Rejected.
- **A second model call ("are you sure?").** Not independent of the context that produced the decision. The reviewer still runs, as a quality check, not an authorisation.
- **Allow-list of "safe" automatic writes (low-severity tickets).** Severity is itself a model judgement. Deferred until there is data on rejection rates.
- **File first, let humans delete.** Shifts cost to humans and leaves artefacts. Rejected.
