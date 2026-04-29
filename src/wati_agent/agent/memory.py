"""Per-session conversational memory.

Stores plain (role, content) turns. The planner re-sends the most recent N
turns to the LLM so the user can say things like "send the same template to
the Bandung group too" after their previous instruction.

Kept deliberately simple: no embeddings, no summarisation. For longer sessions
we'd add summarisation; out of scope for V1.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from typing import Iterable


@dataclass
class Turn:
    role: str  # "user" | "assistant"
    content: str


@dataclass
class SessionMemory:
    max_turns: int = 16
    _turns: deque[Turn] = field(default_factory=deque)

    def add_user(self, text: str) -> None:
        self._append("user", text)

    def add_assistant(self, text: str) -> None:
        self._append("assistant", text)

    def _append(self, role: str, content: str) -> None:
        self._turns.append(Turn(role=role, content=content))
        while len(self._turns) > self.max_turns:
            self._turns.popleft()

    def turns(self) -> list[Turn]:
        return list(self._turns)

    def as_messages(self) -> list[dict]:
        """Shape ready for the Anthropic Messages API."""
        return [{"role": t.role, "content": t.content} for t in self._turns]

    def reset(self) -> None:
        self._turns.clear()

    def extend(self, turns: Iterable[Turn]) -> None:
        for t in turns:
            self._append(t.role, t.content)
