"""HTTP client for the real WATI API.

Kept thin on purpose — every public method maps 1:1 to one endpoint from the
assignment's API reference. The mock client mirrors this surface, so swapping
backends is just a config change.
"""

from __future__ import annotations

from typing import Any

import httpx

from .client import WatiAPIError, WatiClient


class RealWatiClient(WatiClient):
    def __init__(self, *, tenant_id: str, token: str, base_url: str, timeout: float = 30.0) -> None:
        self._base = f"{base_url.rstrip('/')}/{tenant_id}"
        self._client = httpx.Client(
            base_url=self._base,
            headers={"Authorization": f"Bearer {token}"},
            timeout=timeout,
        )

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> "RealWatiClient":
        return self

    def __exit__(self, *_: Any) -> None:
        self.close()

    def _request(self, method: str, path: str, **kwargs: Any) -> Any:
        try:
            r = self._client.request(method, path, **kwargs)
        except httpx.HTTPError as e:
            raise WatiAPIError(f"Network error calling {method} {path}: {e}") from e
        if r.status_code >= 400:
            raise WatiAPIError(
                f"WATI API error {r.status_code} on {method} {path}",
                status=r.status_code,
                payload=_safe_json(r),
            )
        return _safe_json(r)

    # --- Contacts ---------------------------------------------------------

    def get_contacts(
        self,
        *,
        tag: str | None = None,
        attribute_name: str | None = None,
        attribute_value: str | None = None,
        page_size: int = 20,
        page_number: int = 1,
    ) -> list[dict]:
        params: dict[str, Any] = {"pageSize": page_size, "pageNumber": page_number}
        if tag is not None:
            params["tag"] = tag
        # WATI doesn't support arbitrary attribute filtering server-side — we filter client-side.
        data = self._request("GET", "/api/v1/getContacts", params=params)
        contacts = data.get("contact_list") or data.get("contacts") or data.get("items") or []
        if attribute_name is not None:
            contacts = [
                c
                for c in contacts
                if any(
                    p.get("name") == attribute_name and p.get("value") == attribute_value
                    for p in c.get("customParams", [])
                )
            ]
        return contacts

    def get_contact(self, whatsapp_number: str) -> dict | None:
        try:
            return self._request("GET", f"/api/v1/getContactInfo/{whatsapp_number}")
        except WatiAPIError as e:
            if e.status == 404:
                return None
            raise

    def add_contact(self, whatsapp_number: str, name: str, custom_params: list[dict] | None = None) -> dict:
        body: dict[str, Any] = {"name": name}
        if custom_params:
            body["customParams"] = custom_params
        return self._request("POST", f"/api/v1/addContact/{whatsapp_number}", json=body)

    def update_contact_attributes(self, whatsapp_number: str, custom_params: list[dict]) -> dict:
        return self._request(
            "POST",
            f"/api/v1/updateContactAttributes/{whatsapp_number}",
            json={"customParams": custom_params},
        )

    # --- Tags -------------------------------------------------------------

    def add_tag(self, whatsapp_number: str, tag: str) -> dict:
        return self._request("POST", f"/api/v1/addTag/{whatsapp_number}", json={"tag": tag})

    def remove_tag(self, whatsapp_number: str, tag: str) -> dict:
        return self._request("DELETE", f"/api/v1/removeTag/{whatsapp_number}/{tag}")

    # --- Messages ---------------------------------------------------------

    def send_session_message(self, whatsapp_number: str, message_text: str) -> dict:
        return self._request(
            "POST",
            f"/api/v1/sendSessionMessage/{whatsapp_number}",
            json={"messageText": message_text},
        )

    def send_template_message(
        self,
        whatsapp_number: str,
        template_name: str,
        broadcast_name: str,
        parameters: list[dict],
    ) -> dict:
        return self._request(
            "POST",
            f"/api/v2/sendTemplateMessage/{whatsapp_number}",
            json={
                "template_name": template_name,
                "broadcast_name": broadcast_name,
                "parameters": parameters,
            },
        )

    # --- Templates --------------------------------------------------------

    def list_templates(self, page_size: int = 20, page_number: int = 1) -> list[dict]:
        data = self._request(
            "GET",
            "/api/v1/getMessageTemplates",
            params={"pageSize": page_size, "pageNumber": page_number},
        )
        return data.get("messageTemplates") or data.get("items") or []

    # --- Broadcasts -------------------------------------------------------

    def send_broadcast_to_segment(
        self,
        template_name: str,
        broadcast_name: str,
        segment_name: str,
    ) -> dict:
        return self._request(
            "POST",
            "/api/v1/sendBroadcastToSegment",
            json={
                "template_name": template_name,
                "broadcast_name": broadcast_name,
                "segmentName": segment_name,
            },
        )

    # --- Operators / tickets ---------------------------------------------

    def list_operators(self) -> list[dict]:
        data = self._request("GET", "/api/v1/getOperators")
        if isinstance(data, list):
            return data
        if isinstance(data, dict):
            return data.get("operators") or data.get("items") or []
        return []

    def assign_operator(self, whatsapp_number: str, email: str) -> dict:
        return self._request(
            "POST", f"/api/v1/assignOperator/{whatsapp_number}", json={"email": email}
        )

    def assign_team(self, whatsapp_number: str, team_name: str) -> dict:
        return self._request(
            "POST",
            "/api/v1/tickets/assign",
            json={"whatsappNumber": whatsapp_number, "teamName": team_name},
        )


def _safe_json(r: httpx.Response) -> Any:
    try:
        return r.json()
    except ValueError:
        return {"raw": r.text}
