# 01 — LLM Fundamentals

*Who this is for: engineers who have used a chat assistant but never called a model from code. Time to complete: about 2 hours.*

You do not need to understand how a transformer is trained to build with one, any more than you need to understand B-tree internals to use Postgres. You do need a working model of a handful of concepts, because they drive every design decision in this repo: what goes in the prompt, how big a chunk should be, which model to use where, and why the reviewer exists at all.

## Tokens

A model does not read characters or words. It reads **tokens**: sub-word pieces from a fixed vocabulary. In English, a token is roughly four characters or three-quarters of a word. Code, identifiers and non-English text tokenise less efficiently; `payments-api-p99-latency` may be six or seven tokens.

Why it matters:

- **You pay per token**, in and out.
- **Context is measured in tokens**, so "how much can I fit" is a token question.
- **Chunk size in RAG is a token budget.** The chunker in `ops_copilot.rag.chunker` uses a "token-ish" size estimate rather than character counts for this reason.

You rarely need an exact count. A ratio of 4 characters per token is a fine estimate for planning; measure with the API's usage fields when it matters.

## Context window

The **context window** is everything the model can see for one call: system prompt, conversation history, tool definitions, tool results, retrieved documents, and the answer it is producing. It is finite. When you exceed it the call fails.

Two consequences shape this project:

1. **You must choose what to include.** RAG exists because you cannot paste the whole knowledge base in. Retrieval is selection under a token budget.
2. **More is not better.** Models attend unevenly across long contexts; information in the middle of a long prompt is recalled worse than information at the start or end (the "Lost in the Middle" result, covered in Chapter 03). Top-5 well-chosen chunks beat top-50 noisy ones.

The agents here also carry state across many tool calls in one run (`CopilotState.messages` grows with every `tool_result`). Chapter 05 covers the guards that stop that from bloating.

## Transformer intuition, without the maths

A transformer predicts the next token given all previous tokens. The mechanism that makes it work is **attention**: for each position, the model computes how much every other position should influence it. Stack many layers of that and the model learns which earlier tokens matter for predicting the next one: the subject of the sentence, the variable name defined thirty lines up, the instruction in the system prompt.

Three practical takeaways:

- **It is autoregressive.** Output is produced one token at a time, each conditioned on everything before. This is why output is slower and more expensive than input, and why streaming is natural.
- **Attention is over the whole context.** Instructions, examples and retrieved text all compete for influence. Putting the right things in the prompt is the main lever you have.
- **It is a pattern completer, not a database.** It will complete a plausible-looking answer whether or not it has the facts. That is the root of hallucination.

If you want the actual paper, it is *Attention Is All You Need* (Vaswani et al., 2017). You can build everything in this repo without reading it.

## Pretraining, instruction tuning, RLHF

A base model is **pretrained** on a very large text corpus to predict the next token. That alone gives you something that continues text, not something that answers questions.

**Instruction tuning** (supervised fine-tuning on prompt/response pairs) teaches it to follow instructions and respond in the assistant format. **Reinforcement learning from human feedback (RLHF)** and related techniques then adjust it towards responses people rate as helpful, honest and harmless.

Why you care: the models you call have been trained to follow system prompts, to use tools when given tool definitions, and to say "I don't know" when told to. These behaviours are learned, not guaranteed. Prompts that work with the grain of that training (clear role, explicit constraints, examples) work better than prompts that fight it.

## Sampling and temperature

At each step the model produces a probability over the next token. **Sampling** picks one. **Temperature** scales that distribution: at 0 you (nearly) always get the most likely token; higher values flatten the distribution and admit less-likely tokens.

Rules of thumb used in this project:

- **Routing and judging (the `fast` role): temperature 0.** The supervisor's job is to emit one of three labels. The reviewer's job is to say `pass` or `revise`. Determinism matters more than variety.
- **Specialists (the `strong` role): low temperature, 0 to 0.3.** Grounded answers and triage reports should not be creative.
- Even at temperature 0 outputs are not bit-for-bit reproducible across calls. Do not build tests that depend on exact strings; test structure and content.

## Why models hallucinate

A hallucination is a fluent, confident statement that is not supported by anything the model actually has. The causes are structural:

- The training objective rewards plausible continuations, not verified ones.
- The model has no access to your private data and no way to check.
- Instruction tuning makes it eager to be helpful, so "I don't know" is under-produced unless you ask for it.
- Long or noisy contexts dilute the signal from the facts that are there.

The defences in this repo, in increasing strength:

1. **Give it the facts.** Retrieval puts the relevant runbook text in the prompt (`ops_copilot.rag.retriever.HybridRetriever`).
2. **Tell it to use only the facts and cite them.** The knowledge agent's prompt requires `[doc_id#chunk]` citations and an explicit "not found in the knowledge base" when evidence is missing.
3. **Check it.** `ops_copilot.agents.reviewer` is a second model call that compares each claim against `CopilotState.evidence` and returns `revise` if something is unsupported.
4. **Measure it.** `ops_copilot.evals.judge` scores faithfulness over the golden set so a prompt change that increases hallucination shows up as a number.

No single layer is sufficient. Together they make hallucination rare and detectable rather than impossible.

## Cost model

You are billed for **input tokens** (everything you send) and **output tokens** (everything the model generates), at different rates; output is several times more expensive per token than input. Exact prices change; check the pricing page on docs.claude.com rather than memorising numbers.

What this means for an agentic system:

- **Input dominates in agent loops.** Every turn of a ReAct loop resends the entire conversation so far, including every earlier tool result. A six-step triage run sends the system prompt and tool definitions six times.
- **Retries multiply cost.** A `revise` from the reviewer re-runs the specialist. The spec caps this at two rounds for exactly this reason.
- **Retrieval k is a cost knob.** Five chunks of 400 tokens is 2,000 input tokens per call; twenty is 8,000.

The eval harness (Chapter 06) tracks token counts per run so you can see this instead of guessing.

## Model tiers: the two roles in this project

Model families come in tiers that trade capability for speed and cost. This project uses exactly two, chosen in `ops_copilot.llm.get_llm(role)`:

| Role | Model | Used by | Why |
|---|---|---|---|
| `"strong"` | Sonnet (`claude-sonnet-5-5`) | `knowledge_agent`, `triage_agent`, `release_agent` | Multi-step tool use, synthesis across many chunks, writing the final artefact. Quality matters most here. |
| `"fast"` | Haiku (`claude-haiku-5-5`) | `supervisor`, `reviewer`, `evals.judge` | Short, structured, high-volume decisions. Latency and cost matter most; the task is narrow enough that a smaller model does it well. |

```python
# ops_copilot/llm.py (illustrative)
from langchain_anthropic import ChatAnthropic
from ops_copilot.config import Settings

def get_llm(role: str, settings: Settings | None = None) -> ChatAnthropic:
    settings = settings or Settings()
    model = settings.strong_model if role == "strong" else settings.fast_model
    return ChatAnthropic(model=model, temperature=0, api_key=settings.anthropic_api_key)
```

The model IDs live in `Settings` and are read from `.env`, so swapping tiers is a config change. The design principle is the useful part: **route by task difficulty, not by habit**. Using the strong model for a three-way classification wastes money and adds latency; using the fast model to synthesise a triage report produces worse reports.

## Structured output

Free text is hard to branch on. If the supervisor replied "I think this is probably a triage question", you would be parsing prose. Instead you ask for a **schema** and the model fills it.

With `langchain-anthropic` this is `with_structured_output(PydanticModel)`, which under the hood uses tool calling so the model's output is validated JSON:

```python
from pydantic import BaseModel
from typing import Literal

class RouteDecision(BaseModel):
    route: Literal["knowledge", "triage", "release_notes"]
    reason: str

router = get_llm("fast").with_structured_output(Route)
decision = router.invoke([("system", SUPERVISOR_PROMPT), ("user", question)])
# decision.route is now a validated enum, not a string you have to parse
```

Every decision point in the graph uses this: the supervisor's `RouteDecision`, the triage agent's `TriageReport`, the reviewer's `pass`/`revise` verdict. Chapter 02 goes deeper.

## Prompt caching (concept)

In an agent loop, most of each request is identical to the previous one: the same system prompt, the same tool definitions, the same earlier turns. **Prompt caching** lets the API reuse the processed prefix of a prompt across calls, charging a reduced rate for cached input tokens.

What to know at this stage:

- It works on a stable prefix. Put the things that do not change (system prompt, tool schemas, few-shot examples) first; put the things that do (the user's question, latest tool result) last.
- The specialists' long system prompts and the MCP tool definitions are the natural cache candidates in this repo.
- It is an optimisation to add once correctness is settled. Chapter 07 covers where it fits.

See the prompt caching page on docs.claude.com for the current mechanics and minimum cacheable sizes.

## Common pitfalls

- **Measuring context in characters.** Code and identifiers tokenise badly; budget in tokens.
- **Treating temperature 0 as deterministic.** It is close, not exact. Test structure, not strings.
- **Using one model for everything.** Either you overpay for routing or you under-serve synthesis.
- **Parsing prose when you could have asked for a schema.** Always prefer structured output at decision points.
- **Stuffing the context.** More retrieved text does not mean better answers; it often means worse ones and higher bills.
- **Assuming "I don't know" is the default.** You have to ask for it explicitly in the prompt.

## Exercises

1. **Token budgeting.** Pick three markdown files under `data/knowledge_base/`. Estimate their token counts using the 4-chars-per-token rule, then compare with what `ops_copilot.rag.chunker` produces when you run `ops-copilot ingest` with logging on. How far off was the estimate for files with lots of identifiers?
2. **Role swap.** Temporarily point the `"fast"` role at the Sonnet model ID in `.env` and run `ops-copilot ask` three times. Note latency. Then point `"strong"` at Haiku and compare the quality of the answer on a question that needs two documents. Write one paragraph on what each swap cost or gained. Restore the defaults.
3. **Force a hallucination, then catch it.** Ask `ops-copilot ask` something the knowledge base cannot answer (a service that does not exist). Observe whether the knowledge agent says "not found" or invents something, and what the reviewer does in each case. Then read the knowledge agent's system prompt and identify the line that is supposed to prevent invention.
4. **Structured output by hand.** Write a ten-line script that uses `get_llm("fast").with_structured_output(...)` with a small Pydantic model of your choice (for example, `Severity` with a `Literal["sev1","sev2","sev3"]` field) and classifies three alert strings. Try removing the `Literal` constraint and see what the model does.

## Further reading

- *Attention Is All You Need*, Vaswani et al., 2017 (arxiv.org/abs/1706.03762). The transformer paper. Skim Section 3 for the attention mechanism.
- *Training language models to follow instructions with human feedback*, Ouyang et al., 2022 (arxiv.org/abs/2203.02155). The InstructGPT paper; the clearest description of instruction tuning plus RLHF.
- Anthropic documentation, "Models" and "Pricing" pages (docs.claude.com). The authoritative source for current tiers, context sizes and costs.
- Anthropic documentation, "Prompt caching" (docs.claude.com).
- Anthropic documentation, "Structured outputs" / tool use pages (docs.claude.com).
