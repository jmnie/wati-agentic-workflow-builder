"""CLI entry point.

Run ``wati-agent`` for an interactive chat. Plan previews print to the terminal;
type 'yes' to run, 'dry' for dry-run, 'no' to cancel, '/reset' to clear memory,
and Ctrl-D to exit.
"""

from __future__ import annotations

import sys

import typer
from rich.console import Console
from rich.panel import Panel
from rich.text import Text

from .agent import Agent
from .agent.schemas import AgentTurnKind

app = typer.Typer(add_completion=False, help="WATI WhatsApp automation agent.")
console = Console()


@app.command()
def chat(
    backend: str = typer.Option(None, help="Override WATI backend: mock | real"),
    llm: str = typer.Option(None, help="Override LLM mode: real | fake"),
) -> None:
    """Interactive chat with the agent."""
    import os

    if backend:
        os.environ["WATI_AGENT_BACKEND"] = backend
    if llm:
        os.environ["WATI_AGENT_LLM"] = llm

    agent = Agent()
    _banner(agent)

    while True:
        try:
            line = input("you ▸ ").strip()
        except (EOFError, KeyboardInterrupt):
            console.print("\n[dim]bye.[/dim]")
            return

        if not line:
            continue
        if line in ("/quit", "/exit"):
            return
        if line == "/reset":
            agent.reset()
            console.print("[dim]session reset.[/dim]")
            continue
        if line == "/help":
            _banner(agent)
            continue

        resp = agent.chat(line)
        _render(resp)


def _banner(agent: Agent) -> None:
    cfg = agent.settings
    body = Text()
    body.append("WATI Agent — wati=", style="bold")
    body.append(cfg.backend, style="cyan")
    body.append("  llm=", style="bold")
    if cfg.llm == "real" and cfg.llm_api_key:
        body.append(f"{cfg.llm_provider}({cfg.llm_model or 'default'})", style="cyan")
    else:
        body.append("fake (rule-based)", style="cyan")
    body.append("\n\nType anything in plain English. Commands: ", style="dim")
    body.append("/reset /help /quit", style="bold")
    console.print(Panel(body, border_style="blue"))


def _render(resp) -> None:
    style = {
        AgentTurnKind.CLARIFICATION: "yellow",
        AgentTurnKind.PLAN_PREVIEW: "magenta",
        AgentTurnKind.EXECUTION: "green",
        AgentTurnKind.CHITCHAT: "white",
    }.get(resp.kind, "white")
    console.print(Panel(resp.text, border_style=style, title=f"agent · {resp.kind.value}"))


@app.command()
def serve(
    host: str = typer.Option("127.0.0.1"),
    port: int = typer.Option(8000),
) -> None:
    """Run the FastAPI server (chat web UI)."""
    import uvicorn

    uvicorn.run("wati_agent.server:app", host=host, port=port, reload=False)


def main() -> None:
    app()


if __name__ == "__main__":
    main()
