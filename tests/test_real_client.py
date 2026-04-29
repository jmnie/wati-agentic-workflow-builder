"""Real WATI client response-shape handling without network calls."""

from __future__ import annotations

from typing import Any

from wati_agent.wati.real_client import RealWatiClient


def _client_returning(payload: Any) -> RealWatiClient:
    client = object.__new__(RealWatiClient)
    client._request = lambda _method, _path, **_kwargs: payload  # type: ignore[attr-defined]
    return client


def test_list_operators_accepts_operators_dict() -> None:
    client = _client_returning({"operators": [{"email": "agent@company.com"}]})
    assert client.list_operators() == [{"email": "agent@company.com"}]


def test_list_operators_accepts_items_dict() -> None:
    client = _client_returning({"items": [{"email": "agent@company.com"}]})
    assert client.list_operators() == [{"email": "agent@company.com"}]


def test_list_operators_accepts_plain_list() -> None:
    client = _client_returning([{"email": "agent@company.com"}])
    assert client.list_operators() == [{"email": "agent@company.com"}]
