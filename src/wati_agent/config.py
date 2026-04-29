"""Runtime configuration loaded from environment variables.

LLM provider config is generic — pick a provider, give it an API key, and
optionally override the base URL + model. Backwards-compatible env names from
the older single-Anthropic build are still honoured as fallbacks.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

from dotenv import load_dotenv

load_dotenv()


def _first(*names: str, default: str | None = None) -> str | None:
    """Return the first env var that's set (and non-empty), else default."""
    for n in names:
        v = os.getenv(n)
        if v:
            return v
    return default


@dataclass(frozen=True)
class Settings:
    # --- LLM ---------------------------------------------------------------
    llm: str  # "real" | "fake"
    llm_provider: str  # "anthropic" | "openai"
    llm_api_key: str | None
    llm_base_url: str | None
    llm_model: str | None

    # --- WATI --------------------------------------------------------------
    backend: str  # "mock" | "real"
    wati_tenant_id: str | None
    wati_api_token: str | None
    wati_base_url: str

    @classmethod
    def from_env(cls) -> "Settings":
        provider = (os.getenv("LLM_PROVIDER") or "anthropic").lower().strip()

        # Per-provider API key fallbacks for backwards compatibility.
        provider_specific_key = {
            "anthropic": os.getenv("ANTHROPIC_API_KEY"),
            "openai": os.getenv("OPENAI_API_KEY"),
        }.get(provider)
        api_key = _first("LLM_API_KEY") or provider_specific_key

        # Older builds used WATI_AGENT_MODEL — still accept it.
        model = _first("LLM_MODEL", "WATI_AGENT_MODEL")

        return cls(
            llm=os.getenv("WATI_AGENT_LLM", "real"),
            llm_provider=provider,
            llm_api_key=api_key,
            llm_base_url=_first("LLM_BASE_URL"),
            llm_model=model,
            backend=os.getenv("WATI_AGENT_BACKEND", "mock"),
            wati_tenant_id=os.getenv("WATI_TENANT_ID"),
            wati_api_token=os.getenv("WATI_API_TOKEN"),
            wati_base_url=os.getenv("WATI_BASE_URL", "https://live-mt-server.wati.io"),
        )


def get_settings() -> Settings:
    return Settings.from_env()
