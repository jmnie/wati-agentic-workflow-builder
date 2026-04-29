"""Planner: turn natural-language requests into structured Plans.

Two implementations live here:

* :class:`LLMPlanner` — calls Anthropic Claude with tool-use to force a plan
  shaped exactly like ``Plan``. Production path.
* :class:`FakePlanner` — rule-based fallback used by tests and by anyone who
  wants to demo the agent without an API key. It covers the assignment's three
  example instructions plus a few obvious extensions.

Both implementations satisfy the same protocol so the agent core never needs
to know which one is in use.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Protocol

from pydantic import ValidationError

from .llm import LLMBackend, build_backend
from .memory import SessionMemory
from .schemas import Plan, Step
from .tools import all_tools, tool_catalog_for_prompt

log = logging.getLogger(__name__)


SYSTEM_PROMPT = """\
You are an automation planner for the WATI WhatsApp Business API. A non-technical operator
gives you an instruction in plain English; you produce a structured plan of API calls that
fulfils it. Another component executes your plan — you never call APIs yourself.

# Tools you can use
{tool_catalog}

# Producing plans
- Output is delivered by calling the `submit_plan` tool exactly once. Never reply with prose.
- Each step has an integer `id` starting at 1, contiguous.
- For "do X to every contact matching Y" use a `find_contacts` step first, then a follow-up
  step with `for_each` set to the find step's id. In the follow-up step, use "$item.wAid" as
  the placeholder for each contact's WhatsApp number, and "$item.fullName" for the name.
- Keep each step description short and user-readable — it goes into a confirmation preview.
- Mark `requires_confirmation: true` whenever the plan contains any destructive step
  (sending messages, broadcasts, mutating tags or attributes, assigning operators).
- Never invent template names, segment names, or team names. If you are unsure whether a
  resource exists, add a `list_templates` / `list_operators` step first, OR put the question
  in `needs_clarification`.
- Never use a tool that is not in the catalogue above.
- Add caveats to `notes` when the plan is irreversible (sending messages, broadcasts).

# Use the conversation history
Earlier assistant turns include the actual data returned by previous tool calls — for example,
"Plan executed successfully. 1. list_templates: ok — 3 template(s) — renewal_reminder [MARKETING], …".
Read those carefully:
- If the user mentions a template / operator / team name, check whether it appeared in
  earlier tool outputs. If it did NOT appear, point that out in `needs_clarification` and
  list the names that DID appear.
- Do NOT re-issue a `list_templates` (or `list_operators`) step if the user already saw the
  same listing recently — refer to that data instead.

# Writing clarifications
When you cannot plan because input is ambiguous or missing fields:
- Quote the user's exact phrase in the question so they see what you parsed.
- Be specific about what's missing (audience? action? template name? team name?).
- Never write a generic catch-all ("tell me more") — every clarification should be tied to
  the concrete request.
- If two consecutive turns from the user are both ambiguous, vary your wording so the
  conversation doesn't stall on identical replies.

# Examples
User: "Find all VIP contacts and send them the renewal_reminder template using their name."
Plan:
  step 1: find_contacts(tag="VIP")
  step 2: send_template_message with for_each=1, args:
          whatsapp_number="$item.wAid",
          template_name="renewal_reminder",
          broadcast_name="renewal_reminder",
          parameters=[{{"name":"body_1","value":"$item.fullName"}}]

User: "Escalate 6281234567890 to the Support team and tag them 'escalated'."
Plan:
  step 1: assign_team(whatsapp_number="6281234567890", team_name="Support")
  step 2: add_tag(whatsapp_number="6281234567890", tag="escalated")

User: "VIP template"  (after the assistant just listed templates: renewal_reminder, flash_sale, welcome)
Expected: needs_clarification = [
  "I don't see a template called 'VIP' — the available ones are renewal_reminder, flash_sale, "
  "and welcome. Did you mean to send one of those to VIP-tagged contacts?"
]
"""


class Planner(Protocol):
    def plan(self, user_message: str, memory: SessionMemory) -> Plan: ...


# ---------------------------------------------------------------------------
# LLM-backed planner
# ---------------------------------------------------------------------------


class LLMPlanner:
    """Plan via any LLM backend that speaks our ``LLMBackend`` protocol."""

    def __init__(self, backend: LLMBackend) -> None:
        self._backend = backend
        self._system_prompt = SYSTEM_PROMPT.format(tool_catalog=tool_catalog_for_prompt())
        self._plan_schema = Plan.model_json_schema()

    def plan(self, user_message: str, memory: SessionMemory) -> Plan:
        messages = memory.as_messages() + [{"role": "user", "content": user_message}]
        try:
            plan_input = self._backend.submit_plan(
                system_prompt=self._system_prompt,
                messages=messages,
                plan_schema=self._plan_schema,
            )
        except Exception as e:  # network, auth, rate limit
            log.exception("LLM planner failure (%s)", self._backend.name)
            return Plan(
                summary="Could not reach the planner.",
                steps=[],
                needs_clarification=[
                    f"The planner ({self._backend.name}) is unavailable right now "
                    f"({type(e).__name__}). Please retry in a moment."
                ],
                requires_confirmation=False,
            )

        if plan_input is None:
            log.warning("LLM (%s) did not return a tool call", self._backend.name)
            return Plan(
                summary="Planner did not produce a plan.",
                steps=[],
                needs_clarification=["I didn't quite catch that — could you rephrase your request?"],
                requires_confirmation=False,
            )
        try:
            plan = Plan.model_validate(plan_input)
        except ValidationError as e:
            log.warning("LLM (%s) produced an invalid plan: %s", self._backend.name, e)
            return Plan(
                summary="Planner produced an invalid plan.",
                steps=[],
                needs_clarification=[
                    "Sorry, I produced a malformed plan. Could you re-state the request?"
                ],
                requires_confirmation=False,
            )
        return _guard_against_spurious_template_listing(user_message, plan)


# ---------------------------------------------------------------------------
# Rule-based fallback planner
# ---------------------------------------------------------------------------


class FakePlanner:
    """Rule-based planner for tests + API-key-less demos.

    Recognises a handful of canonical request shapes. Anything outside those
    shapes returns a plan with `needs_clarification`, mirroring how the LLM
    would behave on an unfamiliar request.
    """

    def plan(self, user_message: str, memory: SessionMemory) -> Plan:
        text = user_message.strip()
        flags = re.IGNORECASE

        # 1) "list templates"
        if re.search(r"\blist\b.*\btemplates?\b", text, flags) or text.lower() in {"templates", "show templates"}:
            return Plan(
                summary="List approved WhatsApp templates.",
                steps=[
                    Step(id=1, tool="list_templates", args={}, description="Fetch all approved templates."),
                ],
                requires_confirmation=False,
            )

        # 2) "list operators"
        if re.search(r"\blist\b.*\boperators?\b", text, flags):
            return Plan(
                summary="List operators.",
                steps=[
                    Step(id=1, tool="list_operators", args={}, description="Fetch operator list."),
                ],
                requires_confirmation=False,
            )

        # 3) "Find all contacts tagged X and send them the Y template"
        # Non-greedy .*? — without it the trailing greedy [\w_\-]+ match collapses to one char.
        m = re.search(
            r"tagg?ed\s+['\"]?(?P<tag>[\w\-]+)['\"]?.*?send.*?['\"]?(?P<tpl>[\w_\-]+)['\"]?\s+template",
            text,
            flags,
        )
        if m:
            return _plan_template_to_tag(tag=m.group("tag"), template=m.group("tpl"))

        # 4) "send a broadcast with the X template to all contacts who have city='Y'"
        m = re.search(
            r"broadcast.*?['\"]?(?P<tpl>[\w_\-]+)['\"]?\s+template.*?(?P<attr>\w+)\s*=\s*['\"]?(?P<val>[\w\-]+)['\"]?",
            text,
            flags,
        )
        if m:
            return _plan_broadcast_to_attribute(
                template=m.group("tpl"),
                attr_name=m.group("attr"),
                attr_value=m.group("val"),
            )

        # 5) "Escalate <number>" or "escalate <number> to <team>"
        m = re.search(r"escalate\s+(?P<num>\d{8,})(?:\s+to\s+(?:the\s+)?(?P<team>\w+))?", text, flags)
        if m:
            team = (m.group("team") or "Support").capitalize()
            return Plan(
                summary=f"Escalate {m.group('num')} to {team} and tag as 'escalated'.",
                steps=[
                    Step(
                        id=1,
                        tool="assign_team",
                        args={"whatsapp_number": m.group("num"), "team_name": team},
                        description=f"Assign conversation to the {team} team.",
                        destructive=True,
                    ),
                    Step(
                        id=2,
                        tool="add_tag",
                        args={"whatsapp_number": m.group("num"), "tag": "escalated"},
                        description="Tag the contact as 'escalated'.",
                        destructive=True,
                    ),
                ],
                requires_confirmation=True,
                notes=["Tag changes can be rolled back; team assignment cannot."],
            )

        # 6) Friendly chitchat — empty plan, no clarification needed.
        if re.search(r"\b(hi|hey|hello|thanks|thank you)\b", text, flags):
            return Plan(summary="Acknowledged.", steps=[], requires_confirmation=False)

        # Catch-all: quote the user's input so two ambiguous turns don't get
        # the identical reply.
        quote = text if len(text) <= 80 else text[:77] + "…"
        return Plan(
            summary="I'm not sure how to do that yet.",
            steps=[],
            needs_clarification=[
                f"I couldn't tell what to do with “{quote}”. "
                "I can find contacts by tag or attribute, send template messages, "
                "broadcast to segments, manage tags, and assign conversations to teams "
                "or operators. Tell me both an audience and an action."
            ],
            requires_confirmation=False,
        )


def _plan_template_to_tag(*, tag: str, template: str) -> Plan:
    return Plan(
        summary=f"Send the {template!r} template to every contact tagged {tag!r}.",
        steps=[
            Step(
                id=1,
                tool="find_contacts",
                args={"tag": tag},
                description=f"Find every contact tagged '{tag}'.",
            ),
            Step(
                id=2,
                tool="send_template_message",
                args={
                    "whatsapp_number": "$item.wAid",
                    "template_name": template,
                    "broadcast_name": template,
                    "parameters": [{"name": "body_1", "value": "$item.fullName"}],
                },
                description=f"Send the '{template}' template to each matching contact.",
                destructive=True,
                for_each=1,
            ),
        ],
        requires_confirmation=True,
        notes=["Outbound messages cannot be unsent."],
    )


def _plan_broadcast_to_attribute(*, template: str, attr_name: str, attr_value: str) -> Plan:
    segment_name = f"{attr_name}={attr_value}"
    return Plan(
        summary=f"Broadcast {template!r} to the {segment_name!r} segment.",
        steps=[
            Step(
                id=1,
                tool="find_contacts",
                args={"attribute_name": attr_name, "attribute_value": attr_value},
                description=f"Find contacts with {attr_name}={attr_value!r}.",
            ),
            Step(
                id=2,
                tool="send_broadcast",
                args={
                    "template_name": template,
                    "broadcast_name": template,
                    "segment_name": segment_name,
                },
                description=f"Send the '{template}' broadcast to the '{segment_name}' segment.",
                destructive=True,
            ),
        ],
        requires_confirmation=True,
        notes=[
            f"Assumes a WATI segment named '{segment_name}' exists for this attribute filter.",
            "Broadcasts can fan out to many contacts and cannot be unsent.",
        ],
    )


def build_planner(
    *,
    llm: str,
    provider: str,
    api_key: str | None,
    base_url: str | None,
    model: str | None,
) -> Planner:
    """Build a planner from settings. Falls back to ``FakePlanner`` on any error.

    The fallback is deliberate — the agent stays usable even if the LLM is
    misconfigured, so the user gets a clear "I can't understand X yet" rather
    than a 500.
    """
    if llm == "fake":
        return FakePlanner()
    if llm == "real":
        backend = build_backend(
            provider=provider,
            api_key=api_key,
            base_url=base_url,
            model=model,
        )
        if backend is None:
            log.warning("LLM backend unavailable; falling back to FakePlanner.")
            return FakePlanner()
        return LLMPlanner(backend)
    raise ValueError(f"Unknown LLM mode: {llm!r}")


def _guard_against_spurious_template_listing(user_message: str, plan: Plan) -> Plan:
    """Catch a common weak-model failure: defaulting every short intent to list_templates.

    The clarification quotes the user's actual phrase, so two ambiguous turns
    in a row don't produce identical-looking responses.
    """
    if not _is_single_tool_plan(plan, "list_templates"):
        return plan
    if _looks_like_template_listing_request(user_message):
        return plan
    quote = user_message.strip()
    if len(quote) > 80:
        quote = quote[:77] + "…"
    return Plan(
        summary="I need more detail before I can plan this.",
        steps=[],
        needs_clarification=[
            f"I couldn't tell what to do with “{quote}”. "
            "Tell me the audience (tag, segment, phone number, or attribute filter) "
            "and the action (send a template, send a broadcast, add a tag, or assign a team). "
            "If you want to see available templates, say 'list templates'."
        ],
        requires_confirmation=False,
    )


def _is_single_tool_plan(plan: Plan, tool_name: str) -> bool:
    return len(plan.steps) == 1 and plan.steps[0].tool == tool_name


def _looks_like_template_listing_request(user_message: str) -> bool:
    text = user_message.lower()
    return bool(
        re.search(r"\b(list|show|available|approved|what|which|view|see)\b.*\btemplates?\b", text)
        or re.search(r"\btemplates?\b.*\b(list|show|available|approved)\b", text)
        or text.strip() in {"templates", "list templates", "show templates"}
    )
