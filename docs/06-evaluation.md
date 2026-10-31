# 06 — Evaluation

*Who this is for: engineers who have changed a prompt or a chunk size and want to know whether it helped. Time to complete: about 2.5 hours.*

Every other chapter ends with "measure it". This one explains how. The spec's phrasing is the point: **evals are part of the repo, so changes to chunking and prompts are measured, not vibes.**

## Why evals

LLM systems fail differently from ordinary software. A unit test asserts one exact output; a model's output varies, and "better" is a distribution shift, not a boolean. Without an eval harness you end up:

- tuning chunk size by rerunning three favourite questions and squinting;
- shipping a prompt change that fixed one case and broke four you did not try;
- unable to say, in a review or an interview, *how good* the system is.

An eval harness turns "I think the answers got better" into "hit@5 went from X to Y and faithfulness from A to B on the same 40 questions". It is the unit-test equivalent for this kind of system, and like unit tests it pays for itself the first time it catches a regression.

## The golden set

`src/ops_copilot/evals/golden.json` is a list of question/answer cases written by hand against the knowledge base and fixtures. Each case carries what the harness needs to score both retrieval and generation:

```json
[
  {
    "id": "q01",
    "question": "payments is slow, what do I check first?",
    "expected_doc_ids": ["rb-payments-p99-latency-spike"],
    "reference_answer": "Check for a payments-api deployment in the last two hours and roll it back first; then check the database pool (waiting_count above 5 means saturation), PayGate latency, Postgres and traffic.",
    "category": "runbook"
  },
  {
    "id": "q16",
    "question": "What does ERR_CONN_RESET from PayGate mean during webhook delivery?",
    "expected_doc_ids": ["rb-paygate-webhook-failures-duplicate-charges"],
    "reference_answer": "PayGate dropped the connection, usually because the handler held it past their 10s budget during a database stall; treat it like a timeout.",
    "category": "runbook"
  },
  {
    "id": "neg01",
    "question": "How do I configure the mobile app's offline mode for students?",
    "expected_doc_ids": [],
    "reference_answer": "The knowledge base does not cover the mobile app; the assistant should say so and point to the service owner rather than guess.",
    "category": "negative"
  }
]
```

Fields: `id`, `question`, `expected_doc_ids` (empty for a **negative** case), `reference_answer` (short, human-written, used by the judge), `category` (`runbook` | `adr` | `postmortem` | `service` | `negative`). The committed set has 16 positive cases and 2 negatives — smaller than the thirty-to-fifty recommended below, which is exercise 1.

Principles for a good golden set:

- **Cover each retrieval failure mode.** Exact identifiers (where BM25 should win), paraphrased concepts (where vectors should win), questions that need two documents, questions that need a metadata filter.
- **Include negatives.** Questions the knowledge base cannot answer. The correct behaviour is "not found", and the harness should reward that and punish an invented answer.
- **Include questions phrased the way people actually ask them** ("payments is slow") as well as precise ones, so retrieval is tested on both.
- **Keep reference answers short and factual.** They are for the judge to compare against, not model answers to imitate.
- **Grow it from failures.** Every bug report or bad answer becomes a case.

Thirty to fifty cases is enough to detect meaningful changes on a corpus this size. More is better, but a small set you actually run beats a large one you do not.

## Retrieval metrics: hit@k and MRR

`ops_copilot.evals.retrieval_eval` runs `HybridRetriever.search` directly for each case (no model, no MCP, fast and free) and compares retrieved `doc_id`s against `expected_doc_ids`.

**hit@k.** Did at least one expected document appear in the top k? Averaged over cases. It answers "is the right thing in the prompt at all?", which is the precondition for everything else.

**Mean Reciprocal Rank (MRR).** For each case, `1 / rank` of the first expected document in the result list (0 if absent), averaged. It answers "how high is it?", which matters because of "Lost in the Middle" (Chapter 03): a hit at rank 1 is worth more than a hit at rank 5.

Worked example with five cases, k = 5:

| Case | Rank of first expected doc | hit@5 | Reciprocal rank |
|---|---|---|---|
| kb-001 | 1 | 1 | 1.000 |
| kb-002 | 3 | 1 | 0.333 |
| kb-003 | not in top 5 | 0 | 0.000 |
| kb-004 | 2 | 1 | 0.500 |
| kb-005 | 5 | 1 | 0.200 |
| **mean** | | **hit@5 = 0.80** | **MRR = 0.407** |

Now suppose a chunk-size change moves kb-002 to rank 1 and kb-005 to rank 2, but drops kb-004 out of the top 5. hit@5 stays 0.80; MRR becomes (1 + 1 + 0 + 0 + 0.5) / 5 = 0.50. The two metrics disagree, and that is informative: ranking improved overall but one case regressed, and you should look at kb-004 before deciding.

```python
# ops_copilot/evals/retrieval_eval.py (illustrative)
def evaluate_retrieval(retriever, cases, k=5) -> dict:
    hits, rr = [], []
    for c in cases:
        if not c["expected_doc_ids"]:
            continue                                   # negatives are scored by the judge, not here
        got = [r.chunk.doc_id for r in retriever.search(c["question"], k=k)]
        first = next((i for i, d in enumerate(got, 1) if d in c["expected_doc_ids"]), None)
        hits.append(1 if first else 0)
        rr.append(1 / first if first else 0.0)
    return {"hit@k": sum(hits) / len(hits), "mrr": sum(rr) / len(rr), "k": k, "n": len(hits)}
```

Report hit@k at more than one k (1, 3, 5) to see the shape, not just one number. `ops-copilot eval --compare` runs the same cases through BM25-only, vector-only and hybrid (`retrieval_eval.compare_retrievers`) so the decision to fuse is backed by numbers for *this* corpus and embedding provider — with the hashing provider, for instance, BM25 alone beats hybrid, which is exactly what RRF should do with a noisy second list.

## LLM-as-judge: faithfulness and answer relevance

Retrieval metrics stop at "the right chunk was present". Whether the model used it correctly needs a judge, and the practical judge is another model. `ops_copilot.evals.judge` uses the fast model, following the RAGAS framework's decomposition (*RAGAS: Automated Evaluation of Retrieval Augmented Generation*, Es et al., 2023):

**Faithfulness.** Split the answer into atomic claims. For each, ask: is this claim supported by the retrieved evidence? Score = supported claims / total claims. An answer that is correct but unsupported by the evidence still scores low, deliberately: the system is supposed to answer from the knowledge base, not from the model's memory.

**Answer relevance.** Does the answer address the question that was asked? One approach is to ask the judge to generate questions the answer would be a good response to and measure similarity with the original. A simpler version, fine here, is a direct 1 to 5 rubric score with reasoning.

**Negative handling** (project-specific). For cases with empty `expected_doc_ids`, did the answer say the information was not available? Scored as pass/fail.

```python
# ops_copilot/evals/judge.py (illustrative)
class FaithfulnessJudgement(BaseModel):
    claims: list[str]
    supported: list[bool]
    score: float          # supported / total, computed by the judge and re-checked in code

def judge_faithfulness(llm_fast, question: str, answer: str, evidence: list[dict]) -> FaithfulnessJudgement:
    judged = llm_fast.with_structured_output(FaithfulnessJudgement).invoke([
        ("system", FAITHFULNESS_RUBRIC),
        ("user", f"Question: {question}\n\nEvidence:\n{render(evidence)}\n\nAnswer:\n{answer}"),
    ])
    judged.score = sum(judged.supported) / max(len(judged.claims), 1)   # never trust the model's arithmetic
    return judged
```

Caveats, because LLM judges have known biases (Zheng et al., 2023):

- They prefer longer answers. Normalise by claim count, as above, rather than scoring the whole answer.
- They are lenient on fluent text. Ask for claim-by-claim evidence pointers, not a gut score.
- Judge and judged should not share a prompt or context. The reviewer in the graph and the judge in evals are separate prompts on purpose, so the eval is not just the reviewer agreeing with itself.
- Spot-check. Read ten judgements a week against your own view. If you disagree with more than one or two, fix the rubric.

## Building the eval loop into development

`ops-copilot eval` runs retrieval metrics (free, seconds); `ops-copilot eval --judge` adds the judge over the full pipeline (costs model calls, minutes). Use them at different cadences:

| When | Run | Why |
|---|---|---|
| Every change to `rag/*` | retrieval evals | Fast, free, catches most regressions |
| Every prompt change | retrieval + judge on the affected route | Prompts cannot change retrieval but can change faithfulness |
| Before merging | full `ops-copilot eval` | The number you quote |
| CI | retrieval evals with `OPS_COPILOT_EMBEDDING_PROVIDER=hashing` | No model download, no API key; it checks the pipeline runs and that lexical retrieval has not regressed, not absolute quality |

Keep a `evals/results/` directory (git-ignored or committed, your call) with one JSON per run containing the config (chunk size, overlap, provider, prompt version hash) and the scores. A one-line change to the harness to write that file saves you from re-running baselines.

## Cost and latency tracking

Every model response from `langchain-anthropic` carries `usage_metadata` with input and output token counts. Summing it over `state["messages"]` undercounts: structured-output calls (supervisor, reviewer, the triage report formatter) never land in the message list. A usage callback sees every model call in the run, split by model, which matters because Sonnet and Haiku are priced differently:

```python
from langchain_core.callbacks import get_usage_metadata_callback

with get_usage_metadata_callback() as usage:
    state = await graph.ainvoke(initial_state(question, "knowledge"), config)
usage.usage_metadata  # {"claude-sonnet-5-5": {"input_tokens": ..., ...}, "claude-haiku-5-5": {...}}
```

`ops-copilot eval --judge` does this and reports `tokens_by_model` next to the scores.

Also record wall-clock time per node (LangGraph's stream events carry timestamps) and the number of tool calls. A change that improves faithfulness by a little and doubles cost is a decision, not a win, and you can only make it if both numbers are in front of you.

## What to log in production

Chapter 07 covers tooling; here is the content. For every request, you want to be able to reconstruct:

- **The trace:** which nodes ran, in what order, with timing.
- **Every tool call:** name, arguments, result size, latency, errors.
- **Every model call:** model id, input/output tokens, cache hits if caching is on, latency.
- **The route and the review verdict**, including feedback on `revise`.
- **The evidence set** (chunk ids and scores), so a bad answer can be traced to a retrieval miss or a generation error.
- **Human decisions** at `human_approval`.

This is the same data the eval harness consumes. A good design makes production traces loadable as eval cases.

## A/B-ing chunk sizes

The procedure, because it is the most common tuning job:

1. Fix everything else: embedding provider, RRF `k`, retrieval k, golden set version.
2. For each chunk size in `{200, 400, 800}` (and overlap at a fixed fraction): run `ops-copilot ingest`, then the retrieval evals. Record hit@1, hit@5, MRR.
3. Pick the best one or two by retrieval metrics. Only then run the judge, because generation quality depends on what retrieval delivered.
4. Look at the cases that flipped. A size that wins on average but loses the three identifier-heavy cases may be the wrong choice for an on-call tool, where identifiers are the common case.
5. Record the decision and the numbers in the ADR or a short note.

The same procedure applies to overlap, RRF `k`, retrieval `k`, embedding provider and re-ranker on/off.

## Common pitfalls

- **Evaluating on the questions you wrote the prompt with.** Keep a few cases you never look at while tuning.
- **One run, one number.** Model outputs vary; for judge scores run at least twice and look at the spread before trusting a difference.
- **Letting the judge do arithmetic.** Compute scores in code from the judge's per-claim booleans.
- **No negatives.** A system that always answers scores perfectly on a set with no unanswerable questions.
- **Changing two things at once.** Chunk size and prompt in one run tells you nothing.
- **Trusting hit@k without MRR.** Both, always.

## Exercises

1. **Write five cases.** Add five entries to `golden.json`: one identifier-heavy, one paraphrase, one needing two documents, one needing a `kind` filter, one negative. Run `ops-copilot eval` and confirm they are scored.
2. **Reproduce the worked example.** Run the retrieval evals, then hand-compute hit@5 and MRR for the first five cases from the per-case output and confirm they match the harness.
3. **Judge the judge.** Take ten faithfulness judgements from an eval run and score the same answers yourself, claim by claim. Where you disagree, decide whether the rubric or your reading is wrong, and adjust the rubric prompt.
4. **Cost report.** Extend the eval output to print total input tokens, output tokens and estimated cost per route. Which route is most expensive and why?

## Further reading

- *RAGAS: Automated Evaluation of Retrieval Augmented Generation*, Es et al., 2023 (arxiv.org/abs/2309.15217).
- *Judging LLM-as-a-Judge with MT-Bench and Chatbot Arena*, Zheng et al., 2023 (arxiv.org/abs/2306.05685).
- *Lost in the Middle*, Liu et al., 2023 (arxiv.org/abs/2307.03172), for why MRR matters and not only hit@k.
- Anthropic documentation, guidance on building evals and using Claude as a grader (docs.claude.com).
- LangGraph / LangSmith documentation on tracing and evaluation datasets (langchain-ai.github.io/langgraph and the LangSmith docs linked from there).
- *An Introduction to Information Retrieval*, Manning, Raghavan and Schütze, 2008, chapter 8 (evaluation), for the classical definitions of precision, recall, MRR and friends.
