"""Tool registry.

Each Tool wraps one WatiClient method and adds:
  * a JSON-Schema-shaped declaration the planner can show the LLM,
  * a ``destructive`` flag (drives confirmation prompts),
  * an optional inverse for rollback.

The registry is the single source of truth for what the agent can do — the
planner reads from it, the executor dispatches through it, and the prompt is
generated from it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable

from ..wati.client import WatiAPIError, WatiClient

Handler = Callable[[WatiClient, dict[str, Any]], Any]
InverseFactory = Callable[[dict[str, Any], Any], "InverseCall | None"]


@dataclass
class InverseCall:
    """An action the executor can run to undo a successful step."""

    tool: str
    args: dict[str, Any]


@dataclass
class ToolParameter:
    name: str
    type: str  # "string" | "integer" | "boolean" | "object" | "array"
    description: str
    required: bool = True
    enum: list[str] | None = None
    items_type: str | None = None  # for arrays


@dataclass
class Tool:
    name: str
    description: str
    parameters: list[ToolParameter]
    handler: Handler
    destructive: bool = False
    inverse: InverseFactory | None = None
    notes: str = ""  # surfaced in plan previews, e.g. "sending cannot be undone"

    def json_schema(self) -> dict[str, Any]:
        props: dict[str, Any] = {}
        required: list[str] = []
        for p in self.parameters:
            schema: dict[str, Any] = {"type": p.type, "description": p.description}
            if p.enum:
                schema["enum"] = p.enum
            if p.type == "array" and p.items_type:
                schema["items"] = {"type": p.items_type}
            props[p.name] = schema
            if p.required:
                required.append(p.name)
        return {
            "type": "object",
            "properties": props,
            "required": required,
            "additionalProperties": False,
        }

    def call(self, client: WatiClient, args: dict[str, Any]) -> Any:
        missing = [p.name for p in self.parameters if p.required and args.get(p.name) is None]
        if missing:
            raise WatiAPIError(
                f"Invalid plan for tool {self.name!r}: missing required argument(s): "
                + ", ".join(missing)
            )
        return self.handler(client, args)


# ----------------------------------------------------------------------
# Concrete tool definitions
# ----------------------------------------------------------------------


def _find_contacts(client: WatiClient, args: dict[str, Any]) -> Any:
    return client.get_contacts(
        tag=args.get("tag"),
        attribute_name=args.get("attribute_name"),
        attribute_value=args.get("attribute_value"),
        page_size=args.get("page_size", 100),
        page_number=args.get("page_number", 1),
    )


def _get_contact(client: WatiClient, args: dict[str, Any]) -> Any:
    return client.get_contact(args["whatsapp_number"])


def _add_contact(client: WatiClient, args: dict[str, Any]) -> Any:
    return client.add_contact(
        args["whatsapp_number"],
        args["name"],
        args.get("custom_params"),
    )


def _add_tag(client: WatiClient, args: dict[str, Any]) -> Any:
    return client.add_tag(args["whatsapp_number"], args["tag"])


def _remove_tag(client: WatiClient, args: dict[str, Any]) -> Any:
    return client.remove_tag(args["whatsapp_number"], args["tag"])


def _update_attributes(client: WatiClient, args: dict[str, Any]) -> Any:
    return client.update_contact_attributes(
        args["whatsapp_number"],
        args["custom_params"],
    )


def _send_session_message(client: WatiClient, args: dict[str, Any]) -> Any:
    return client.send_session_message(args["whatsapp_number"], args["message_text"])


def _send_template_message(client: WatiClient, args: dict[str, Any]) -> Any:
    return client.send_template_message(
        args["whatsapp_number"],
        args["template_name"],
        args.get("broadcast_name") or args["template_name"],
        args.get("parameters", []),
    )


def _list_templates(client: WatiClient, args: dict[str, Any]) -> Any:
    return client.list_templates()


def _send_broadcast(client: WatiClient, args: dict[str, Any]) -> Any:
    return client.send_broadcast_to_segment(
        args["template_name"],
        args.get("broadcast_name") or args["template_name"],
        args["segment_name"],
    )


def _list_operators(client: WatiClient, args: dict[str, Any]) -> Any:
    return client.list_operators()


def _assign_team(client: WatiClient, args: dict[str, Any]) -> Any:
    return client.assign_team(args["whatsapp_number"], args["team_name"])


def _assign_operator(client: WatiClient, args: dict[str, Any]) -> Any:
    return client.assign_operator(args["whatsapp_number"], args["email"])


def _inverse_add_tag(args: dict, _result: Any) -> InverseCall | None:
    return InverseCall(tool="remove_tag", args={"whatsapp_number": args["whatsapp_number"], "tag": args["tag"]})


def _inverse_remove_tag(args: dict, _result: Any) -> InverseCall | None:
    return InverseCall(tool="add_tag", args={"whatsapp_number": args["whatsapp_number"], "tag": args["tag"]})


TOOLS: list[Tool] = [
    Tool(
        name="find_contacts",
        description=(
            "Find contacts, optionally filtered by tag and/or a custom attribute "
            "(e.g. attribute_name='city', attribute_value='Jakarta'). Returns a list of contact objects."
        ),
        parameters=[
            ToolParameter("tag", "string", "Filter by a single tag, e.g. 'VIP'.", required=False),
            ToolParameter("attribute_name", "string", "Custom attribute name to filter by.", required=False),
            ToolParameter("attribute_value", "string", "Custom attribute value to match.", required=False),
            ToolParameter("page_size", "integer", "Max results per page.", required=False),
            ToolParameter("page_number", "integer", "1-indexed page number.", required=False),
        ],
        handler=_find_contacts,
    ),
    Tool(
        name="get_contact",
        description="Look up a single contact by WhatsApp number.",
        parameters=[
            ToolParameter("whatsapp_number", "string", "WhatsApp number, digits only, e.g. '6281234567890'."),
        ],
        handler=_get_contact,
    ),
    Tool(
        name="add_contact",
        description="Add a contact with optional custom attributes.",
        parameters=[
            ToolParameter("whatsapp_number", "string", "WhatsApp number, digits only."),
            ToolParameter("name", "string", "Contact display name."),
            ToolParameter(
                "custom_params",
                "array",
                "Optional list of {name, value} dicts, e.g. [{'name':'city','value':'Jakarta'}].",
                required=False,
                items_type="object",
            ),
        ],
        handler=_add_contact,
        destructive=True,
    ),
    Tool(
        name="list_templates",
        description="List the WhatsApp message templates approved for this account.",
        parameters=[],
        handler=_list_templates,
    ),
    Tool(
        name="list_operators",
        description="List the operators (human agents) and their teams.",
        parameters=[],
        handler=_list_operators,
    ),
    Tool(
        name="add_tag",
        description="Add a tag to a contact.",
        parameters=[
            ToolParameter("whatsapp_number", "string", "WhatsApp number, digits only."),
            ToolParameter("tag", "string", "Tag to add, e.g. 'escalated'."),
        ],
        handler=_add_tag,
        destructive=True,
        inverse=_inverse_add_tag,
    ),
    Tool(
        name="remove_tag",
        description="Remove a tag from a contact.",
        parameters=[
            ToolParameter("whatsapp_number", "string", "WhatsApp number, digits only."),
            ToolParameter("tag", "string", "Tag to remove."),
        ],
        handler=_remove_tag,
        destructive=True,
        inverse=_inverse_remove_tag,
    ),
    Tool(
        name="update_contact_attributes",
        description="Set custom attributes on a contact (e.g. plan='pro', city='Jakarta').",
        parameters=[
            ToolParameter("whatsapp_number", "string", "WhatsApp number, digits only."),
            ToolParameter(
                "custom_params",
                "array",
                "List of {name, value} dicts.",
                items_type="object",
            ),
        ],
        handler=_update_attributes,
        destructive=True,
        notes="Previous attribute values are not snapshotted — rollback is not supported.",
    ),
    Tool(
        name="send_session_message",
        description="Send a free-form session text message to a contact (only valid in an open session window).",
        parameters=[
            ToolParameter("whatsapp_number", "string", "WhatsApp number, digits only."),
            ToolParameter("message_text", "string", "The message body."),
        ],
        handler=_send_session_message,
        destructive=True,
        notes="Outbound messages cannot be unsent — confirm before running.",
    ),
    Tool(
        name="send_template_message",
        description="Send an approved WhatsApp template message to a contact.",
        parameters=[
            ToolParameter("whatsapp_number", "string", "WhatsApp number, digits only."),
            ToolParameter("template_name", "string", "Name of an approved template."),
            ToolParameter(
                "broadcast_name",
                "string",
                "Name shown in the WATI dashboard. Defaults to template_name.",
                required=False,
            ),
            ToolParameter(
                "parameters",
                "array",
                "List of {name, value} dicts for v2 template body parameters, e.g. [{'name':'body_1','value':'Andi'}].",
                required=False,
                items_type="object",
            ),
        ],
        handler=_send_template_message,
        destructive=True,
        notes="Outbound messages cannot be unsent.",
    ),
    Tool(
        name="send_broadcast",
        description="Send a broadcast (template message) to a named segment of contacts.",
        parameters=[
            ToolParameter("template_name", "string", "Approved template name."),
            ToolParameter(
                "broadcast_name",
                "string",
                "Broadcast name shown in the dashboard. Defaults to template_name.",
                required=False,
            ),
            ToolParameter("segment_name", "string", "Segment / list name in WATI."),
        ],
        handler=_send_broadcast,
        destructive=True,
        notes="A broadcast can fan out to many contacts and cannot be unsent.",
    ),
    Tool(
        name="assign_team",
        description="Assign a contact's conversation to a team (creates/assigns a ticket).",
        parameters=[
            ToolParameter("whatsapp_number", "string", "WhatsApp number, digits only."),
            ToolParameter("team_name", "string", "Team name, e.g. 'Support'."),
        ],
        handler=_assign_team,
        destructive=True,
    ),
    Tool(
        name="assign_operator",
        description="Assign a contact's conversation to a specific operator by email.",
        parameters=[
            ToolParameter("whatsapp_number", "string", "WhatsApp number, digits only."),
            ToolParameter("email", "string", "Operator email."),
        ],
        handler=_assign_operator,
        destructive=True,
    ),
]


def get_tool(name: str) -> Tool:
    for t in TOOLS:
        if t.name == name:
            return t
    raise KeyError(f"Unknown tool: {name!r}")


def all_tools() -> list[Tool]:
    return list(TOOLS)


def tool_catalog_for_prompt() -> str:
    """Render a compact tool catalog for the LLM system prompt."""
    lines: list[str] = []
    for t in TOOLS:
        flags = []
        if t.destructive:
            flags.append("destructive")
        if t.inverse is not None:
            flags.append("invertible")
        flag_str = f" [{', '.join(flags)}]" if flags else ""
        lines.append(f"- {t.name}{flag_str}: {t.description}")
        for p in t.parameters:
            req = "required" if p.required else "optional"
            lines.append(f"    * {p.name} ({p.type}, {req}): {p.description}")
        if t.notes:
            lines.append(f"    note: {t.notes}")
    return "\n".join(lines)
