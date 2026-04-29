"""FastAPI app: REST endpoints + static chat UI.

The UI is a single HTML page in /frontend; the server mounts it at the root.
Sessions are kept in-memory by id (so the UI keeps a session_id cookie/header).
"""

from __future__ import annotations

import logging
import uuid
from pathlib import Path
from typing import Literal

from fastapi import Depends, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from .agent import Agent, AgentResponse
from .agent.schemas import AgentTurnKind, Plan, ExecutionReport

log = logging.getLogger(__name__)

FRONTEND_DIR = Path(__file__).resolve().parents[2] / "frontend"


# ---------------------------------------------------------------------------
# Session store (in-memory).
# ---------------------------------------------------------------------------


class SessionStore:
    def __init__(self) -> None:
        self._agents: dict[str, Agent] = {}

    def get_or_create(self, session_id: str | None) -> tuple[str, Agent]:
        if session_id and session_id in self._agents:
            return session_id, self._agents[session_id]
        new_id = session_id or uuid.uuid4().hex
        self._agents[new_id] = Agent()
        return new_id, self._agents[new_id]

    def reset(self, session_id: str) -> None:
        if session_id in self._agents:
            self._agents[session_id].reset()


_sessions = SessionStore()


# ---------------------------------------------------------------------------
# Request / response models
# ---------------------------------------------------------------------------


class ChatRequest(BaseModel):
    message: str
    session_id: str | None = None


class ConfirmRequest(BaseModel):
    session_id: str
    action: Literal["run", "dry_run", "cancel"]


class ChatResponseModel(BaseModel):
    session_id: str
    kind: AgentTurnKind
    text: str
    plan: Plan | None = None
    report: ExecutionReport | None = None
    awaiting_confirmation: bool = False


def _to_response(session_id: str, r: AgentResponse) -> ChatResponseModel:
    return ChatResponseModel(
        session_id=session_id,
        kind=r.kind,
        text=r.text,
        plan=r.plan,
        report=r.report,
        awaiting_confirmation=r.awaiting_confirmation,
    )


# ---------------------------------------------------------------------------
# App
# ---------------------------------------------------------------------------


def create_app() -> FastAPI:
    app = FastAPI(title="WATI Agent", version="0.1.0")

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],  # local dev only
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.post("/api/chat", response_model=ChatResponseModel)
    def chat(req: ChatRequest) -> ChatResponseModel:
        session_id, agent = _sessions.get_or_create(req.session_id)
        return _to_response(session_id, agent.chat(req.message))

    @app.post("/api/confirm", response_model=ChatResponseModel)
    def confirm(req: ConfirmRequest) -> ChatResponseModel:
        if req.session_id not in _sessions._agents:
            raise HTTPException(status_code=404, detail="Unknown session")
        agent = _sessions._agents[req.session_id]
        return _to_response(req.session_id, agent.confirm(req.action))

    @app.post("/api/reset")
    def reset(session_id: str) -> dict:
        _sessions.reset(session_id)
        return {"ok": True}

    @app.get("/api/health")
    def health() -> dict:
        return {"ok": True, "version": "0.1.0"}

    if FRONTEND_DIR.exists():
        app.mount(
            "/static",
            StaticFiles(directory=str(FRONTEND_DIR)),
            name="static",
        )

        @app.get("/")
        def root() -> FileResponse:
            return FileResponse(str(FRONTEND_DIR / "index.html"))

    return app


app = create_app()


def run() -> None:
    """Entry point for ``wati-agent-server`` console script."""
    import uvicorn

    uvicorn.run("wati_agent.server:app", host="127.0.0.1", port=8000, reload=False)


if __name__ == "__main__":
    run()
