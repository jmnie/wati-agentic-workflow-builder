"""LLM backend abstraction.

Two production backends — Anthropic native and OpenAI-compatible (which covers
OpenAI, DeepSeek, Moonshot, Groq, Together, OpenRouter, Ollama, vLLM, …) — share
a single ``LLMBackend`` protocol. The planner only depends on the protocol, so
swapping providers is a config change, not a code change.

Both backends use forced tool/function calling so the model is required to emit
arguments that match the provided JSON Schema. That gives us a parsed dict
back, not a chat completion we have to parse with regex.

Why this design rather than a single OpenAI-only client pointed at every
provider?  Anthropic isn't OpenAI-compatible and supports prompt caching out of
the box; we lose both if we route Claude through a translation layer.
"""

from __future__ import annotations

import json
import logging
from typing import Any, Protocol

log = logging.getLogger(__name__)

# Sensible per-provider defaults for the model name.
DEFAULT_MODELS: dict[str, str] = {
    "anthropic": "claude-sonnet-4-6",
    "openai": "gpt-4o-mini",
}


class LLMBackendError(Exception):
    """Raised when the backend cannot be initialised or a call fails fatally."""


class LLMBackend(Protocol):
    """One method: take the planner's prompt + history, return a parsed dict.

    The returned dict is the arguments to ``submit_plan`` — i.e. a Plan
    JSON-serialised. ``None`` means the model declined to call the tool, which
    the planner treats as "ask the user to rephrase".
    """

    name: str

    def submit_plan(
        self,
        *,
        system_prompt: str,
        messages: list[dict[str, str]],
        plan_schema: dict[str, Any],
    ) -> dict[str, Any] | None: ...


# ---------------------------------------------------------------------------
# Anthropic
# ---------------------------------------------------------------------------


class AnthropicBackend:
    """Native Anthropic Messages API with prompt caching."""

    name = "anthropic"

    def __init__(self, *, api_key: str, model: str, base_url: str | None = None) -> None:
        try:
            from anthropic import Anthropic
        except ImportError as e:  # pragma: no cover
            raise LLMBackendError(
                "anthropic package not installed. `pip install anthropic`."
            ) from e
        kwargs: dict[str, Any] = {"api_key": api_key}
        if base_url:
            kwargs["base_url"] = base_url
        self._client = Anthropic(**kwargs)
        self._model = model

    def submit_plan(
        self,
        *,
        system_prompt: str,
        messages: list[dict[str, str]],
        plan_schema: dict[str, Any],
    ) -> dict[str, Any] | None:
        resp = self._client.messages.create(
            model=self._model,
            max_tokens=2048,
            system=[
                {
                    "type": "text",
                    "text": system_prompt,
                    "cache_control": {"type": "ephemeral"},
                }
            ],
            tools=[
                {
                    "name": "submit_plan",
                    "description": "Submit the structured execution plan.",
                    "input_schema": plan_schema,
                }
            ],
            tool_choice={"type": "tool", "name": "submit_plan"},
            messages=messages,
        )
        for block in getattr(resp, "content", []) or []:
            if getattr(block, "type", None) == "tool_use" and getattr(block, "name", None) == "submit_plan":
                return getattr(block, "input", None)
        return None


# ---------------------------------------------------------------------------
# OpenAI-compatible (OpenAI / DeepSeek / Moonshot / Together / Groq / Ollama …)
# ---------------------------------------------------------------------------


class OpenAICompatibleBackend:
    """Any chat-completions endpoint that speaks the OpenAI tool-calling API.

    Tested against OpenAI itself; should work with every provider that exposes
    `/v1/chat/completions` and accepts the standard ``tools`` + ``tool_choice``
    payload. ``base_url`` lets you point this at non-OpenAI hosts.
    """

    name = "openai"

    def __init__(self, *, api_key: str, model: str, base_url: str | None = None) -> None:
        try:
            from openai import OpenAI
        except ImportError as e:  # pragma: no cover
            raise LLMBackendError(
                "openai package not installed. `pip install openai`."
            ) from e
        kwargs: dict[str, Any] = {"api_key": api_key}
        if base_url:
            kwargs["base_url"] = base_url
        self._client = OpenAI(**kwargs)
        self._model = model

    def submit_plan(
        self,
        *,
        system_prompt: str,
        messages: list[dict[str, str]],
        plan_schema: dict[str, Any],
    ) -> dict[str, Any] | None:
        resp = self._client.chat.completions.create(
            model=self._model,
            messages=[{"role": "system", "content": system_prompt}, *messages],
            tools=[
                {
                    "type": "function",
                    "function": {
                        "name": "submit_plan",
                        "description": "Submit the structured execution plan.",
                        "parameters": plan_schema,
                    },
                }
            ],
            tool_choice={"type": "function", "function": {"name": "submit_plan"}},
        )
        choice = resp.choices[0] if resp.choices else None
        if not choice or not choice.message.tool_calls:
            return None
        for call in choice.message.tool_calls:
            if call.function.name == "submit_plan":
                try:
                    return json.loads(call.function.arguments or "{}")
                except json.JSONDecodeError:
                    log.warning("OpenAI tool call arguments were not valid JSON.")
                    return None
        return None


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------


def build_backend(
    *,
    provider: str,
    api_key: str | None,
    base_url: str | None,
    model: str | None,
) -> LLMBackend | None:
    """Pick a backend based on env config. Returns ``None`` to signal fallback.

    Returning ``None`` (rather than raising) lets the caller decide what to do —
    the planner uses this to fall back to ``FakePlanner`` so the agent stays
    usable even when the LLM is misconfigured.
    """
    if not api_key:
        log.warning("No LLM_API_KEY for provider=%s; LLM disabled.", provider)
        return None

    resolved_model = model or DEFAULT_MODELS.get(provider)
    if not resolved_model:
        log.warning("No model configured and no default for provider=%s.", provider)
        return None

    try:
        if provider == "anthropic":
            return AnthropicBackend(api_key=api_key, model=resolved_model, base_url=base_url)
        if provider == "openai":
            return OpenAICompatibleBackend(api_key=api_key, model=resolved_model, base_url=base_url)
    except Exception as e:
        log.warning("Could not initialise %s backend (%s); LLM disabled.", provider, e)
        return None

    log.warning("Unknown LLM provider: %r", provider)
    return None
