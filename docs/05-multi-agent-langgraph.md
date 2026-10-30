# 05 — Multi-Agent Systems with LangGraph

*Who this is for: engineers who can write a single ReAct loop (Chapter 02) and have tools (Chapter 04), and now need several agents to cooperate safely. Time to complete: about 4 hours.*

A single agent with every tool and one long system prompt works for demos and degrades in practice. This chapter explains why, introduces the LangGraph primitives the project uses, and walks through `ops_copilot.agents.graph.build_graph` node by node.

## Why multiple agents

The spec's reasoning is three words long: **prompt focus, tool scoping, veto.**

1. **Prompt focus.** A triage prompt and a release-notes prompt have almost nothing in common. One prompt that covers both is long, and long prompts with many conditional rules are followed less reliably. Three short prompts, each with one job, are each easier to get right and to test.
2. **Tool scoping.** The knowledge agent needs three tools. The triage agent needs seven. The release agent needs three. Giving every agent all nine makes tool selection noisier and widens the blast radius of prompt injection (Chapter 02).
3. **Veto.** A separate reviewer with a different prompt and no stake in the draft can say no. The same model, in the same context, asked "is your answer correct?" will usually say yes.

The cost is coordination: something has to route, carry state between agents, and stop loops. That is what LangGraph is for.

## LangGraph concepts you need

LangGraph models an application as a **state machine**: a typed state object, nodes that are functions from state to state updates, and edges that decide what runs next.

### State

```python
# ops_copilot/agents/state.py (illustrative)
from typing import Annotated, Literal, TypedDict
from langchain_core.messages import AnyMessage
from langgraph.graph.message import add_messages

class CopilotState(TypedDict):
    messages: Annotated[list[AnyMessage], add_messages]   # reducer: append, not replace
    route: Literal["knowledge", "triage", "release_notes"] | None
    evidence: list[dict]                                   # retrieved chunks and tool results the reviewer may check
    draft: str | None                                      # the specialist's current answer / report
    review: dict | None                                    # {"verdict": "pass"|"revise", "feedback": str}
    attempts: int                                          # specialist rounds so far
    final: str | None                                      # what the user sees
    pending_action: dict | None                            # e.g. a drafted ticket awaiting approval
```

The `Annotated[..., add_messages]` is a **reducer**: when a node returns `{"messages": [new_msg]}`, LangGraph appends rather than overwrites. Fields without a reducer are replaced. `MessagesState` is the prebuilt TypedDict with just `messages` and that reducer; `CopilotState` extends the same idea with the fields the reviewer and approval gate need.

### Nodes and edges

A **node** is a function `(state) -> partial_state`. An **edge** connects two nodes unconditionally. A **conditional edge** calls a function that returns the name of the next node, so routing is just Python.

### Checkpointer and threads

A **checkpointer** persists the state after every node, keyed by a `thread_id` in the run config. This gives you:

- Resumability: a graph can stop (for an interrupt) and continue later from the same point.
- Memory across turns in the API: `POST /chat` with the same `thread_id` continues the conversation.
- Time travel for debugging: inspect the state after any node.

The CLI uses an in-memory checkpointer (`MemorySaver`). The API would need a persistent one (SQLite or Postgres); see Chapter 07.

### `interrupt()` and `Command(resume=...)`

Inside a node, `interrupt(payload)` pauses the graph, surfaces `payload` to the caller, and saves the state. The caller later invokes the graph again with `Command(resume=value)`, the node re-runs, and `interrupt()` returns `value`. This is LangGraph's human-in-the-loop primitive and it is how `human_approval` works.

## The project's graph

```
supervisor ──▶ knowledge ─┐
    │          triage    ─┼──▶ reviewer ──▶ END
    │          release   ─┘        │
    └──────── (routing) ◀── retry ─┘       (attempts < 2 on `revise`)

triage ──▶ human_approval ──▶ reviewer     (when a ticket was drafted)
```

```python
# ops_copilot/agents/graph.py (illustrative)
from langgraph.graph import StateGraph, END
from ops_copilot.agents.state import CopilotState
from ops_copilot.agents import supervisor, knowledge_agent, triage_agent, release_agent, reviewer, human_approval

MAX_REVIEW_ROUNDS = 2  # Settings.max_review_rounds

def build_graph(llm_strong, llm_fast, tools, checkpointer):
    g = StateGraph(CopilotState)

    g.add_node("supervisor", supervisor.make_node(llm_fast))
    g.add_node("knowledge", knowledge_agent.make_node(llm_strong, tools))
    g.add_node("triage", triage_agent.make_node(llm_strong, tools))
    g.add_node("release_notes", release_agent.make_node(llm_strong, tools))
    g.add_node("human_approval", human_approval.node)
    g.add_node("reviewer", reviewer.make_node(llm_fast))

    g.set_entry_point("supervisor")
    g.add_conditional_edges("supervisor", lambda s: s["route"],
                            {"knowledge": "knowledge", "triage": "triage", "release_notes": "release_notes"})
    g.add_edge("knowledge", "reviewer")
    g.add_edge("release_notes", "reviewer")
    g.add_conditional_edges("triage", lambda s: "human_approval" if s.get("pending_action") else "reviewer")
    g.add_edge("human_approval", "reviewer")

    def after_review(s: CopilotState):
        if s["review"]["verdict"] == "pass" or s["attempts"] >= MAX_REVIEW_ROUNDS:
            return END
        return s["route"]                       # back to the same specialist with feedback
    g.add_conditional_edges("reviewer", after_review)

    return g.compile(checkpointer=checkpointer)
```

### Node by node

**`supervisor`** (`ops_copilot.agents.supervisor`). Fast model, structured output, one job: set `route`. It reads only the latest user message. It does not call tools. If routing is ambiguous it defaults to `knowledge`, which is the safe path (read-only, no side effects).

**Specialists** (`knowledge_agent`, `triage_agent`, `release_agent`). Each is the ReAct sub-graph from Chapter 02, compiled and added as one node. Each:

- binds its tool subset with `llm_strong.bind_tools(...)`;
- runs the model/tool loop with a per-run cap on tool calls;
- copies retrieval results and relevant tool outputs into `evidence`;
- writes its answer to `draft` and increments `attempts`;
- if `review` contains feedback from a previous round, includes it in the prompt so the retry is informed, not a blind re-roll (the Reflexion idea).

The triage agent additionally produces a `TriageReport` and, when it calls `draft_ticket`, stores the returned draft in `pending_action`.

**`human_approval`** (`ops_copilot.agents.human_approval`).

```python
# ops_copilot/agents/human_approval.py (illustrative)
from langgraph.types import interrupt

def node(state: CopilotState):
    draft = state["pending_action"]
    decision = interrupt({"type": "approve_ticket", "draft": draft})   # graph pauses here
    if decision.get("approved"):
        path = file_ticket(draft)                                       # writes data/.drafts/<id>.json
        note = f"Ticket filed: {path}"
    else:
        note = "Ticket not filed (rejected by user)."
    return {"pending_action": None, "messages": [("system", note)]}
```

The CLI sees the interrupt payload, prints the draft, asks `Approve? [y/N]`, and resumes with `Command(resume={"approved": True})` or `False`. Nothing is written until that resume. See [`adr/0004-human-in-the-loop-side-effects.md`](adr/0004-human-in-the-loop-side-effects.md).

**`reviewer`** (`ops_copilot.agents.reviewer`). Below.

## How the reviewer works

The reviewer is an **LLM-as-judge**: a second model call whose only job is to evaluate the first. It uses the fast model because the task is narrow and the volume is high (every answer).

Its prompt gives it `draft` and `evidence` and asks for a structured verdict:

```python
class ReviewVerdict(BaseModel):
    verdict: Literal["pass", "revise"]
    faithfulness: int                  # 1-5: are the claims supported by the evidence?
    citations_ok: bool                 # do document facts carry [doc#chunk] ids present in evidence?
    completeness: int                  # 1-5: does it answer the task in the required format?
    feedback: str                      # what the specialist should change
```

The checks:

1. **Faithfulness.** For each factual claim in the draft, is there a chunk or tool result in `evidence` that supports it? A claim with no support is a hallucination, however plausible.
2. **Citation integrity.** Does every `[doc_id#chunk]` in the draft refer to an id that was actually retrieved? This part can and should also be done with a regex before the model is called; the model handles the semantic half.
3. **Completeness relative to the question** (light-touch). Did it answer what was asked?

On `revise`, the graph routes back to the same specialist with `review.feedback` in context. On `pass`, or after two rounds, `final` is set and the graph ends. After the second failed round the final answer is returned with a visible warning that review did not pass, rather than silently shipping it.

Why cap at two: each round is another full specialist run (several tool calls and model calls). Beyond two rounds the fix is almost always a prompt or retrieval problem, not something a third attempt will solve, and the eval harness is where you find that.

## Supervisor vs swarm vs hierarchical

| Pattern | Shape | Good for | Cost |
|---|---|---|---|
| **Supervisor** (this project) | One router decides which specialist runs; results come back through a fixed path | Clear, disjoint task types; need for central control and audit | The router is a single point of misrouting |
| **Swarm / handoff** | Any agent can hand off to any other; no central router | Open-ended conversations where the task type shifts mid-way | Hard to reason about, hard to bound, hard to audit |
| **Hierarchical** | Supervisors of supervisors; each team has its own router | Very large tool sets, many teams | More latency and more prompts to maintain |

Supervisor fits here because the three tasks are disjoint and each run is one request, not a long conversation. If a fourth task arrived that overlapped heavily with triage, the router would get worse, and that would be the moment to revisit. See [`adr/0003-supervisor-specialists-reviewer.md`](adr/0003-supervisor-specialists-reviewer.md).

## Failure modes and the guards against them

| Failure | What it looks like | Guard in this project |
|---|---|---|
| **Infinite review loop** | Specialist and reviewer disagree forever | `attempts` counter in state; `after_review` ends at `MAX_REVIEW_ROUNDS = 2  # Settings.max_review_rounds` |
| **Tool thrash** | Agent calls `search_knowledge` twelve times with slightly different queries | Per-run max tool calls inside each specialist; after the cap the model is forced to answer with what it has |
| **Context bloat** | Every tool result stays in `messages`; later calls are slow and expensive | Small k; `get_document` returns bounded text; evidence is deduplicated by chunk id; `messages` is per-run, not accumulated across requests in the CLI |
| **Misrouting** | A triage request goes to `knowledge`, which cannot see alerts | Few-shot examples in the supervisor prompt; default to `knowledge` only when genuinely ambiguous; an eval case per route |
| **Side effect without consent** | A ticket gets filed because the model decided to | The only write path is `human_approval`; `draft_ticket` itself writes nothing |
| **Lost interrupt** | API process restarts between interrupt and resume | In-memory checkpointer is CLI-only; a persistent checkpointer is required for `serve` (Chapter 07) |
| **Silent reviewer failure** | Reviewer returns malformed output and the graph passes the draft | Structured output; on validation error, treat as `revise` with generic feedback |

## Common pitfalls

- **Forgetting the reducer.** Returning `{"messages": [msg]}` from a node without `add_messages` on the field replaces the whole history.
- **Mutating state in place.** Nodes must return updates; mutating the input dict bypasses reducers and checkpoints.
- **One shared system prompt.** Defeats the point of specialists.
- **Reviewer with the same model and context as the specialist.** Give it a fresh context with only draft and evidence; do not show it the specialist's reasoning.
- **No `thread_id`.** Interrupts need a thread to resume into.
- **Retrying without feedback.** A blind retry at temperature 0 produces the same answer.

## Exercises

1. **Draw the state.** Run `ops-copilot ask` with the graph's stream mode set to values (or add logging in each node) and print `route`, `attempts`, `len(evidence)` and `review` after every node. Confirm the sequence matches the request lifecycle in `architecture.md`.
2. **Trigger a revise.** Weaken the knowledge agent's prompt (remove the "only from evidence" line) and ask something the knowledge base covers partially. Observe the reviewer's low `faithfulness` score and `feedback`, and the second attempt. Restore the prompt.
3. **Reject a ticket.** Run `ops-copilot triage` and answer `N` at the approval prompt. Verify `data/.drafts/` is unchanged, then inspect the checkpointer's saved state for the thread to see `pending_action` cleared.
4. **Add a fourth agent (design only, then optionally build).** Specify a `status_update` agent that writes a customer-facing incident update from the triage report and recent alerts. Which tools does it need? What would the supervisor's few-shot examples look like? Where does it join the graph? Building it is the Week 4 stretch project in [`exercises.md`](exercises.md).

## Further reading

- LangGraph documentation (langchain-ai.github.io/langgraph): concepts (state, reducers, checkpointers), "Human-in-the-loop" (`interrupt`, `Command`), and the multi-agent tutorials covering supervisor, swarm and hierarchical patterns.
- *ReAct: Synergizing Reasoning and Acting in Language Models*, Yao et al., 2022 (arxiv.org/abs/2210.03629).
- *Reflexion: Language Agents with Verbal Reinforcement Learning*, Shinn et al., 2023 (arxiv.org/abs/2303.11366). The basis for feeding reviewer feedback into the retry.
- *Judging LLM-as-a-Judge with MT-Bench and Chatbot Arena*, Zheng et al., 2023 (arxiv.org/abs/2306.05685). Strengths and known biases of using a model as the judge.
- Anthropic documentation, "Building effective agents" guidance and the agent/tool-use pages (docs.claude.com), for the argument that simple, composable patterns beat frameworks-first designs.
