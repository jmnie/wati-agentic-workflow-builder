"""Shared pytest fixtures."""

from __future__ import annotations

import pytest

from wati_agent.agent.core import Agent
from wati_agent.agent.planner import FakePlanner
from wati_agent.config import Settings
from wati_agent.wati.mock_client import MockWatiClient


@pytest.fixture
def mock_client() -> MockWatiClient:
    return MockWatiClient.with_seed_data()


@pytest.fixture
def settings() -> Settings:
    return Settings(
        llm="fake",
        llm_provider="anthropic",
        llm_api_key=None,
        llm_base_url=None,
        llm_model=None,
        backend="mock",
        wati_tenant_id=None,
        wati_api_token=None,
        wati_base_url="https://example.test",
    )


@pytest.fixture
def agent(mock_client: MockWatiClient, settings: Settings) -> Agent:
    return Agent(planner=FakePlanner(), client=mock_client, settings=settings)
