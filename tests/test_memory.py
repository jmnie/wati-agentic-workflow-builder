"""Session memory."""

from __future__ import annotations

from wati_agent.agent.memory import SessionMemory


def test_round_trip() -> None:
    m = SessionMemory()
    m.add_user("Hi")
    m.add_assistant("Hello")
    msgs = m.as_messages()
    assert msgs == [
        {"role": "user", "content": "Hi"},
        {"role": "assistant", "content": "Hello"},
    ]


def test_max_turns_truncation() -> None:
    m = SessionMemory(max_turns=4)
    for i in range(10):
        m.add_user(f"u{i}")
        m.add_assistant(f"a{i}")
    turns = m.turns()
    assert len(turns) == 4
    assert turns[0].content == "u8"
    assert turns[-1].content == "a9"


def test_reset_clears_state() -> None:
    m = SessionMemory()
    m.add_user("x")
    m.reset()
    assert m.turns() == []
