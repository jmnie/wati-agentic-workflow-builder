"""Pydantic schemas shared across the agent."""

from __future__ import annotations

from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


class Step(BaseModel):
    """One unit of work in a Plan.

    A step is a single tool invocation, optionally repeated once per element of
    a previous step's list output.

    Argument substitution rules:
      * "$item"            — when ``for_each`` is set, the current iteration item.
      * "$item.<field>"    — a field of the current iteration item.
      * "$user.<field>"    — a value the user supplied during clarification.

    Substitution happens at execution time, never at planning time.
    """

    id: int = Field(..., description="Step ID, 1-indexed and contiguous.")
    tool: str = Field(..., description="Name of the registered tool to invoke.")
    args: dict[str, Any] = Field(default_factory=dict, description="Arguments for the tool.")
    description: str = Field(..., description="Human-friendly explanation, shown in plan preview.")
    destructive: bool = Field(False, description="True for outbound messages, broadcasts, etc.")
    for_each: int | None = Field(
        None,
        description="If set, run this step once per item in step <for_each>.output (which must be a list).",
    )


class Plan(BaseModel):
    summary: str = Field(..., description="One-sentence plain-English summary shown to the user.")
    steps: list[Step] = Field(default_factory=list)
    needs_clarification: list[str] = Field(
        default_factory=list,
        description="Questions to ask the user before this plan can run. If non-empty, no execution.",
    )
    requires_confirmation: bool = Field(
        True,
        description="Whether to require explicit user confirmation before running. True for any destructive plan.",
    )
    notes: list[str] = Field(
        default_factory=list,
        description="Caveats/warnings the user should see (e.g., 'sending a message cannot be undone').",
    )

    @property
    def has_destructive_step(self) -> bool:
        return any(s.destructive for s in self.steps)


class StepStatus(str, Enum):
    PENDING = "pending"
    OK = "ok"
    FAILED = "failed"
    SKIPPED = "skipped"
    ROLLED_BACK = "rolled_back"


class StepResult(BaseModel):
    step_id: int
    tool: str
    args_resolved: list[dict[str, Any]] = Field(
        default_factory=list,
        description="The actual arg dicts used at runtime (one per iteration if for_each).",
    )
    status: StepStatus
    output: Any = None
    error: str | None = None


class ExecutionReport(BaseModel):
    success: bool
    dry_run: bool
    steps: list[StepResult]
    rolled_back: list[int] = Field(default_factory=list)
    message: str = ""


class ConfirmationStatus(str, Enum):
    NONE = "none"  # plan not produced yet (clarifications outstanding)
    PENDING = "pending"  # plan produced, awaiting user confirmation
    EXECUTED = "executed"  # plan ran (success or failure)
    CANCELLED = "cancelled"


class AgentTurnKind(str, Enum):
    CLARIFICATION = "clarification"  # plan needs more info from user
    PLAN_PREVIEW = "plan_preview"  # plan ready, awaiting confirm/run
    EXECUTION = "execution"  # plan was executed
    CHITCHAT = "chitchat"  # no actionable intent (e.g. "thanks!")
