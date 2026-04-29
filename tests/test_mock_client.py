"""Mock WATI client behaviour."""

from __future__ import annotations

import pytest

from wati_agent.wati.client import WatiAPIError
from wati_agent.wati.mock_client import MockWatiClient


def test_seed_data_is_loaded(mock_client: MockWatiClient) -> None:
    contacts = mock_client.get_contacts()
    assert len(contacts) >= 4
    assert any(c["fullName"] == "Andi Wijaya" for c in contacts)


def test_filter_by_tag(mock_client: MockWatiClient) -> None:
    vips = mock_client.get_contacts(tag="VIP")
    assert {c["fullName"] for c in vips} == {"Andi Wijaya", "Budi Santoso"}


def test_filter_by_attribute(mock_client: MockWatiClient) -> None:
    jakartans = mock_client.get_contacts(attribute_name="city", attribute_value="Jakarta")
    names = {c["fullName"] for c in jakartans}
    assert names == {"Andi Wijaya", "Sari Putri"}


def test_get_contact_normalises_phone(mock_client: MockWatiClient) -> None:
    assert mock_client.get_contact("+62-812-3456-7890")["fullName"] == "Andi Wijaya"
    assert mock_client.get_contact("nonexistent") is None


def test_add_contact_dedup(mock_client: MockWatiClient) -> None:
    with pytest.raises(WatiAPIError):
        mock_client.add_contact("6281234567890", name="Duplicate")


def test_update_attributes_merges(mock_client: MockWatiClient) -> None:
    updated = mock_client.update_contact_attributes(
        "6281234567890", [{"name": "city", "value": "Bali"}, {"name": "lang", "value": "id"}]
    )
    params = {p["name"]: p["value"] for p in updated["customParams"]}
    assert params["city"] == "Bali"
    assert params["lang"] == "id"
    assert params["plan"] == "pro"  # untouched


def test_add_then_remove_tag(mock_client: MockWatiClient) -> None:
    mock_client.add_tag("6287733334444", "VIP")
    contact = mock_client.get_contact("6287733334444")
    assert "VIP" in contact["tags"]

    mock_client.remove_tag("6287733334444", "VIP")
    contact = mock_client.get_contact("6287733334444")
    assert "VIP" not in contact["tags"]


def test_send_template_message_unknown_template(mock_client: MockWatiClient) -> None:
    with pytest.raises(WatiAPIError) as ei:
        mock_client.send_template_message(
            "6281234567890",
            template_name="ghost_template",
            broadcast_name="x",
            parameters=[],
        )
    assert ei.value.status == 404


def test_send_template_message_audit_log(mock_client: MockWatiClient) -> None:
    res = mock_client.send_template_message(
        "6281234567890",
        template_name="renewal_reminder",
        broadcast_name="renewal_q2",
        parameters=[{"name": "body_1", "value": "Andi"}],
    )
    assert res["result"] is True
    assert any(m["template"] == "renewal_reminder" for m in mock_client.sent_messages)


def test_assign_team_unknown(mock_client: MockWatiClient) -> None:
    with pytest.raises(WatiAPIError):
        mock_client.assign_team("6281234567890", "Marketing")


def test_assign_team_ok(mock_client: MockWatiClient) -> None:
    res = mock_client.assign_team("6281234567890", "Support")
    assert res["team"] == "Support"


def test_simulated_failure(mock_client: MockWatiClient) -> None:
    mock_client.fail_next_call = "add_tag"
    with pytest.raises(WatiAPIError):
        mock_client.add_tag("6281234567890", "lead")
    # fail_next_call resets after firing
    res = mock_client.add_tag("6281234567890", "lead")
    assert res["result"] is True
