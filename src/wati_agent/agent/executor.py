"""Plan execution.

Resolves placeholders, runs each step, supports dry-run + rollback.

Why a separate executor? The planner is non-deterministic (it's an LLM); the
executor is the deterministic side that turns a Plan into real API calls. This
split lets us unit-test execution (rollback, error paths, dry-run) without
touching the LLM.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Any

from ..wati.client import WatiAPIError, WatiClient
from .schemas import ExecutionReport, Plan, Step, StepResult, StepStatus
from .tools import InverseCall, Tool, get_tool

log = logging.getLogger(__name__)


@dataclass
class _CompletedAction:
    step_id: int
    inverse: InverseCall | None
    iteration_index: int = 0


@dataclass
class Executor:
    client: WatiClient
    completed: list[_CompletedAction] = field(default_factory=list)

    def execute(self, plan: Plan, *, dry_run: bool = False) -> ExecutionReport:
        results: list[StepResult] = []
        step_outputs: dict[int, Any] = {}

        for step in plan.steps:
            outputs: list[Any] = []
            args_log: list[dict[str, Any]] = []
            try:
                tool = _resolve_tool(step.tool)
                iterations = self._materialise_iterations(step, step_outputs)
                # In dry-run, we still execute read-only tools so subsequent steps see real
                # data (and thus a meaningful preview). Destructive tools are only simulated.
                simulate = dry_run and tool.destructive
                for idx, item_args in enumerate(iterations):
                    args_log.append(item_args)
                    if simulate:
                        outputs.append({"dry_run": True, "would_call": tool.name, "args": item_args})
                    else:
                        out = tool.call(self.client, item_args)
                        outputs.append(out)
                        if not dry_run:
                            inv = tool.inverse(item_args, out) if tool.inverse else None
                            self.completed.append(
                                _CompletedAction(step_id=step.id, inverse=inv, iteration_index=idx)
                            )
            except WatiAPIError as e:
                return self._fail_step(
                    plan,
                    results,
                    step,
                    args_log,
                    outputs,
                    dry_run,
                    str(e),
                )
            except Exception as e:
                log.exception("Unexpected executor failure at step %d", step.id)
                error = f"Unexpected error while executing step {step.id}: {type(e).__name__}: {e}"
                return self._fail_step(
                    plan,
                    results,
                    step,
                    args_log,
                    outputs,
                    dry_run,
                    error,
                )

            single_output = outputs[0] if step.for_each is None and outputs else outputs
            step_outputs[step.id] = single_output
            results.append(
                StepResult(
                    step_id=step.id,
                    tool=step.tool,
                    args_resolved=args_log,
                    status=StepStatus.OK,
                    output=single_output,
                )
            )

        return self._finalise(plan, results, dry_run, abort=False)

    # ------------------------------------------------------------------
    def _materialise_iterations(
        self, step: Step, step_outputs: dict[int, Any]
    ) -> list[dict[str, Any]]:
        """Resolve placeholders, returning a list of arg dicts (one per iteration)."""
        if step.for_each is None:
            return [_resolve_placeholders(step.args, item=None)]

        if step.for_each not in step_outputs:
            raise WatiAPIError(
                f"Step {step.id} references step {step.for_each}, but that step has not run."
            )
        source = step_outputs[step.for_each]
        if not isinstance(source, list):
            raise WatiAPIError(
                f"Step {step.id} references step {step.for_each} but its output is not a list."
            )
        return [_resolve_placeholders(step.args, item=item) for item in source]

    def _fail_step(
        self,
        plan: Plan,
        results: list[StepResult],
        step: Step,
        args_log: list[dict[str, Any]],
        outputs: list[Any],
        dry_run: bool,
        error: str,
    ) -> ExecutionReport:
        results.append(
            StepResult(
                step_id=step.id,
                tool=step.tool,
                args_resolved=args_log,
                status=StepStatus.FAILED,
                output=outputs or None,
                error=error,
            )
        )
        return self._finalise(plan, results, dry_run, abort=True, error=error)

    def _finalise(
        self,
        plan: Plan,
        results: list[StepResult],
        dry_run: bool,
        *,
        abort: bool,
        error: str | None = None,
    ) -> ExecutionReport:
        rolled_back: list[int] = []
        if abort and not dry_run and self.completed:
            rolled_back = self._rollback()

        # Mark steps that never ran as skipped.
        executed_ids = {r.step_id for r in results}
        for step in plan.steps:
            if step.id not in executed_ids:
                results.append(
                    StepResult(step_id=step.id, tool=step.tool, status=StepStatus.SKIPPED)
                )

        message = ""
        if dry_run and abort:
            message = (
                f"Dry-run failed at step {results[-1].step_id if results else '?'}: "
                f"{error or 'unknown error'}."
            )
        elif dry_run:
            message = "Dry-run complete — no API calls were made."
        elif abort:
            head = f"Plan failed at step {results[-1].step_id if results else '?'}: {error or 'unknown error'}."
            if rolled_back:
                message = f"{head} Rolled back steps: {sorted(rolled_back)}."
            else:
                message = f"{head} Nothing to roll back."
        else:
            message = "Plan executed successfully."

        return ExecutionReport(
            success=not abort,
            dry_run=dry_run,
            steps=sorted(results, key=lambda r: r.step_id),
            rolled_back=sorted(set(rolled_back)),
            message=message,
        )

    def _rollback(self) -> list[int]:
        rolled_back: list[int] = []
        for action in reversed(self.completed):
            if action.inverse is None:
                log.warning("Step %d has no inverse; cannot rollback iteration %d.", action.step_id, action.iteration_index)
                continue
            try:
                tool = get_tool(action.inverse.tool)
                tool.call(self.client, action.inverse.args)
                rolled_back.append(action.step_id)
            except Exception as e:  # log and keep going — best-effort rollback
                log.error("Rollback for step %d failed: %s", action.step_id, e)
        self.completed.clear()
        return rolled_back


# ---------------------------------------------------------------------------
# Placeholder resolution
# ---------------------------------------------------------------------------


def _resolve_tool(name: str) -> Tool:
    try:
        return get_tool(name)
    except KeyError as e:
        raise WatiAPIError(str(e)) from e


_ITEM_PLACEHOLDER = re.compile(r"\$item(?:\.([A-Za-z_][\w.]*))?")


def _resolve_placeholders(value: Any, *, item: Any) -> Any:
    if isinstance(value, str):
        return _resolve_string(value, item)
    if isinstance(value, dict):
        return {k: _resolve_placeholders(v, item=item) for k, v in value.items()}
    if isinstance(value, list):
        return [_resolve_placeholders(v, item=item) for v in value]
    return value


def _resolve_string(s: str, item: Any) -> Any:
    """Replace ``$item`` and ``$item.<field>`` references inside a string.

    Whole-string replacement preserves the original Python type (so a single
    ``$item`` placeholder that points at a number stays a number).
    """
    if s == "$item":
        if item is None:
            raise WatiAPIError("Cannot resolve $item because the step is not using for_each.")
        return item
    if s.startswith("$item."):
        if item is None:
            raise WatiAPIError(f"Cannot resolve {s!r} because the step is not using for_each.")
        field = s[len("$item."):]
        return _lookup(item, field)
    # Inline substitution — produces a string.
    if "$item" in s:
        if item is None:
            raise WatiAPIError(f"Cannot resolve {s!r} because the step is not using for_each.")

        def repl(match: re.Match[str]) -> str:
            path = match.group(1)
            val = _lookup(item, path) if path else item
            return str(val)

        return _ITEM_PLACEHOLDER.sub(repl, s)
    return s


def _lookup(obj: Any, path: str) -> Any:
    if obj is None:
        raise WatiAPIError(f"Cannot resolve $item.{path}: item is missing.")
    cur: Any = obj
    for part in path.split("."):
        if isinstance(cur, dict):
            if part not in cur:
                raise WatiAPIError(f"Cannot resolve $item.{path}: missing field {part!r}.")
            cur = cur.get(part)
        else:
            if not hasattr(cur, part):
                raise WatiAPIError(f"Cannot resolve $item.{path}: missing field {part!r}.")
            cur = getattr(cur, part)
        if cur is None:
            raise WatiAPIError(f"Cannot resolve $item.{path}: field {part!r} is null.")
    return cur
