"""Settings.from_env: provider switching + backwards-compat fallbacks."""

from __future__ import annotations

import pytest

from wati_agent.config import Settings


@pytest.fixture(autouse=True)
def _clear_llm_env(monkeypatch):
    for v in (
        "LLM_PROVIDER",
        "LLM_API_KEY",
        "LLM_BASE_URL",
        "LLM_MODEL",
        "ANTHROPIC_API_KEY",
        "OPENAI_API_KEY",
        "WATI_AGENT_MODEL",
        "WATI_AGENT_LLM",
        "WATI_AGENT_BACKEND",
    ):
        monkeypatch.delenv(v, raising=False)


def test_default_provider_is_anthropic(monkeypatch) -> None:
    s = Settings.from_env()
    assert s.llm_provider == "anthropic"
    assert s.llm_api_key is None
    assert s.llm_model is None


def test_provider_is_lowercased(monkeypatch) -> None:
    monkeypatch.setenv("LLM_PROVIDER", "OpenAI")
    assert Settings.from_env().llm_provider == "openai"


def test_anthropic_api_key_falls_back(monkeypatch) -> None:
    monkeypatch.setenv("LLM_PROVIDER", "anthropic")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-legacy")
    s = Settings.from_env()
    assert s.llm_api_key == "sk-ant-legacy"


def test_openai_api_key_falls_back(monkeypatch) -> None:
    monkeypatch.setenv("LLM_PROVIDER", "openai")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-openai-legacy")
    s = Settings.from_env()
    assert s.llm_api_key == "sk-openai-legacy"


def test_generic_llm_api_key_wins_over_provider_specific(monkeypatch) -> None:
    monkeypatch.setenv("LLM_PROVIDER", "anthropic")
    monkeypatch.setenv("LLM_API_KEY", "sk-generic")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-legacy")
    assert Settings.from_env().llm_api_key == "sk-generic"


def test_legacy_model_var_still_honoured(monkeypatch) -> None:
    monkeypatch.setenv("WATI_AGENT_MODEL", "claude-haiku-4-5")
    assert Settings.from_env().llm_model == "claude-haiku-4-5"


def test_new_model_var_wins(monkeypatch) -> None:
    monkeypatch.setenv("LLM_MODEL", "gpt-4o-mini")
    monkeypatch.setenv("WATI_AGENT_MODEL", "claude-haiku-4-5")
    assert Settings.from_env().llm_model == "gpt-4o-mini"


def test_base_url_only_set_when_present(monkeypatch) -> None:
    assert Settings.from_env().llm_base_url is None
    monkeypatch.setenv("LLM_BASE_URL", "http://localhost:11434/v1")
    assert Settings.from_env().llm_base_url == "http://localhost:11434/v1"
