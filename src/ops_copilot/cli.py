"""Command-line interface.

    ops-copilot ingest
    ops-copilot ask "How do I rotate the payments DB credentials?"
    ops-copilot triage "PagerDuty: payments-api p99 latency > 2s since 02:10"
    ops-copilot release-notes --version 2.14.0
    ops-copilot eval [--judge]
    ops-copilot serve
    ops-copilot mcp knowledge|ops
"""

from __future__ import annotations

import json
import uuid

import typer
from rich.console import Console
from rich.markdown import Markdown
from rich.panel import Panel
from rich.table import Table

from .config import get_settings

app = typer.Typer(help="Ops Copilot — multi-agent RAG + MCP assistant for on-call engineers.", no_args_is_help=True)
console = Console()


def _print_review(state: dict) -> None:
    review = state.get("review") or {}
    if not review:
        return
    t = Table(title="Reviewer", show_header=True, header_style="bold")
    for k in ("verdict", "faithfulness", "citations_ok", "completeness"):
        t.add_column(k)
    t.add_row(*(str(review.get(k, "")) for k in ("verdict", "faithfulness", "citations_ok", "completeness")))
    console.print(t)
    if review.get("feedback"):
        console.print(f"[dim]{review['feedback']}[/dim]")
    if review.get("note"):
        console.print(f"[yellow]{review['note']}[/yellow]")


def _run(task: str, route: str | None, tool_mode: str, show_evidence: bool, auto_approve: bool | None) -> None:
    from langgraph.types import Command

    from .agents import final_answer, initial_state, thread_config
    from .runtime import get_graph

    try:
        graph = get_graph(tool_mode)  # type: ignore[arg-type]
    except RuntimeError as e:  # e.g. missing ANTHROPIC_API_KEY — one clear line, not a traceback
        console.print(f"[red]error:[/red] {e}")
        raise typer.Exit(code=1) from None
    cfg = thread_config(str(uuid.uuid4()))
    with console.status("[bold]agents working…"):
        state = graph.invoke(initial_state(task, route), cfg)  # type: ignore[arg-type]

    if state.get("__interrupt__"):
        payload = state["__interrupt__"][0].value
        console.print(Panel(json.dumps(payload["action"], indent=2), title=payload["question"], border_style="yellow"))
        approved = auto_approve if auto_approve is not None else typer.confirm("Approve?", default=False)
        comment = "" if auto_approve is not None else typer.prompt("Comment (optional)", default="", show_default=False)
        with console.status("[bold]resuming…"):
            state = graph.invoke(Command(resume={"approved": approved, "comment": comment}), cfg)

    console.print(Panel(Markdown(final_answer(state)), title=f"route: {state.get('route')} — {state.get('route_reason', '')}"))
    _print_review(state)
    if show_evidence:
        for ev in state.get("evidence", []):
            console.print(Panel(ev["content"][:1500], title=f"{ev['tool']}({ev['args']})", border_style="dim"))
    console.print(f"[dim]tool calls: {state.get('tool_calls', 0)} · revisions: {state.get('attempts', 0)}[/dim]")


@app.command()
def ingest() -> None:
    """Build the search index from data/knowledge_base."""
    from .rag import ingest as _ingest

    with console.status("[bold]embedding knowledge base…"):
        res = _ingest()
    console.print(f"[green]✓[/green] {res.documents} documents → {res.chunks} chunks ({res.embedding_provider}) at {res.index_dir}")


@app.command()
def ask(
    question: str,
    tools: str = typer.Option("mcp", help="mcp (subprocess servers) or local (in-process)"),
    evidence: bool = typer.Option(False, "--evidence", help="Print raw tool results"),
) -> None:
    """Grounded Q&A over runbooks / ADRs / postmortems (supervisor decides the route)."""
    _run(question, None, tools, evidence, None)


@app.command()
def triage(
    alert: str,
    tools: str = typer.Option("mcp"),
    evidence: bool = typer.Option(False, "--evidence"),
    yes: bool = typer.Option(False, "--yes", help="Auto-approve any drafted ticket (non-interactive)"),
    no: bool = typer.Option(False, "--no", help="Auto-reject any drafted ticket (non-interactive)"),
) -> None:
    """Investigate an alert / symptom and produce a triage report."""
    auto = True if yes else (False if no else None)
    _run(alert, "triage", tools, evidence, auto)


@app.command("release-notes")
def release_notes(
    version: str = typer.Option(..., "--version", "-v", help="Fix version, e.g. 2.14.0"),
    tools: str = typer.Option("mcp"),
    evidence: bool = typer.Option(False, "--evidence"),
) -> None:
    """Draft release notes for a fix version from tickets + deployments."""
    _run(f"Write the release notes for Navigator version {version}.", "release_notes", tools, evidence, None)


@app.command()
def eval(
    judge: bool = typer.Option(False, "--judge", help="Also run LLM-as-judge on generated answers (needs API key)"),
    compare: bool = typer.Option(False, "--compare", help="Compare bm25-only vs vector-only vs hybrid"),
    k: int = typer.Option(5, help="k for hit@k"),
    output: str | None = typer.Option(None, help="Write JSON report to this path"),
) -> None:
    """Retrieval metrics (hit@k, MRR) on the golden set; optionally faithfulness via judge."""
    from .evals.run import run_eval

    report = run_eval(k=k, with_judge=judge, compare=compare)
    t = Table(
        title=f"Retrieval eval — {report['n_positive']} questions (+{report['n_negative']} negatives)",
        header_style="bold",
    )
    t.add_column("metric")
    t.add_column("hybrid")
    if compare:
        t.add_column("bm25 only")
        t.add_column("vector only")
    for name in ("hit@1", f"hit@{k}", "mrr"):
        row = [name, f"{report['retrieval'][name]:.3f}"]
        if compare:
            row += [f"{report['compare']['bm25'][name]:.3f}", f"{report['compare']['vector'][name]:.3f}"]
        t.add_row(*row)
    console.print(t)
    if judge:
        j = report["judge"]
        console.print(
            f"faithfulness {j['faithfulness_avg']:.2f}/5 · relevance {j['relevance_avg']:.2f}/5 · "
            f"reference agreement {j['reference_agreement_avg']:.2f}/5"
        )
        if j["negative_abstention_rate"] is not None:
            console.print(f"negatives answered correctly (abstained): {j['negative_abstention_rate']:.0%}")
        console.print(
            f"[dim]avg latency {j['avg_latency_s']}s · tokens in/out "
            f"{j['total_input_tokens']}/{j['total_output_tokens']}[/dim]"
        )
    misses = [r for r in report["per_question"] if not r["hit"]]
    if misses:
        console.print("[yellow]Missed:[/yellow]")
        for m in misses:
            console.print(f"  {m['id']}: {m['question'][:70]} → got {m['top_doc_ids'][:3]}")
    if output:
        from pathlib import Path

        Path(output).write_text(json.dumps(report, indent=2))
        console.print(f"[dim]report written to {output}[/dim]")


@app.command()
def serve(host: str | None = None, port: int | None = None, reload: bool = False) -> None:
    """Run the FastAPI service."""
    import uvicorn

    s = get_settings()
    uvicorn.run("ops_copilot.api.main:app", host=host or s.api_host, port=port or s.api_port, reload=reload)


@app.command()
def mcp(server: str = typer.Argument(..., help="knowledge | ops")) -> None:
    """Run one MCP server on stdio (plug it into Claude Desktop / Claude Code)."""
    if server == "knowledge":
        from .mcp_servers.knowledge_server import main
    elif server == "ops":
        from .mcp_servers.ops_server import main
    else:
        raise typer.BadParameter("server must be 'knowledge' or 'ops'")
    main()


@app.command("mcp-config")
def mcp_config() -> None:
    """Print a Claude Desktop / Claude Code mcpServers config block."""
    from .mcp_client import mcp_server_config

    cfg = {}
    for name, c in mcp_server_config().items():
        env = {k: v for k, v in c["env"].items() if k.startswith("OPS_COPILOT_") or k == "VOYAGE_API_KEY"}
        cfg[name] = {"command": c["command"], "args": c["args"], **({"env": env} if env else {})}
    console.print_json(json.dumps({"mcpServers": cfg}))


if __name__ == "__main__":
    app()
