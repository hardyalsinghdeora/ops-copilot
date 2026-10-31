# ADR 0003 — Supervisor, specialists and reviewer instead of one agent

## Status

Accepted.

## Context

The system handles three distinct tasks: grounded knowledge Q&A, incident triage, and release-note generation. They need different instructions (cite chunks; produce a structured `TriageReport`; group tickets by capability), different tool subsets (three, eight and five of the nine MCP tools — only triage can `draft_ticket`, only knowledge can `list_documents`), and different output formats.

A single agent with one long system prompt and all nine tools can do all three, with predictable weaknesses: the prompt grows to cover every case and conditional rules are followed less reliably; tool selection gets noisier because irrelevant tools are always available; and there is no independent check, since a context asked to verify its own answer almost always approves it.

An on-call tool needs the opposite: narrow, reliable behaviour per task; minimal tool exposure per task; and an output check that can say no.

## Decision

`ops_copilot.agents.graph.build_graph` wires a LangGraph `StateGraph` over `CopilotState` with:

- **`supervisor`** (`ops_copilot.agents.supervisor`): fast model, structured output, sets `route` to `knowledge`, `triage` or `release_notes`. No tools.
- **Three specialists** (`knowledge_agent`, `triage_agent`, `release_agent`): strong model, each a ReAct loop over its own tool subset via `bind_tools`, writing `draft` and `evidence`.
- **`reviewer`** (`ops_copilot.agents.reviewer`): fast model, LLM-as-judge with a fresh context containing only draft and evidence; returns `pass` or `revise` with feedback. On `revise` the graph loops to the same specialist with the feedback; at most two rounds (`attempts`).
- **`collect_pending`** records a drafted ticket as `pending_action`; after the reviewer passes the draft, **`human_approval`** interrupts the graph for that ticket (see ADR 0004). Read-only requests never pause.

This is the supervisor pattern. The reviewer is the "evaluator" in an evaluator-optimizer loop.

## Consequences

- Each prompt is short and testable alone; adding a task is adding a node and a route.
- Per-specialist tool scoping limits routing noise and the reach of prompt injection from documents.
- An independent reviewer catches unsupported claims and broken citations the author would not.
- Every request costs at least three model calls, plus up to two extra specialist runs on revision; the cap bounds this.
- Misrouting is a single point of failure; the supervisor prompt carries examples per route, and `route` can be pre-set by the caller (`ops-copilot triage`, `POST /triage`) to bypass routing entirely.
- Coordination state (`route`, `attempts`, `review`, `pending_action`) is managed explicitly through reducers and conditional edges.

## Alternatives considered

- **One mega-agent.** Fewer parts. Rejected for prompt dilution, tool noise and no veto.
- **Swarm / handoff.** Suits shifting conversations; here each request is one task and auditability matters more. Rejected.
- **Hierarchical supervisors.** Unnecessary at three specialists; revisit if task types multiply.
- **Self-review in the specialist's context.** Cheaper, but approves its own errors. Rejected for a separate reviewer with an independent context.
