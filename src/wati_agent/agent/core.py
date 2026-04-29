"""Agent orchestrator.

Owns the per-session state machine:
  user input → plan → (clarify | preview & confirm | execute) → report.

The agent is a small loop, not a heavy framework. It deliberately avoids
"chain of thought" gymnastics: planning is one LLM call, execution is
deterministic Python.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Iterable

from ..config import Settings, get_settings
from ..wati.client import WatiClient, build_client
from .executor import Executor
from .memory import SessionMemory
from .planner import Planner, build_planner
from .schemas import (
    AgentTurnKind,
    ConfirmationStatus,
    ExecutionReport,
    Plan,
    Step,
)

log = logging.getLogger(__name__)


@dataclass
class AgentResponse:
    kind: AgentTurnKind
    text: str
    plan: Plan | None = None
    report: ExecutionReport | None = None
    awaiting_confirmation: bool = False


_AFFIRMATIVE = {"yes", "y", "run", "execute", "go", "confirm", "ok", "okay", "do it", "ship it"}
_NEGATIVE = {"no", "n", "cancel", "stop", "abort", "nope"}
_DRY_RUN = {"dry", "dry run", "preview", "dryrun", "dry-run"}


class Agent:
    """High-level façade. Construct once per session."""

    def __init__(
        self,
        *,
        planner: Planner | None = None,
        client: WatiClient | None = None,
        settings: Settings | None = None,
    ) -> None:
        self.settings = settings or get_settings()
        self.client = client or build_client(self.settings)
        self.planner = planner or build_planner(
            llm=self.settings.llm,
            provider=self.settings.llm_provider,
            api_key=self.settings.llm_api_key,
            base_url=self.settings.llm_base_url,
            model=self.settings.llm_model,
        )
        self.memory = SessionMemory()
        self._pending_plan: Plan | None = None

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------
    def chat(self, text: str) -> AgentResponse:
        text = text.strip()
        if not text:
            return AgentResponse(
                kind=AgentTurnKind.CHITCHAT,
                text="(empty message — nothing to do)",
            )

        decision = _classify_confirmation(text)

        # If a plan is awaiting confirmation, interpret yes/no/dry-run intents.
        if self._pending_plan is not None:
            if decision == "yes":
                return self._execute_pending(dry_run=False)
            if decision == "dry":
                return self._execute_pending(dry_run=True, keep_pending=True)
            if decision == "no":
                cancelled = self._pending_plan
                self._pending_plan = None
                msg = "Cancelled. Tell me a new instruction whenever you're ready."
                self.memory.add_assistant(msg)
                return AgentResponse(
                    kind=AgentTurnKind.CHITCHAT, text=msg, plan=cancelled
                )
            # Anything else → treat as new instruction. Clear pending.
            self._pending_plan = None
        elif decision is not None:
            msg = "There is no plan awaiting confirmation. Send me a workflow instruction first."
            self.memory.add_assistant(msg)
            return AgentResponse(kind=AgentTurnKind.CHITCHAT, text=msg)

        self.memory.add_user(text)
        plan = self.planner.plan(text, self.memory)
        return self._handle_plan(plan)

    def execute(self, plan: Plan, *, dry_run: bool = False) -> AgentResponse:
        """Run a plan directly (used by the API when the UI clicks 'Run')."""
        return self._run(plan, dry_run=dry_run, keep_pending=dry_run)

    def confirm(self, action: str) -> AgentResponse:
        """Explicit confirmation handler used by the API. ``action`` ∈ {run, dry_run, cancel}."""
        if self._pending_plan is None:
            return AgentResponse(
                kind=AgentTurnKind.CHITCHAT,
                text="There is no plan awaiting confirmation.",
            )
        if action == "run":
            return self._execute_pending(dry_run=False)
        if action == "dry_run":
            return self._execute_pending(dry_run=True, keep_pending=True)
        if action == "cancel":
            cancelled = self._pending_plan
            self._pending_plan = None
            msg = "Plan cancelled."
            self.memory.add_assistant(msg)
            return AgentResponse(kind=AgentTurnKind.CHITCHAT, text=msg, plan=cancelled)
        raise ValueError(f"Unknown confirm action: {action!r}")

    def reset(self) -> None:
        self.memory.reset()
        self._pending_plan = None

    @property
    def pending_plan(self) -> Plan | None:
        return self._pending_plan

    @property
    def confirmation_status(self) -> ConfirmationStatus:
        return ConfirmationStatus.PENDING if self._pending_plan else ConfirmationStatus.NONE

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------
    def _handle_plan(self, plan: Plan) -> AgentResponse:
        if plan.needs_clarification:
            text = "I need a bit more info before I can plan this:\n" + "\n".join(
                f"  • {q}" for q in plan.needs_clarification
            )
            self.memory.add_assistant(text)
            return AgentResponse(kind=AgentTurnKind.CLARIFICATION, text=text, plan=plan)

        if not plan.steps:
            self.memory.add_assistant(plan.summary)
            return AgentResponse(kind=AgentTurnKind.CHITCHAT, text=plan.summary, plan=plan)

        if plan.requires_confirmation or plan.has_destructive_step:
            self._pending_plan = plan
            preview = format_plan_preview(plan)
            self.memory.add_assistant(preview)
            return AgentResponse(
                kind=AgentTurnKind.PLAN_PREVIEW,
                text=preview,
                plan=plan,
                awaiting_confirmation=True,
            )

        return self._run(plan, dry_run=False, keep_pending=False)

    def _execute_pending(self, *, dry_run: bool, keep_pending: bool = False) -> AgentResponse:
        plan = self._pending_plan
        assert plan is not None
        return self._run(plan, dry_run=dry_run, keep_pending=keep_pending)

    def _run(self, plan: Plan, *, dry_run: bool, keep_pending: bool) -> AgentResponse:
        executor = Executor(self.client)
        report = executor.execute(plan, dry_run=dry_run)
        if not keep_pending:
            self._pending_plan = None
        text = format_execution_report(plan, report)
        self.memory.add_assistant(text)
        return AgentResponse(
            kind=AgentTurnKind.EXECUTION,
            text=text,
            plan=plan,
            report=report,
            awaiting_confirmation=keep_pending,
        )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _classify_confirmation(text: str) -> str | None:
    t = text.lower().strip().rstrip(".!?")
    if t in _DRY_RUN or t.startswith(("dry run", "dryrun", "dry-run")):
        return "dry"
    if t in _AFFIRMATIVE:
        return "yes"
    if t in _NEGATIVE:
        return "no"
    return None


def format_plan_preview(plan: Plan) -> str:
    lines: list[str] = [f"Plan: {plan.summary}", ""]
    for step in plan.steps:
        flags: list[str] = []
        if step.destructive:
            flags.append("destructive")
        if step.for_each is not None:
            flags.append(f"for-each(step {step.for_each})")
        flag_str = f" [{', '.join(flags)}]" if flags else ""
        lines.append(f"  {step.id}. {step.description}{flag_str}")
        lines.append(f"     → {step.tool}({_short_args(step.args)})")
    if plan.notes:
        lines.append("")
        for n in plan.notes:
            lines.append(f"⚠  {n}")
    lines.append("")
    lines.append("Reply 'yes' to run, 'dry run' to preview API calls, or 'no' to cancel.")
    return "\n".join(lines)


def format_execution_report(plan: Plan, report: ExecutionReport) -> str:
    header = "✅ " if report.success else "❌ "
    if report.dry_run:
        header = "🧪 "
    lines: list[str] = [header + report.message]
    for r in report.steps:
        line = f"  {r.step_id}. {r.tool}: {r.status.value}"
        if r.error:
            line += f" — {r.error}"
        elif r.status.value == "ok" and r.output is not None:
            summary = _summarise_output(r.tool, r.output)
            if summary:
                line += f" — {summary}"
        lines.append(line)
    if report.rolled_back:
        lines.append(f"  rolled back: {report.rolled_back}")
    return "\n".join(lines)


def _short_args(args: dict) -> str:
    parts = []
    for k, v in args.items():
        if isinstance(v, str) and len(v) > 40:
            v = v[:37] + "..."
        parts.append(f"{k}={v!r}")
    return ", ".join(parts)


_LIST_PREVIEW_LIMIT = 8


def _summarise_output(tool: str, output) -> str:
    """One-line summary of a tool's output.

    Used in both the user-facing execution report AND the per-turn assistant
    text we write into memory. So it has two jobs: show humans what happened,
    and give the LLM enough detail to reason on the *next* turn (e.g. "the
    user just saw templates X/Y/Z; if they now ask about template W, point
    out it doesn't exist").
    """
    if isinstance(output, list):
        if not output:
            return _empty_summary(tool)
        if tool == "find_contacts":
            return _format_list(output, _format_contact, "contact(s)")
        if tool == "list_templates":
            return _format_list(output, _format_template, "template(s)")
        if tool == "list_operators":
            return _format_list(output, _format_operator, "operator(s)")
        return f"{len(output)} item(s)"
    if isinstance(output, dict):
        if "messageId" in output:
            return f"messageId={output['messageId']}"
        if "broadcastId" in output:
            return f"broadcastId={output['broadcastId']}"
        if "result" in output:
            return f"result={output['result']}"
    return ""


def _empty_summary(tool: str) -> str:
    label = {
        "find_contacts": "no contacts matched",
        "list_templates": "no templates available",
        "list_operators": "no operators available",
    }.get(tool, "0 item(s)")
    return label


def _format_list(items, formatter, noun: str) -> str:
    n = len(items)
    head = ", ".join(formatter(it) for it in items[:_LIST_PREVIEW_LIMIT])
    if n > _LIST_PREVIEW_LIMIT:
        head += f", … (+{n - _LIST_PREVIEW_LIMIT} more)"
    return f"{n} {noun} — {head}"


def _format_contact(c: dict) -> str:
    name = c.get("fullName") or "(unnamed)"
    waid = c.get("wAid") or c.get("phone") or "?"
    return f"{name} ({waid})"


def _format_template(t: dict) -> str:
    name = t.get("name") or "(unnamed)"
    cat = t.get("category")
    return f"{name} [{cat}]" if cat else name


def _format_operator(o: dict) -> str:
    name = o.get("name") or o.get("email") or "(unknown)"
    team = o.get("team")
    return f"{name} ({team})" if team else name
