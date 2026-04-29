"""HTTP layer smoke tests via FastAPI's TestClient."""

from __future__ import annotations

import os

import pytest
from fastapi.testclient import TestClient


@pytest.fixture(autouse=True)
def _force_fake_llm(monkeypatch):
    monkeypatch.setenv("WATI_AGENT_LLM", "fake")
    monkeypatch.setenv("WATI_AGENT_BACKEND", "mock")


@pytest.fixture
def client() -> TestClient:
    # Import lazily so env vars are applied first.
    from wati_agent.server import create_app

    return TestClient(create_app())


def test_health(client: TestClient) -> None:
    r = client.get("/api/health")
    assert r.status_code == 200
    assert r.json()["ok"] is True


def test_chat_creates_session(client: TestClient) -> None:
    r = client.post("/api/chat", json={"message": "list templates"})
    assert r.status_code == 200
    data = r.json()
    assert data["kind"] == "execution"
    assert data["session_id"]


def test_chat_then_confirm(client: TestClient) -> None:
    r1 = client.post("/api/chat", json={"message": "escalate 6281234567890 to support"})
    sid = r1.json()["session_id"]
    assert r1.json()["awaiting_confirmation"] is True

    r2 = client.post("/api/confirm", json={"session_id": sid, "action": "run"})
    assert r2.status_code == 200
    assert r2.json()["report"]["success"] is True


def test_confirm_unknown_session(client: TestClient) -> None:
    r = client.post("/api/confirm", json={"session_id": "nope", "action": "run"})
    assert r.status_code == 404
