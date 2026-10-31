"""Eval entry point used by ``ops-copilot eval``.

Retrieval metrics run fully offline against the on-disk index. The optional judge
stage asks the knowledge agent each golden question and grades the answers; it needs
an API key and takes a few minutes. Token usage and latency are recorded per question
so prompt/model changes can be costed, not just scored. Usage covers every model call
the copilot makes for a question (specialist, report formatter, reviewer), split by
model because Sonnet and Haiku are priced differently; the judge's own call is eval
overhead and is not counted.
"""

from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path

from langchain_core.callbacks import get_usage_metadata_callback
from langchain_core.messages.ai import UsageMetadata, add_usage

from ..rag.ingest import load_retriever
from .retrieval_eval import compare_retrievers, evaluate_retrieval

GOLDEN_PATH = Path(__file__).with_name("golden.json")


def load_golden(path: Path = GOLDEN_PATH) -> list[dict]:
    return json.loads(path.read_text(encoding="utf-8"))


def run_eval(k: int = 5, with_judge: bool = False, compare: bool = False, golden_path: Path = GOLDEN_PATH) -> dict:
    golden = load_golden(golden_path)
    retriever = load_retriever()
    retrieval = evaluate_retrieval(retriever, golden, k)
    report: dict = {
        "n": len(golden),
        "n_positive": retrieval["n"],
        "n_negative": sum(1 for g in golden if not g.get("expected_doc_ids")),
        "per_question": retrieval.pop("per_question"),
        "retrieval": retrieval,
    }
    if compare:
        report["compare"] = compare_retrievers(retriever, golden, k)
    if with_judge:
        report["judge"] = asyncio.run(_run_judge(golden))
    return report


def _tokens(usage_by_model: dict[str, UsageMetadata]) -> dict[str, int]:
    return {
        "input_tokens": sum(u["input_tokens"] for u in usage_by_model.values()),
        "output_tokens": sum(u["output_tokens"] for u in usage_by_model.values()),
    }


async def _run_judge(golden: list[dict]) -> dict:
    from ..agents import build_graph, final_answer, initial_state, thread_config
    from ..agents.reviewer import render_evidence
    from ..llm import get_llm
    from ..mcp_client import load_local_tools
    from .judge import judge_answer

    fast = get_llm("fast")
    graph = build_graph(get_llm("strong"), fast, load_local_tools())
    rows = []
    by_model: dict[str, UsageMetadata] = {}
    for item in golden:
        t0 = time.perf_counter()
        # structured-output calls (reviewer, formatter) never land in state["messages"]; a
        # usage callback sees every model call made inside the run
        with get_usage_metadata_callback() as usage:
            state = await graph.ainvoke(initial_state(item["question"], "knowledge"), thread_config(f"eval-{item['id']}"))
        latency = time.perf_counter() - t0
        for model, u in usage.usage_metadata.items():
            by_model[model] = add_usage(by_model.get(model), u)
        answer = final_answer(state)
        js = judge_answer(fast, item["question"], answer, render_evidence(state, 20_000), item["reference_answer"])
        rows.append(
            {
                "id": item["id"],
                "negative": not item.get("expected_doc_ids"),
                **js.model_dump(),
                "latency_s": round(latency, 2),
                "tool_calls": state.get("tool_calls", 0),
                "revisions": state.get("attempts", 0),
                **_tokens(usage.usage_metadata),
                "answer": answer,
            }
        )
    n = len(rows) or 1
    negatives = [r for r in rows if r["negative"]]
    return {
        "faithfulness_avg": sum(r["faithfulness"] for r in rows) / n,
        "relevance_avg": sum(r["relevance"] for r in rows) / n,
        "reference_agreement_avg": sum(r["reference_agreement"] for r in rows) / n,
        # on negatives, "agreeing with the reference" means the agent correctly said the KB doesn't cover it
        "negative_abstention_rate": (sum(r["reference_agreement"] >= 4 for r in negatives) / len(negatives)) if negatives else None,
        "avg_latency_s": round(sum(r["latency_s"] for r in rows) / n, 2),
        "total_input_tokens": sum(r["input_tokens"] for r in rows),
        "total_output_tokens": sum(r["output_tokens"] for r in rows),
        "tokens_by_model": {m: _tokens({m: u}) for m, u in by_model.items()},
        "per_question": rows,
    }


if __name__ == "__main__":
    print(json.dumps(run_eval(compare=True), indent=2))
