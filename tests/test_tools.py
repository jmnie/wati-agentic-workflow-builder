"""Tool registry + dispatch."""

from __future__ import annotations

import pytest

from wati_agent.agent.tools import all_tools, get_tool, tool_catalog_for_prompt
from wati_agent.wati.mock_client import MockWatiClient


def test_get_tool_unknown() -> None:
    with pytest.raises(KeyError):
        get_tool("teleport_user")


def test_each_tool_has_required_params_marked() -> None:
    for tool in all_tools():
        schema = tool.json_schema()
        for required in schema["required"]:
            assert required in schema["properties"]


def test_tool_catalog_includes_destructive_flag() -> None:
    catalog = tool_catalog_for_prompt()
    assert "send_template_message" in catalog
    assert "[destructive" in catalog


def test_find_contacts_dispatch(mock_client: MockWatiClient) -> None:
    tool = get_tool("find_contacts")
    out = tool.call(mock_client, {"tag": "VIP"})
    assert {c["fullName"] for c in out} == {"Andi Wijaya", "Budi Santoso"}


def test_send_template_dispatch(mock_client: MockWatiClient) -> None:
    tool = get_tool("send_template_message")
    res = tool.call(
        mock_client,
        {
            "whatsapp_number": "6281234567890",
            "template_name": "renewal_reminder",
            "parameters": [{"name": "body_1", "value": "Andi"}],
        },
    )
    assert res["result"] is True


def test_add_contact_dispatch(mock_client: MockWatiClient) -> None:
    tool = get_tool("add_contact")
    res = tool.call(
        mock_client,
        {
            "whatsapp_number": "628999000111",
            "name": "New Contact",
            "custom_params": [{"name": "city", "value": "Jakarta"}],
        },
    )
    assert res["wAid"] == "628999000111"
    assert mock_client.get_contact("628999000111")["fullName"] == "New Contact"


def test_inverse_for_add_tag() -> None:
    tool = get_tool("add_tag")
    inv = tool.inverse({"whatsapp_number": "6281", "tag": "VIP"}, None)
    assert inv is not None
    assert inv.tool == "remove_tag"
    assert inv.args == {"whatsapp_number": "6281", "tag": "VIP"}


def test_send_template_has_no_inverse() -> None:
    assert get_tool("send_template_message").inverse is None
    assert get_tool("send_template_message").destructive is True
