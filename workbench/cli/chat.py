"""Interactive chat session handler."""

from __future__ import annotations

import asyncio
import json
from typing import Any

from rich.console import Console
from rich.markdown import Markdown

from workbench.cli.output import OutputFormatter
from workbench.llm.types import StreamChunk, ToolCall
from workbench.orchestrator.core import Orchestrator
from workbench.tools.base import ToolRisk


class ChatHandler:
    """
    Manages the interactive chat loop.

    Handles streaming output, inline commands, and tool confirmation prompts.
    """

    def __init__(
        self,
        orchestrator: Orchestrator,
        console: Console | None = None,
    ) -> None:
        self.orchestrator = orchestrator
        self.console = console or Console()
        self.formatter = OutputFormatter(self.console)
        self._running = True

    async def confirm_tool(self, tool_name: str, tool_call: ToolCall) -> bool:
        """Rich-formatted confirmation prompt for tool calls."""
        tool = self.orchestrator.registry.get(tool_name)
        risk = tool.risk_level.name if tool else "UNKNOWN"
        target = tool_call.arguments.get("target", None)

        self.formatter.format_confirmation(
            tool_name=tool_name,
            risk_level=risk,
            target=target,
            arguments=tool_call.arguments,
        )

        try:
            response = await asyncio.get_event_loop().run_in_executor(
                None, lambda: input("\n  Proceed? [y/N]: ").strip().lower()
            )
            return response in ("y", "yes")
        except (EOFError, KeyboardInterrupt):
            return False

    async def handle_command(self, command: str) -> bool:
        """
        Handle inline commands. Returns True if the command was handled.
        """
        parts = command.strip().split(None, 1)
        cmd = parts[0].lower()
        arg = parts[1] if len(parts) > 1 else ""

        if cmd == "/quit":
            self._running = False
            self.console.print("[dim]Goodbye.[/dim]")
            return True

        if cmd == "/history":
            events = await self.orchestrator.session.store.get_events(
                self.orchestrator.session.session_id or ""
            )
            self.formatter.format_session_events(events)
            return True

        if cmd == "/tools":
            tools = self.orchestrator.registry.list()
            self.formatter.format_tool_list(tools)
            return True

        if cmd == "/switch":
            if not arg:
                names = self.orchestrator.router.provider_names
                self.console.print(f"  Available providers: {', '.join(names)}")
                self.console.print(f"  Active: {self.orchestrator.router.active_name}")
            else:
                try:
                    self.orchestrator.router.set_active(arg)
                    self.console.print(f"  Switched to provider: [bold]{arg}[/bold]")
                except KeyError as e:
                    self.console.print(f"  [red]Error:[/red] {e}")
            return True

        if cmd == "/help":
            self.console.print(
                "  [bold]Commands:[/bold]\n"
                "  /quit     - Exit the chat\n"
                "  /history  - Show session events\n"
                "  /tools    - List available tools\n"
                "  /switch   - Switch LLM provider\n"
                "  /help     - Show this help\n"
            )
            return True

        return False

    async def handle_input(self, user_input: str) -> None:
        """Process user input: run through orchestrator and stream response."""
        content_parts: list[str] = []

        try:
            async for chunk in self.orchestrator.run(user_input):
                if chunk.delta:
                    content_parts.append(chunk.delta)
                    # Print incrementally
                    self.console.print(chunk.delta, end="", markup=False)
                if chunk.done:
                    break
        except Exception as e:
            self.console.print(f"\n[red]Error:[/red] {e}")
            return

        # Newline after streaming
        self.console.print()

    async def run_loop(self) -> None:
        """Main interactive loop."""
        self.console.print(
            "[bold]Workbench[/bold] - Support & Diagnostics Assistant\n"
            "[dim]Type /help for commands, /quit to exit.[/dim]\n"
        )

        while self._running:
            try:
                user_input = await asyncio.get_event_loop().run_in_executor(
                    None, lambda: input("you> ").strip()
                )
            except (EOFError, KeyboardInterrupt):
                self.console.print("\n[dim]Goodbye.[/dim]")
                break

            if not user_input:
                continue

            if user_input.startswith("/"):
                handled = await self.handle_command(user_input)
                if handled:
                    continue

            self.console.print("[dim]assistant>[/dim] ", end="")
            await self.handle_input(user_input)
