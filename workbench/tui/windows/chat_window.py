"""Chat window content for the Workbench TUI."""

from __future__ import annotations

from typing import Any, Callable

from rich.markup import escape
from textual import on, work
from textual.app import ComposeResult
from textual.containers import Vertical
from textual.widgets import Input, RichLog

from workbench.tui.context_menu import MenuItem
from workbench.tui.menu_bar import MenuAction, MenuSection


class ChatWindowContent(Vertical):
    """Chat interface with streaming LLM output.

    Contains a RichLog for messages and an Input for user text.
    Meant to be placed inside a Window widget.
    """

    DEFAULT_CSS = """
    ChatWindowContent {
        width: 1fr;
        height: 1fr;
    }
    ChatWindowContent > #chat-log {
        height: 1fr;
    }
    ChatWindowContent > #chat-input {
        dock: bottom;
        height: 3;
        padding: 0 1;
    }
    """

    def __init__(
        self,
        orchestrator: Any = None,
        session: Any = None,
        router: Any = None,
        registry: Any = None,
        **kwargs,
    ) -> None:
        super().__init__(**kwargs)
        self.orchestrator = orchestrator
        self.session = session
        self.router = router
        self.registry = registry

    def compose(self) -> ComposeResult:
        yield RichLog(id="chat-log", wrap=True, highlight=True, markup=True)
        yield Input(placeholder="Type a message...", id="chat-input")

    def on_mount(self) -> None:
        log = self.query_one("#chat-log", RichLog)
        log.write("[bold]Chat[/bold] - Type a message to begin.")
        log.write("[dim]Use /help for commands.[/dim]\n")
        self.query_one("#chat-input", Input).focus()

    @on(Input.Submitted, "#chat-input")
    async def on_input_submitted(self, event: Input.Submitted) -> None:
        user_input = event.value.strip()
        if not user_input:
            return

        input_widget = self.query_one("#chat-input", Input)
        input_widget.value = ""

        if user_input.startswith("/"):
            await self._handle_command(user_input)
            return

        log = self.query_one("#chat-log", RichLog)
        log.write(f"[bold blue]you>[/bold blue] {escape(user_input)}")

        if self.orchestrator:
            self._run_orchestrator(user_input)
        else:
            log.write("[red]No orchestrator configured.[/red]")

    @work(thread=False)
    async def _run_orchestrator(self, user_input: str) -> None:
        log = self.query_one("#chat-log", RichLog)
        log.write("[dim]assistant>[/dim] ", shrink=False)

        content_parts: list[str] = []
        try:
            async for chunk in self.orchestrator.run(user_input):
                if chunk.delta:
                    if chunk.delta.startswith("\n[Tool:"):
                        pass  # Tool results go to events window
                    else:
                        content_parts.append(chunk.delta)
                if chunk.done:
                    break
        except Exception as e:
            log.write(f"[red]Error: {e}[/red]")
            return

        if content_parts:
            log.write("".join(content_parts))
        log.write("")

    async def _handle_command(self, command: str) -> None:
        log = self.query_one("#chat-log", RichLog)
        parts = command.strip().split(None, 1)
        cmd = parts[0].lower()

        if cmd == "/help":
            log.write(
                "[bold]Commands:[/bold]\n"
                "  /help     - Show this help\n"
                "  /clear    - Clear chat\n"
                "  /switch   - Switch LLM provider\n"
            )
        elif cmd == "/clear":
            self.clear_chat()
        elif cmd == "/switch":
            arg = parts[1] if len(parts) > 1 else ""
            if not self.router:
                log.write("[red]No router configured.[/red]")
            elif not arg:
                names = self.router.provider_names
                active = self.router.active_name
                log.write(f"  Available: {', '.join(names)}")
                log.write(f"  Active: [bold]{active}[/bold]")
            else:
                try:
                    self.router.set_active(arg)
                    log.write(f"  Switched to: [bold]{arg}[/bold]")
                except KeyError as e:
                    log.write(f"  [red]{e}[/red]")
        else:
            log.write(f"[red]Unknown command: {cmd}[/red]")

    def clear_chat(self) -> None:
        log = self.query_one("#chat-log", RichLog)
        log.clear()
        log.write("[dim]Chat cleared.[/dim]\n")

    # -- Menu protocol ---------------------------------------------------------

    def get_context_menu_items(self) -> list[MenuItem]:
        return [
            MenuItem("Clear Chat", callback=self.clear_chat),
            MenuItem(separator=True),
            MenuItem("Focus Input", callback=lambda: self.query_one("#chat-input", Input).focus()),
        ]

    def get_menu_bar_sections(self) -> list[MenuSection]:
        return [
            MenuSection("Chat", [
                MenuAction("Clear", callback=self.clear_chat, hotkey_hint="Ctrl+L"),
                MenuAction(separator=True),
                MenuAction("Switch Provider", callback=lambda: self._handle_command("/switch")),
            ]),
        ]
