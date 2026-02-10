"""Output formatting utilities for the CLI."""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from rich.console import Console
from rich.panel import Panel
from rich.syntax import Syntax
from rich.table import Table
from rich.text import Text

from workbench.session.events import SessionEvent
from workbench.tools.base import Tool, ToolRisk

RISK_COLORS = {
    ToolRisk.READ_ONLY: "green",
    ToolRisk.WRITE: "yellow",
    ToolRisk.DESTRUCTIVE: "red",
    ToolRisk.SHELL: "bold red",
}


class OutputFormatter:
    """Rich-based output formatting for the workbench CLI."""

    def __init__(self, console: Console | None = None) -> None:
        self.console = console or Console()

    def format_tool_list(self, tools: list[Tool]) -> None:
        table = Table(title="Registered Tools", show_lines=True)
        table.add_column("Name", style="cyan", no_wrap=True)
        table.add_column("Risk", no_wrap=True)
        table.add_column("Privacy", no_wrap=True)
        table.add_column("Description")

        for t in tools:
            color = RISK_COLORS.get(t.risk_level, "white")
            risk_text = Text(t.risk_level.name, style=color)
            table.add_row(t.name, risk_text, t.privacy_scope.value, t.description)

        self.console.print(table)

    def format_tool_info(self, tool: Tool) -> None:
        color = RISK_COLORS.get(tool.risk_level, "white")
        self.console.print(Panel(
            f"[bold]{tool.name}[/bold]\n\n"
            f"[dim]Risk:[/dim] [{color}]{tool.risk_level.name}[/{color}]\n"
            f"[dim]Privacy:[/dim] {tool.privacy_scope.value}\n"
            f"[dim]Confirmation hint:[/dim] {tool.confirmation_hint}\n"
            f"[dim]Secret fields:[/dim] {tool.secret_fields or 'none'}\n\n"
            f"{tool.description}",
            title=f"Tool: {tool.name}",
        ))
        schema_json = json.dumps(tool.parameters, indent=2)
        self.console.print(Syntax(schema_json, "json", theme="monokai"))

    def format_tool_result(self, tool_name: str, result: Any) -> None:
        if hasattr(result, "success"):
            status = "[green]OK[/green]" if result.success else "[red]FAILED[/red]"
            self.console.print(f"  [{tool_name}] {status}: {result.content[:200]}")
        else:
            self.console.print(f"  [{tool_name}] {result}")

    def format_session_list(self, sessions: list[dict]) -> None:
        if not sessions:
            self.console.print("[dim]No sessions found.[/dim]")
            return

        table = Table(title="Sessions")
        table.add_column("ID", style="cyan", no_wrap=True)
        table.add_column("Created", no_wrap=True)
        table.add_column("Metadata")

        for s in sessions:
            table.add_row(
                s.get("session_id", "?"),
                s.get("created_at", "?"),
                str(s.get("metadata", {})),
            )

        self.console.print(table)

    def format_session_events(self, events: list[SessionEvent]) -> None:
        if not events:
            self.console.print("[dim]No events.[/dim]")
            return

        for ev in events:
            ts = ev.timestamp.strftime("%H:%M:%S") if isinstance(ev.timestamp, datetime) else str(ev.timestamp)
            etype = ev.event_type

            color_map = {
                "user_message": "blue",
                "assistant_message": "green",
                "tool_call_request": "yellow",
                "tool_call_result": "cyan",
                "confirmation": "magenta",
                "protocol_error": "red",
                "model_switch": "dim",
            }
            color = color_map.get(etype, "white")

            content = ""
            if etype == "user_message":
                content = ev.payload.get("content", "")[:100]
            elif etype == "assistant_message":
                content = ev.payload.get("content", "")[:100]
            elif etype == "tool_call_request":
                content = f"{ev.payload.get('tool_name', '?')}({json.dumps(ev.payload.get('arguments', {}))[:80]})"
            elif etype == "tool_call_result":
                ok = "OK" if ev.payload.get("success") else "FAIL"
                content = f"{ev.payload.get('tool_name', '?')} -> {ok}"
            elif etype == "confirmation":
                yn = "confirmed" if ev.payload.get("confirmed") else "denied"
                content = f"{ev.payload.get('tool_name', '?')}: {yn}"
            elif etype == "protocol_error":
                content = ev.payload.get("error_message", "")[:100]

            self.console.print(f"  [{color}]{ts} {etype:>20s}[/{color}]  {content}")

    def format_config(self, config: dict) -> None:
        yaml_str = json.dumps(config, indent=2, default=str)
        self.console.print(Syntax(yaml_str, "json", theme="monokai"))

    def format_confirmation(
        self,
        tool_name: str,
        risk_level: str,
        target: str | None,
        arguments: dict,
    ) -> None:
        args_str = json.dumps(arguments, indent=2, default=str)
        parts = [
            f"[bold yellow]Tool call requires confirmation[/bold yellow]\n",
            f"  [bold]Tool:[/bold]  {tool_name}",
            f"  [bold]Risk:[/bold]  {risk_level}",
        ]
        if target:
            parts.append(f"  [bold]Target:[/bold] {target}")
        parts.append(f"  [bold]Args:[/bold]")
        self.console.print("\n".join(parts))
        self.console.print(Syntax(args_str, "json", theme="monokai"))

    def export_session(
        self, events: list[SessionEvent], fmt: str = "markdown"
    ) -> str:
        if fmt == "json":
            return json.dumps([e.to_dict() for e in events], indent=2, default=str)

        lines: list[str] = []
        if fmt == "runbook":
            lines.append("# Session Runbook\n")
            step = 0
            for ev in events:
                if ev.event_type == "user_message":
                    step += 1
                    lines.append(f"## Step {step}: User Request\n")
                    lines.append(f"{ev.payload.get('content', '')}\n")
                elif ev.event_type == "tool_call_request":
                    lines.append(f"### Action: {ev.payload.get('tool_name', '?')}\n")
                    lines.append(f"```json\n{json.dumps(ev.payload.get('arguments', {}), indent=2)}\n```\n")
                elif ev.event_type == "tool_call_result":
                    ok = "Success" if ev.payload.get("success") else "Failed"
                    lines.append(f"**Result:** {ok}\n")
                    content = ev.payload.get("content", "")
                    if content:
                        lines.append(f"```\n{content[:500]}\n```\n")
                elif ev.event_type == "assistant_message":
                    lines.append(f"### Assistant Response\n")
                    lines.append(f"{ev.payload.get('content', '')}\n")
        else:
            lines.append("# Session Log\n")
            for ev in events:
                ts = ev.timestamp.isoformat() if isinstance(ev.timestamp, datetime) else str(ev.timestamp)
                lines.append(f"**{ts}** - `{ev.event_type}`\n")
                if ev.event_type == "user_message":
                    lines.append(f"> {ev.payload.get('content', '')}\n")
                elif ev.event_type == "assistant_message":
                    lines.append(f"{ev.payload.get('content', '')}\n")
                elif ev.event_type in ("tool_call_request", "tool_call_result"):
                    lines.append(f"```json\n{json.dumps(ev.payload, indent=2, default=str)}\n```\n")

        return "\n".join(lines)
