"""End-to-end agent behaviour: confirmation flow, dry-run, cancel."""

from __future__ import annotations

from wati_agent.agent.core import Agent
from wati_agent.agent.schemas import AgentTurnKind


def test_chitchat(agent: Agent) -> None:
    resp = agent.chat("hi")
    assert resp.kind == AgentTurnKind.CHITCHAT


def test_clarification_for_unknown_intent(agent: Agent) -> None:
    resp = agent.chat("teleport to mars")
    assert resp.kind == AgentTurnKind.CLARIFICATION
    assert resp.plan and resp.plan.needs_clarification


def test_safe_query_runs_without_confirmation(agent: Agent) -> None:
    resp = agent.chat("list templates")
    assert resp.kind == AgentTurnKind.EXECUTION
    assert resp.report and resp.report.success
    assert isinstance(resp.report.steps[0].output, list)


def test_list_templates_displays_actual_names_in_report(agent: Agent) -> None:
    """Real template names must appear in the user-facing text and in memory.

    The LLM relies on the assistant's prior memory turns to know what's
    available — and the user relies on it to know what they can ask for next.
    """
    resp = agent.chat("list templates")
    assert "renewal_reminder" in resp.text
    assert "flash_sale" in resp.text
    # The same enriched text must end up in conversation memory.
    last_assistant = next(
        m for m in reversed(agent.memory.as_messages()) if m["role"] == "assistant"
    )
    assert "renewal_reminder" in last_assistant["content"]


def test_list_operators_displays_names(agent: Agent) -> None:
    resp = agent.chat("list operators")
    assert "Alice" in resp.text or "alice" in resp.text.lower()
    assert "Bob" in resp.text or "bob" in resp.text.lower()


def test_destructive_plan_requires_confirmation(agent: Agent) -> None:
    resp = agent.chat("escalate 6281234567890 to support")
    assert resp.kind == AgentTurnKind.PLAN_PREVIEW
    assert resp.awaiting_confirmation
    assert agent.pending_plan is not None


def test_yes_executes_pending(agent: Agent) -> None:
    agent.chat("escalate 6281234567890 to support")
    resp = agent.chat("yes")
    assert resp.kind == AgentTurnKind.EXECUTION
    assert resp.report and resp.report.success
    assert agent.pending_plan is None
    contact = agent.client.get_contact("6281234567890")
    assert "escalated" in contact["tags"]


def test_no_cancels_pending(agent: Agent) -> None:
    agent.chat("escalate 6281234567890 to support")
    resp = agent.chat("no")
    assert resp.kind == AgentTurnKind.CHITCHAT
    assert agent.pending_plan is None


def test_dry_run_keeps_pending_and_makes_no_changes(agent: Agent) -> None:
    before = list(agent.client.get_contact("6281234567890")["tags"])
    agent.chat("escalate 6281234567890 to support")
    resp = agent.chat("dry run")
    assert resp.report and resp.report.dry_run
    assert agent.pending_plan is not None  # still awaiting real confirmation
    after = list(agent.client.get_contact("6281234567890")["tags"])
    assert before == after  # unchanged


def test_dry_run_without_pending_plan_does_not_replan(agent: Agent) -> None:
    resp = agent.chat("dry run")
    assert resp.kind == AgentTurnKind.CHITCHAT
    assert "no plan awaiting confirmation" in resp.text.lower()
    assert agent.pending_plan is None


def test_new_instruction_replaces_pending(agent: Agent) -> None:
    agent.chat("escalate 6281234567890 to support")
    assert agent.pending_plan is not None
    resp = agent.chat("list templates")
    assert resp.kind == AgentTurnKind.EXECUTION
    # pending was cleared by the new turn
    assert agent.pending_plan is None


def test_confirm_run_executes(agent: Agent) -> None:
    agent.chat("escalate 6281234567890 to support")
    resp = agent.confirm("run")
    assert resp.kind == AgentTurnKind.EXECUTION
    assert resp.report and resp.report.success


def test_confirm_cancel(agent: Agent) -> None:
    agent.chat("escalate 6281234567890 to support")
    resp = agent.confirm("cancel")
    assert resp.kind == AgentTurnKind.CHITCHAT
    assert agent.pending_plan is None


def test_full_template_to_tag_flow(agent: Agent) -> None:
    resp = agent.chat(
        "find all contacts tagged 'VIP' and send them the renewal_reminder template with their name"
    )
    assert resp.kind == AgentTurnKind.PLAN_PREVIEW
    resp = agent.chat("yes")
    assert resp.kind == AgentTurnKind.EXECUTION
    assert resp.report and resp.report.success
    sent_to = {m["wAid"] for m in agent.client.sent_messages}
    assert sent_to == {"6281234567890", "6285511112222"}


def test_reset_clears_memory_and_pending(agent: Agent) -> None:
    agent.chat("escalate 6281234567890 to support")
    agent.reset()
    assert agent.pending_plan is None
    assert agent.memory.as_messages() == []
