"""Chat window content for the Workbench TUI."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Callable

from rich.markup import escape
from textual import on, work
from textual.app import ComposeResult
from textual.containers import Vertical
from textual.widgets import Input, RichLog

from workbench.tui.context_menu import MenuItem
from workbench.tui.menu_bar import MenuAction, MenuSection

_log = logging.getLogger("workbench.tui.chat")

_COPY_FILE = Path.home() / ".workbench" / "last_response.txt"


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
        self._last_response: str = ""
        self._chat_history: list[str] = []

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
        self._chat_history.append(f"you> {user_input}")

        if self.orchestrator:
            self._run_orchestrator(user_input)
        else:
            log.write("[red]No orchestrator configured.[/red]")

    @work(thread=False)
    async def _run_orchestrator(self, user_input: str) -> None:
        """Run the orchestrator loop as an asyncio task on Textual's event loop.

        thread=False is critical: the session store uses asyncio.Lock bound to
        this loop, and aiosqlite connections are loop-bound.  Using thread=True
        would create a separate event loop and deadlock on the first DB write.
        """
        _log.info("orchestrator start: %s", user_input[:80])
        log = self.query_one("#chat-log", RichLog)

        content_parts: list[str] = []
        chunk_count = 0
        try:
            async for chunk in self.orchestrator.run(user_input):
                chunk_count += 1
                if chunk.delta:
                    _log.debug(
                        "chunk %d: delta=%r done=%s",
                        chunk_count,
                        chunk.delta[:120],
                        chunk.done,
                    )
                    content_parts.append(chunk.delta)
                if chunk.done:
                    _log.info("chunk %d: DONE", chunk_count)
                    break
        except Exception as e:
            _log.exception("orchestrator error: %s", e)
            log.write(f"[red]Error: {escape(str(e))}[/red]")
            return

        _log.info(
            "orchestrator finished: %d chunks, %d content parts",
            chunk_count,
            len(content_parts),
        )

        if content_parts:
            full_text = "".join(content_parts)
            self._last_response = full_text
            self._chat_history.append(f"assistant> {full_text}")
            log.write(f"[dim]assistant>[/dim]\n{escape(full_text)}")
        else:
            log.write(
                "[dim]assistant>[/dim] [yellow](no response)[/yellow]"
            )

    async def _handle_command(self, command: str) -> None:
        log = self.query_one("#chat-log", RichLog)
        parts = command.strip().split(None, 1)
        cmd = parts[0].lower()

        if cmd == "/help":
            log.write(
                "[bold]Commands:[/bold]\n"
                "  /help     - Show this help\n"
                "  /clear    - Clear chat\n"
                "  /copy     - Copy last response to file + clipboard\n"
                "  /save     - Save full chat to file\n"
                "  /switch   - Switch LLM provider\n"
            )
        elif cmd == "/copy":
            self._copy_last_response()
        elif cmd == "/save":
            save_path = parts[1].strip() if len(parts) > 1 else str(Path.home() / ".workbench" / "chat_log.txt")
            self._save_chat(save_path)
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

    def _copy_last_response(self) -> None:
        """Copy last assistant response to file and attempt clipboard."""
        log = self.query_one("#chat-log", RichLog)
        if not self._last_response:
            log.write("[yellow]No response to copy.[/yellow]")
            return
        _COPY_FILE.parent.mkdir(parents=True, exist_ok=True)
        _COPY_FILE.write_text(self._last_response)
        log.write(f"[green]Response saved to {_COPY_FILE}[/green]")
        try:
            import subprocess
            subprocess.run(
                ["xclip", "-selection", "clipboard"],
                input=self._last_response.encode(),
                timeout=2,
                capture_output=True,
            )
            log.write("[green]Copied to clipboard.[/green]")
        except Exception:
            pass

    def _save_chat(self, path: str) -> None:
        """Save full chat history to a file."""
        log = self.query_one("#chat-log", RichLog)
        if not self._chat_history:
            log.write("[yellow]No chat history to save.[/yellow]")
            return
        save_path = Path(path)
        save_path.parent.mkdir(parents=True, exist_ok=True)
        save_path.write_text("\n\n".join(self._chat_history) + "\n")
        log.write(f"[green]Chat saved to {save_path}[/green]")

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
