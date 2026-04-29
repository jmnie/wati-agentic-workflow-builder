"""In-memory mock of the WATI API.

The mock is intentionally realistic: it stores contacts/tags/templates/operators
in dicts, returns the same shape of response the real API would, and supports
the small operations the planner orchestrates. This lets the entire agent run
end-to-end with no network and no API key.
"""

from __future__ import annotations

import copy
import threading
import time
import uuid
from typing import Any

from .client import WatiAPIError, WatiClient


def _normalize_number(number: str) -> str:
    """Strip + and whitespace — WATI keys contacts by digits-only WhatsApp number."""
    return "".join(ch for ch in number if ch.isdigit())


def _params_to_dict(custom_params: list[dict] | None) -> dict[str, str]:
    return {p["name"]: p["value"] for p in (custom_params or []) if "name" in p and "value" in p}


class MockWatiClient(WatiClient):
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._contacts: dict[str, dict] = {}
        self._templates: dict[str, dict] = {}
        self._operators: list[dict] = []
        self._teams: set[str] = set()
        self._sent_messages: list[dict] = []  # audit log; tests inspect this
        self._broadcasts: list[dict] = []
        # Toggle in tests to simulate transient API failures.
        self.fail_next_call: str | None = None

    # ------------------------------------------------------------------
    # Seed data
    # ------------------------------------------------------------------
    @classmethod
    def with_seed_data(cls) -> "MockWatiClient":
        c = cls()
        c._add_contact_internal(
            "6281234567890",
            name="Andi Wijaya",
            custom_params=[{"name": "city", "value": "Jakarta"}, {"name": "plan", "value": "pro"}],
            tags=["VIP"],
        )
        c._add_contact_internal(
            "6281298765432",
            name="Sari Putri",
            custom_params=[{"name": "city", "value": "Jakarta"}, {"name": "plan", "value": "free"}],
            tags=[],
        )
        c._add_contact_internal(
            "6285511112222",
            name="Budi Santoso",
            custom_params=[{"name": "city", "value": "Bandung"}, {"name": "plan", "value": "pro"}],
            tags=["VIP"],
        )
        c._add_contact_internal(
            "6287733334444",
            name="Lina Hartono",
            custom_params=[{"name": "city", "value": "Surabaya"}],
            tags=["lead"],
        )
        c._templates = {
            "renewal_reminder": {
                "name": "renewal_reminder",
                "language": "en",
                "category": "MARKETING",
                "parameters": ["body_1"],  # customer name
                "body": "Hi {{1}}, your subscription is up for renewal. Tap to renew.",
            },
            "flash_sale": {
                "name": "flash_sale",
                "language": "en",
                "category": "MARKETING",
                "parameters": ["body_1"],
                "body": "{{1}}, flash sale today only — 30% off!",
            },
            "welcome": {
                "name": "welcome",
                "language": "en",
                "category": "UTILITY",
                "parameters": ["body_1"],
                "body": "Welcome {{1}}! Reply with HELP if you need anything.",
            },
        }
        c._operators = [
            {"email": "agent.alice@company.com", "name": "Alice", "team": "Sales"},
            {"email": "agent.bob@company.com", "name": "Bob", "team": "Support"},
        ]
        c._teams = {"Sales", "Support", "Billing"}
        return c

    def _add_contact_internal(
        self,
        number: str,
        *,
        name: str,
        custom_params: list[dict] | None = None,
        tags: list[str] | None = None,
    ) -> dict:
        n = _normalize_number(number)
        contact = {
            "id": str(uuid.uuid4()),
            "wAid": n,
            "phone": "+" + n,
            "fullName": name,
            "customParams": list(custom_params or []),
            "tags": list(tags or []),
            "created": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        }
        self._contacts[n] = contact
        return contact

    # ------------------------------------------------------------------
    # Contacts
    # ------------------------------------------------------------------
    def get_contacts(
        self,
        *,
        tag: str | None = None,
        attribute_name: str | None = None,
        attribute_value: str | None = None,
        page_size: int = 20,
        page_number: int = 1,
    ) -> list[dict]:
        self._maybe_fail("get_contacts")
        with self._lock:
            results: list[dict] = []
            for c in self._contacts.values():
                if tag is not None and tag not in c["tags"]:
                    continue
                if attribute_name is not None:
                    params = _params_to_dict(c["customParams"])
                    if params.get(attribute_name) != attribute_value:
                        continue
                results.append(copy.deepcopy(c))
            start = (page_number - 1) * page_size
            return results[start : start + page_size]

    def get_contact(self, whatsapp_number: str) -> dict | None:
        self._maybe_fail("get_contact")
        with self._lock:
            c = self._contacts.get(_normalize_number(whatsapp_number))
            return copy.deepcopy(c) if c else None

    def add_contact(self, whatsapp_number: str, name: str, custom_params: list[dict] | None = None) -> dict:
        self._maybe_fail("add_contact")
        with self._lock:
            n = _normalize_number(whatsapp_number)
            if n in self._contacts:
                raise WatiAPIError(f"Contact {n} already exists", status=409)
            return copy.deepcopy(self._add_contact_internal(n, name=name, custom_params=custom_params))

    def update_contact_attributes(self, whatsapp_number: str, custom_params: list[dict]) -> dict:
        self._maybe_fail("update_contact_attributes")
        with self._lock:
            c = self._contacts.get(_normalize_number(whatsapp_number))
            if not c:
                raise WatiAPIError(f"Contact {whatsapp_number} not found", status=404)
            current = _params_to_dict(c["customParams"])
            for p in custom_params:
                current[p["name"]] = p["value"]
            c["customParams"] = [{"name": k, "value": v} for k, v in current.items()]
            return copy.deepcopy(c)

    # ------------------------------------------------------------------
    # Tags
    # ------------------------------------------------------------------
    def add_tag(self, whatsapp_number: str, tag: str) -> dict:
        self._maybe_fail("add_tag")
        with self._lock:
            c = self._require_contact(whatsapp_number)
            if tag not in c["tags"]:
                c["tags"].append(tag)
            return {"result": True, "wAid": c["wAid"], "tag": tag}

    def remove_tag(self, whatsapp_number: str, tag: str) -> dict:
        self._maybe_fail("remove_tag")
        with self._lock:
            c = self._require_contact(whatsapp_number)
            if tag in c["tags"]:
                c["tags"].remove(tag)
            return {"result": True, "wAid": c["wAid"], "tag": tag}

    # ------------------------------------------------------------------
    # Messages
    # ------------------------------------------------------------------
    def send_session_message(self, whatsapp_number: str, message_text: str) -> dict:
        self._maybe_fail("send_session_message")
        with self._lock:
            c = self._require_contact(whatsapp_number)
            entry = {
                "id": str(uuid.uuid4()),
                "type": "session",
                "wAid": c["wAid"],
                "messageText": message_text,
                "ts": time.time(),
            }
            self._sent_messages.append(entry)
            return {"result": True, "messageId": entry["id"]}

    def send_template_message(
        self,
        whatsapp_number: str,
        template_name: str,
        broadcast_name: str,
        parameters: list[dict],
    ) -> dict:
        self._maybe_fail("send_template_message")
        with self._lock:
            c = self._require_contact(whatsapp_number)
            if template_name not in self._templates:
                raise WatiAPIError(f"Template {template_name!r} not found", status=404)
            entry = {
                "id": str(uuid.uuid4()),
                "type": "template",
                "wAid": c["wAid"],
                "template": template_name,
                "broadcast": broadcast_name,
                "parameters": list(parameters),
                "ts": time.time(),
            }
            self._sent_messages.append(entry)
            return {"result": True, "messageId": entry["id"]}

    # ------------------------------------------------------------------
    # Templates
    # ------------------------------------------------------------------
    def list_templates(self, page_size: int = 20, page_number: int = 1) -> list[dict]:
        self._maybe_fail("list_templates")
        with self._lock:
            items = list(self._templates.values())
            start = (page_number - 1) * page_size
            return copy.deepcopy(items[start : start + page_size])

    # ------------------------------------------------------------------
    # Broadcasts
    # ------------------------------------------------------------------
    def send_broadcast_to_segment(
        self,
        template_name: str,
        broadcast_name: str,
        segment_name: str,
    ) -> dict:
        self._maybe_fail("send_broadcast_to_segment")
        with self._lock:
            if template_name not in self._templates:
                raise WatiAPIError(f"Template {template_name!r} not found", status=404)
            entry = {
                "id": str(uuid.uuid4()),
                "template": template_name,
                "broadcast": broadcast_name,
                "segment": segment_name,
                "ts": time.time(),
            }
            self._broadcasts.append(entry)
            return {"result": True, "broadcastId": entry["id"], "segment": segment_name}

    # ------------------------------------------------------------------
    # Operators / tickets
    # ------------------------------------------------------------------
    def list_operators(self) -> list[dict]:
        self._maybe_fail("list_operators")
        with self._lock:
            return copy.deepcopy(self._operators)

    def assign_operator(self, whatsapp_number: str, email: str) -> dict:
        self._maybe_fail("assign_operator")
        with self._lock:
            self._require_contact(whatsapp_number)
            if not any(op["email"] == email for op in self._operators):
                raise WatiAPIError(f"Operator {email!r} not found", status=404)
            return {"result": True, "wAid": _normalize_number(whatsapp_number), "operator": email}

    def assign_team(self, whatsapp_number: str, team_name: str) -> dict:
        self._maybe_fail("assign_team")
        with self._lock:
            self._require_contact(whatsapp_number)
            if team_name not in self._teams:
                raise WatiAPIError(f"Team {team_name!r} not found", status=404)
            return {"result": True, "wAid": _normalize_number(whatsapp_number), "team": team_name}

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------
    def _require_contact(self, whatsapp_number: str) -> dict:
        n = _normalize_number(whatsapp_number)
        c = self._contacts.get(n)
        if not c:
            raise WatiAPIError(f"Contact {whatsapp_number} not found", status=404)
        return c

    def _maybe_fail(self, operation: str) -> None:
        if self.fail_next_call == operation:
            self.fail_next_call = None
            raise WatiAPIError(f"Simulated failure on {operation}", status=500)

    # Test-friendly inspection helpers (not part of the WatiClient interface).
    @property
    def sent_messages(self) -> list[dict]:
        return list(self._sent_messages)

    @property
    def broadcasts(self) -> list[dict]:
        return list(self._broadcasts)
