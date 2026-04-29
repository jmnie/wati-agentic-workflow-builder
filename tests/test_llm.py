"""LLM backend layer.

We don't hit a real provider here — instead we drive the planner with a stub
backend, and we check the factory's behaviour for the env-driven cases.
"""

from __future__ import annotations

from typing import Any

from wati_agent.agent.llm import (
    DEFAULT_MODELS,
    LLMBackend,
    build_backend,
)
from wati_agent.agent.memory import SessionMemory
from wati_agent.agent.planner import LLMPlanner
from wati_agent.agent.schemas import Plan


class _StubBackend:
    """Backend that returns a fixed dict — lets us test the planner's parsing path."""

    name = "stub"

    def __init__(self, payload: dict[str, Any] | None) -> None:
        self.payload = payload
        self.calls: list[dict[str, Any]] = []

    def submit_plan(self, *, system_prompt, messages, plan_schema):
        self.calls.append({"system_prompt": system_prompt, "messages": messages, "schema": plan_schema})
        return self.payload


def test_default_models_cover_all_supported_providers() -> None:
    assert "anthropic" in DEFAULT_MODELS
    assert "openai" in DEFAULT_MODELS


def test_build_backend_returns_none_without_api_key() -> None:
    assert build_backend(provider="anthropic", api_key=None, base_url=None, model=None) is None


def test_build_backend_unknown_provider_returns_none() -> None:
    assert build_backend(provider="ghost", api_key="x", base_url=None, model=None) is None


def test_planner_uses_backend_to_produce_plan() -> None:
    payload = {
        "summary": "list templates",
        "steps": [
            {
                "id": 1,
                "tool": "list_templates",
                "args": {},
                "description": "fetch",
                "destructive": False,
                "for_each": None,
            }
        ],
        "needs_clarification": [],
        "requires_confirmation": False,
        "notes": [],
    }
    backend: LLMBackend = _StubBackend(payload)  # type: ignore[assignment]
    planner = LLMPlanner(backend)
    plan = planner.plan("list the templates", SessionMemory())
    assert isinstance(plan, Plan)
    assert plan.steps[0].tool == "list_templates"


def test_planner_rejects_spurious_template_listing_for_ambiguous_intent() -> None:
    payload = {
        "summary": "list templates",
        "steps": [
            {
                "id": 1,
                "tool": "list_templates",
                "args": {},
                "description": "fetch",
                "destructive": False,
                "for_each": None,
            }
        ],
        "needs_clarification": [],
        "requires_confirmation": False,
        "notes": [],
    }
    backend: LLMBackend = _StubBackend(payload)  # type: ignore[assignment]
    plan = LLMPlanner(backend).plan("broadcast", SessionMemory())
    assert plan.steps == []
    assert plan.needs_clarification


def test_guard_clarification_quotes_user_input() -> None:
    """Two ambiguous turns shouldn't produce identical clarifications."""
    payload = {
        "summary": "list templates",
        "steps": [
            {"id": 1, "tool": "list_templates", "args": {}, "description": "fetch"},
        ],
        "needs_clarification": [],
        "requires_confirmation": False,
        "notes": [],
    }
    planner = LLMPlanner(_StubBackend(payload))  # type: ignore[arg-type]
    p1 = planner.plan("VIP template", SessionMemory())
    p2 = planner.plan("send a template", SessionMemory())
    assert "VIP template" in " ".join(p1.needs_clarification)
    assert "send a template" in " ".join(p2.needs_clarification)
    assert p1.needs_clarification != p2.needs_clarification


def test_planner_handles_backend_returning_none() -> None:
    backend: LLMBackend = _StubBackend(None)  # type: ignore[assignment]
    plan = LLMPlanner(backend).plan("???", SessionMemory())
    assert plan.steps == []
    assert plan.needs_clarification


def test_planner_handles_invalid_payload() -> None:
    # `summary` is required; an empty object should fail validation.
    backend: LLMBackend = _StubBackend({})  # type: ignore[assignment]
    plan = LLMPlanner(backend).plan("x", SessionMemory())
    assert plan.steps == []
    assert plan.needs_clarification


def test_planner_handles_backend_exception() -> None:
    class _Boom:
        name = "boom"

        def submit_plan(self, **_: object):
            raise RuntimeError("network down")

    plan = LLMPlanner(_Boom()).plan("hi", SessionMemory())  # type: ignore[arg-type]
    assert plan.steps == []
    assert any("unavailable" in q for q in plan.needs_clarification)
