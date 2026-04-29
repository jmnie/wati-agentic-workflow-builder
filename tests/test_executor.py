"""Executor: dry-run, iteration, rollback, error paths."""

from __future__ import annotations

from wati_agent.agent.executor import Executor, _resolve_placeholders
from wati_agent.agent.schemas import Plan, Step, StepStatus
from wati_agent.wati.mock_client import MockWatiClient


def _build_send_to_vip_plan() -> Plan:
    return Plan(
        summary="send renewal_reminder to VIPs",
        steps=[
            Step(id=1, tool="find_contacts", args={"tag": "VIP"}, description="find VIPs"),
            Step(
                id=2,
                tool="send_template_message",
                args={
                    "whatsapp_number": "$item.wAid",
                    "template_name": "renewal_reminder",
                    "broadcast_name": "renewal_q2",
                    "parameters": [{"name": "body_1", "value": "$item.fullName"}],
                },
                description="send template",
                destructive=True,
                for_each=1,
            ),
        ],
    )


def test_placeholder_resolution_field_lookup() -> None:
    item = {"wAid": "6281", "fullName": "Andi"}
    args = {
        "whatsapp_number": "$item.wAid",
        "parameters": [{"name": "body_1", "value": "$item.fullName"}],
    }
    out = _resolve_placeholders(args, item=item)
    assert out["whatsapp_number"] == "6281"
    assert out["parameters"][0]["value"] == "Andi"


def test_placeholder_resolution_full_item() -> None:
    item = {"wAid": "6281"}
    out = _resolve_placeholders("$item", item=item)
    assert out == item


def test_dry_run_makes_no_calls(mock_client: MockWatiClient) -> None:
    plan = _build_send_to_vip_plan()
    report = Executor(mock_client).execute(plan, dry_run=True)
    assert report.success is True
    assert report.dry_run is True
    assert mock_client.sent_messages == []  # nothing sent


def test_iteration_runs_per_item(mock_client: MockWatiClient) -> None:
    plan = _build_send_to_vip_plan()
    report = Executor(mock_client).execute(plan)
    assert report.success
    sent_to = {m["wAid"] for m in mock_client.sent_messages}
    assert sent_to == {"6281234567890", "6285511112222"}


def test_for_each_with_non_list_fails_gracefully(mock_client: MockWatiClient) -> None:
    plan = Plan(
        summary="bad iteration",
        steps=[
            Step(id=1, tool="get_contact", args={"whatsapp_number": "6281234567890"}, description="get one"),
            Step(
                id=2,
                tool="add_tag",
                args={"whatsapp_number": "$item.wAid", "tag": "lead"},
                description="iterate over a single dict — illegal",
                for_each=1,
                destructive=True,
            ),
        ],
    )
    report = Executor(mock_client).execute(plan)
    assert not report.success
    assert any("not a list" in (s.error or "") for s in report.steps)


def test_rollback_undoes_invertible_steps(mock_client: MockWatiClient) -> None:
    plan = Plan(
        summary="tag then fail",
        steps=[
            Step(id=1, tool="add_tag", args={"whatsapp_number": "6287733334444", "tag": "promo"}, description="tag", destructive=True),
            Step(id=2, tool="add_tag", args={"whatsapp_number": "6287733334444", "tag": "vipnext"}, description="tag2", destructive=True),
            Step(id=3, tool="assign_team", args={"whatsapp_number": "6287733334444", "team_name": "GhostTeam"}, description="will fail", destructive=True),
        ],
    )
    report = Executor(mock_client).execute(plan)
    assert not report.success
    assert set(report.rolled_back) == {1, 2}

    contact = mock_client.get_contact("6287733334444")
    assert "promo" not in contact["tags"]
    assert "vipnext" not in contact["tags"]


def test_rollback_skips_irreversible_steps(mock_client: MockWatiClient) -> None:
    """Sending a message has no inverse — rollback should report none for it."""
    plan = Plan(
        summary="send then fail",
        steps=[
            Step(
                id=1,
                tool="send_session_message",
                args={"whatsapp_number": "6281234567890", "message_text": "Hi"},
                description="send",
                destructive=True,
            ),
            Step(
                id=2,
                tool="assign_team",
                args={"whatsapp_number": "6281234567890", "team_name": "GhostTeam"},
                description="fail",
                destructive=True,
            ),
        ],
    )
    report = Executor(mock_client).execute(plan)
    assert not report.success
    assert report.rolled_back == []  # message can't be unsent
    assert len(mock_client.sent_messages) == 1


def test_step_skipped_after_failure(mock_client: MockWatiClient) -> None:
    plan = Plan(
        summary="fail then skip",
        steps=[
            Step(id=1, tool="assign_team", args={"whatsapp_number": "6281234567890", "team_name": "Ghost"}, description="x", destructive=True),
            Step(id=2, tool="add_tag", args={"whatsapp_number": "6281234567890", "tag": "post"}, description="never reached", destructive=True),
        ],
    )
    report = Executor(mock_client).execute(plan)
    statuses = {s.step_id: s.status for s in report.steps}
    assert statuses[1] == StepStatus.FAILED
    assert statuses[2] == StepStatus.SKIPPED


def test_simulated_api_failure_triggers_rollback(mock_client: MockWatiClient) -> None:
    mock_client.fail_next_call = "send_template_message"
    plan = Plan(
        summary="tag then send (will fail)",
        steps=[
            Step(id=1, tool="add_tag", args={"whatsapp_number": "6281234567890", "tag": "campaign"}, description="tag", destructive=True),
            Step(
                id=2,
                tool="send_template_message",
                args={
                    "whatsapp_number": "6281234567890",
                    "template_name": "renewal_reminder",
                    "broadcast_name": "x",
                    "parameters": [],
                },
                description="send",
                destructive=True,
            ),
        ],
    )
    report = Executor(mock_client).execute(plan)
    assert not report.success
    assert 1 in report.rolled_back
    assert "campaign" not in mock_client.get_contact("6281234567890")["tags"]


def test_unknown_tool_returns_failed_report(mock_client: MockWatiClient) -> None:
    plan = Plan(
        summary="bad tool",
        steps=[Step(id=1, tool="teleport_user", args={}, description="bad")],
    )
    report = Executor(mock_client).execute(plan)
    assert not report.success
    assert report.steps[0].status == StepStatus.FAILED
    assert "Unknown tool" in report.steps[0].error


def test_missing_required_argument_returns_failed_report(mock_client: MockWatiClient) -> None:
    plan = Plan(
        summary="missing argument",
        steps=[
            Step(
                id=1,
                tool="send_session_message",
                args={"whatsapp_number": "6281234567890"},
                description="send",
                destructive=True,
            )
        ],
    )
    report = Executor(mock_client).execute(plan)
    assert not report.success
    assert report.steps[0].status == StepStatus.FAILED
    assert "missing required argument" in report.steps[0].error


def test_item_placeholder_without_iteration_returns_failed_report(
    mock_client: MockWatiClient,
) -> None:
    plan = Plan(
        summary="bad placeholder",
        steps=[
            Step(
                id=1,
                tool="send_template_message",
                args={
                    "whatsapp_number": "$item.wAid",
                    "template_name": "renewal_reminder",
                    "parameters": [],
                },
                description="send",
                destructive=True,
            )
        ],
    )
    report = Executor(mock_client).execute(plan)
    assert not report.success
    assert report.steps[0].status == StepStatus.FAILED
    assert "not using for_each" in report.steps[0].error
