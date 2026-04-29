"""Planner: rule-based fallback + LLM planner shape."""

from __future__ import annotations

from wati_agent.agent.memory import SessionMemory
from wati_agent.agent.planner import FakePlanner, build_planner


def test_plan_template_to_tag() -> None:
    plan = FakePlanner().plan(
        "Find all contacts tagged 'VIP' and send them the 'renewal_reminder' template with their name.",
        SessionMemory(),
    )
    assert plan.steps[0].tool == "find_contacts"
    assert plan.steps[0].args["tag"] == "VIP"
    assert plan.steps[1].tool == "send_template_message"
    assert plan.steps[1].for_each == 1
    assert plan.steps[1].args["template_name"] == "renewal_reminder"
    assert plan.requires_confirmation


def test_plan_escalate() -> None:
    plan = FakePlanner().plan("Escalate 6281234567890 to the support team", SessionMemory())
    assert {s.tool for s in plan.steps} == {"assign_team", "add_tag"}
    assert plan.requires_confirmation


def test_plan_broadcast_by_attribute() -> None:
    plan = FakePlanner().plan(
        "Send a broadcast with the 'flash_sale' template to all contacts who have city = 'Jakarta'.",
        SessionMemory(),
    )
    assert plan.steps[0].tool == "find_contacts"
    assert plan.steps[0].args == {"attribute_name": "city", "attribute_value": "Jakarta"}
    assert plan.steps[1].tool == "send_broadcast"
    assert plan.steps[1].args["segment_name"] == "city=Jakarta"
    assert plan.steps[1].for_each is None


def test_plan_list_templates_no_confirm() -> None:
    plan = FakePlanner().plan("list templates", SessionMemory())
    assert plan.steps[0].tool == "list_templates"
    assert plan.requires_confirmation is False


def test_plan_unknown_intent_asks_for_clarification() -> None:
    plan = FakePlanner().plan("blah blah do something", SessionMemory())
    assert plan.steps == []
    assert plan.needs_clarification


def test_plan_chitchat_no_steps() -> None:
    plan = FakePlanner().plan("hi there", SessionMemory())
    assert plan.steps == []
    assert plan.needs_clarification == []


def test_build_planner_falls_back_without_api_key() -> None:
    p = build_planner(
        llm="real", provider="anthropic", api_key=None, base_url=None, model=None
    )
    assert isinstance(p, FakePlanner)


def test_build_planner_fake_mode() -> None:
    p = build_planner(
        llm="fake", provider="openai", api_key="sk-x", base_url=None, model="gpt-4o-mini"
    )
    assert isinstance(p, FakePlanner)


def test_build_planner_unknown_provider_falls_back() -> None:
    p = build_planner(
        llm="real", provider="not-a-provider", api_key="x", base_url=None, model=None
    )
    assert isinstance(p, FakePlanner)
