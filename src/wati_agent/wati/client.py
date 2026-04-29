"""Abstract WATI client + factory.

The agent only depends on this interface, so the mock and real backends are
fully swappable. Each method maps to one WATI API endpoint from the assignment
spec; nothing here is LLM- or planner-aware.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from ..config import Settings


class WatiAPIError(Exception):
    """Raised when a WATI API call fails (network, 4xx/5xx, validation)."""

    def __init__(self, message: str, *, status: int | None = None, payload: Any = None) -> None:
        super().__init__(message)
        self.status = status
        self.payload = payload


class WatiClient(ABC):
    # --- Contacts ---------------------------------------------------------

    @abstractmethod
    def get_contacts(
        self,
        *,
        tag: str | None = None,
        attribute_name: str | None = None,
        attribute_value: str | None = None,
        page_size: int = 20,
        page_number: int = 1,
    ) -> list[dict]: ...

    @abstractmethod
    def get_contact(self, whatsapp_number: str) -> dict | None: ...

    @abstractmethod
    def add_contact(self, whatsapp_number: str, name: str, custom_params: list[dict] | None = None) -> dict: ...

    @abstractmethod
    def update_contact_attributes(self, whatsapp_number: str, custom_params: list[dict]) -> dict: ...

    # --- Tags -------------------------------------------------------------

    @abstractmethod
    def add_tag(self, whatsapp_number: str, tag: str) -> dict: ...

    @abstractmethod
    def remove_tag(self, whatsapp_number: str, tag: str) -> dict: ...

    # --- Messages ---------------------------------------------------------

    @abstractmethod
    def send_session_message(self, whatsapp_number: str, message_text: str) -> dict: ...

    @abstractmethod
    def send_template_message(
        self,
        whatsapp_number: str,
        template_name: str,
        broadcast_name: str,
        parameters: list[dict],
    ) -> dict: ...

    # --- Templates --------------------------------------------------------

    @abstractmethod
    def list_templates(self, page_size: int = 20, page_number: int = 1) -> list[dict]: ...

    # --- Broadcasts -------------------------------------------------------

    @abstractmethod
    def send_broadcast_to_segment(
        self,
        template_name: str,
        broadcast_name: str,
        segment_name: str,
    ) -> dict: ...

    # --- Operators & tickets ---------------------------------------------

    @abstractmethod
    def list_operators(self) -> list[dict]: ...

    @abstractmethod
    def assign_operator(self, whatsapp_number: str, email: str) -> dict: ...

    @abstractmethod
    def assign_team(self, whatsapp_number: str, team_name: str) -> dict: ...


def build_client(settings: Settings | None = None) -> WatiClient:
    """Pick a backend based on settings. The agent uses this — never instantiates directly."""
    from ..config import get_settings
    from .mock_client import MockWatiClient
    from .real_client import RealWatiClient

    settings = settings or get_settings()
    if settings.backend == "mock":
        return MockWatiClient.with_seed_data()
    if settings.backend == "real":
        if not settings.wati_tenant_id or not settings.wati_api_token:
            raise RuntimeError(
                "WATI_TENANT_ID and WATI_API_TOKEN must be set when WATI_AGENT_BACKEND=real"
            )
        return RealWatiClient(
            tenant_id=settings.wati_tenant_id,
            token=settings.wati_api_token,
            base_url=settings.wati_base_url,
        )
    raise ValueError(f"Unknown backend: {settings.backend!r}")
