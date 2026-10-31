"""FastAPI service.

    GET  /               chat page (static; the API calls below do the work)
    POST /chat           {"message": "...", "thread_id": "..."}      → supervisor routes
    POST /triage         {"alert": "..."}                            → may return pending_approval
    POST /approve        {"thread_id": "...", "approved": true}      → resumes an interrupted run
    POST /release-notes  {"version": "2.14.0"}
    GET  /health         public; 503 when the MCP tool servers stop answering

Each request is a LangGraph *thread*; the checkpointer stores its state so an
interrupted triage can be resumed by a later ``/approve`` call.

When ``OPS_COPILOT_API_TOKEN`` is set, every POST needs ``Authorization: Bearer <token>``.
Set it whenever anyone but you can reach the server: each request spends Anthropic credit.
"""

from __future__ import annotations

import logging
import secrets
import uuid
from contextlib import asynccontextmanager
from functools import lru_cache
from pathlib import Path
from typing import Annotated, Literal

from fastapi import Depends, FastAPI, HTTPException, Response
from fastapi.responses import HTMLResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, Field

from ..agents import final_answer, initial_state, thread_config
from ..config import get_settings
from ..runtime import open_copilot

log = logging.getLogger("uvicorn.error")
STATIC_DIR = Path(__file__).with_name("static")


@asynccontextmanager
async def lifespan(app: FastAPI):
    if get_settings().api_token is None:
        log.warning("OPS_COPILOT_API_TOKEN is not set: anyone who can reach this server can use the API")
    async with open_copilot("mcp") as copilot:
        app.state.copilot = copilot
        app.state.graph = copilot.graph
        yield


app = FastAPI(title="Ops Copilot", version="0.1.0", lifespan=lifespan)
_bearer = HTTPBearer(auto_error=False)


async def require_token(credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)]) -> None:
    token = get_settings().api_token
    if token is None:  # no token configured: open, for local use
        return
    given = credentials.credentials.encode() if credentials else b""
    if not secrets.compare_digest(given, token.get_secret_value().encode()):
        raise HTTPException(status_code=401, detail="Missing or wrong access token", headers={"WWW-Authenticate": "Bearer"})


protected = [Depends(require_token)]


class ChatRequest(BaseModel):
    message: str
    thread_id: str | None = None
    route: Literal["knowledge", "triage", "release_notes"] | None = None


class TriageRequest(BaseModel):
    alert: str
    thread_id: str | None = None


class ReleaseNotesRequest(BaseModel):
    version: str


class ApproveRequest(BaseModel):
    thread_id: str
    approved: bool
    comment: str = ""


class CopilotResponse(BaseModel):
    thread_id: str
    route: str | None = None
    answer: str
    review: dict | None = None
    report: dict | None = Field(default=None, description="Typed specialist output, e.g. TriageReport")
    evidence_count: int = 0
    tool_calls: int = 0
    revisions: int = 0
    pending_approval: dict | None = Field(default=None, description="Set when a human must approve a side effect")


def _to_response(thread_id: str, state: dict) -> CopilotResponse:
    pending = None
    if state.get("__interrupt__"):
        pending = state["__interrupt__"][0].value
    return CopilotResponse(
        thread_id=thread_id,
        route=state.get("route"),
        answer=final_answer(state),
        review=state.get("review"),
        report=state.get("report"),
        evidence_count=len(state.get("evidence", [])),
        tool_calls=state.get("tool_calls", 0),
        revisions=state.get("attempts", 0),
        pending_approval=pending,
    )


async def _invoke(task: str, route, thread_id: str | None) -> CopilotResponse:
    graph = app.state.graph
    if thread_id and (await graph.aget_state(thread_config(thread_id))).next:
        # a new turn would silently abandon the drafted ticket the human has not answered yet
        raise HTTPException(status_code=409, detail="Thread is awaiting approval; call /approve first")
    thread_id = thread_id or str(uuid.uuid4())
    state = await graph.ainvoke(initial_state(task, route), thread_config(thread_id))
    return _to_response(thread_id, state)


@lru_cache
def _chat_page() -> str:
    return (STATIC_DIR / "index.html").read_text(encoding="utf-8")


@app.get("/", response_class=HTMLResponse, include_in_schema=False)
async def chat_page() -> str:
    return _chat_page()


@app.get("/health")
async def health(response: Response) -> dict:
    s = get_settings()
    copilot = getattr(app.state, "copilot", None)
    tools_ok = await copilot.tools_healthy() if copilot else None
    if tools_ok is False:
        response.status_code = 503  # lets the host restart a service whose tool servers died
    return {
        "status": "degraded" if tools_ok is False else "ok",
        "tools_ok": tools_ok,
        "model_strong": s.model_strong,
        "model_fast": s.model_fast,
        "index_present": s.bm25_path.exists(),
        "auth_required": s.api_token is not None,
    }


@app.post("/chat", response_model=CopilotResponse, dependencies=protected)
async def chat(req: ChatRequest) -> CopilotResponse:
    return await _invoke(req.message, req.route, req.thread_id)


@app.post("/triage", response_model=CopilotResponse, dependencies=protected)
async def triage(req: TriageRequest) -> CopilotResponse:
    return await _invoke(req.alert, "triage", req.thread_id)


@app.post("/release-notes", response_model=CopilotResponse, dependencies=protected)
async def release_notes(req: ReleaseNotesRequest) -> CopilotResponse:
    return await _invoke(f"Write the release notes for Navigator version {req.version}.", "release_notes", None)


@app.post("/approve", response_model=CopilotResponse, dependencies=protected)
async def approve(req: ApproveRequest) -> CopilotResponse:
    from langgraph.types import Command

    graph = app.state.graph
    snapshot = await graph.aget_state(thread_config(req.thread_id))
    if not snapshot.next:
        raise HTTPException(status_code=409, detail="Nothing pending on this thread")
    state = await graph.ainvoke(
        Command(resume={"approved": req.approved, "comment": req.comment}), thread_config(req.thread_id)
    )
    return _to_response(req.thread_id, state)
