"""
Workbench TUI -- Textual split-pane terminal interface.

Left pane:  Chat messages (streaming)
Right pane: Tool calls / events / diagnostics
Bottom:     Input area
Header:     Session info, active provider
Footer:     Keybindings
"""

from __future__ import annotations

import asyncio
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from rich.markup import escape
from rich.text import Text
from textual import on, work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import (
    Footer,
    Header,
    Input,
    RichLog,
    Static,
)

from workbench.llm.types import StreamChunk, ToolCall


# ---------------------------------------------------------------------------
# Confirmation modal
# ---------------------------------------------------------------------------

class ConfirmToolScreen(ModalScreen[bool]):
    """Modal dialog for tool call confirmation."""

    BINDINGS = [
        Binding("y", "confirm", "Yes"),
        Binding("n", "deny", "No"),
        Binding("escape", "deny", "Cancel"),
    ]

    DEFAULT_CSS = """
    ConfirmToolScreen {
        align: center middle;
    }
    #confirm-dialog {
        width: 70;
        height: auto;
        max-height: 24;
        border: thick $accent;
        background: $surface;
        padding: 1 2;
    }
    #confirm-title {
        text-style: bold;
        color: $warning;
        margin-bottom: 1;
    }
    #confirm-details {
        margin-bottom: 1;
    }
    #confirm-hint {
        color: $text-muted;
        text-style: italic;
    }
    """

    def __init__(
        self,
        tool_name: str,
        risk: str,
        target: str | None,
        arguments: dict,
    ) -> None:
        super().__init__()
        self.tool_name = tool_name
        self.risk = risk
        self.target = target
        self.arguments = arguments

    def compose(self) -> ComposeResult:
        args_str = json.dumps(self.arguments, indent=2, default=str)
        details = f"[bold]Tool:[/bold]   {self.tool_name}\n"
        details += f"[bold]Risk:[/bold]   {self.risk}\n"
        if self.target:
            details += f"[bold]Target:[/bold] {self.target}\n"
        details += f"[bold]Args:[/bold]\n{args_str}"

        with Vertical(id="confirm-dialog"):
            yield Static("Tool call requires confirmation", id="confirm-title")
            yield Static(details, id="confirm-details")
            yield Static("Press [bold]y[/bold] to confirm, [bold]n[/bold] or [bold]Esc[/bold] to cancel", id="confirm-hint")

    def action_confirm(self) -> None:
        self.dismiss(True)

    def action_deny(self) -> None:
        self.dismiss(False)


# ---------------------------------------------------------------------------
# Main TUI app
# ---------------------------------------------------------------------------

class WorkbenchApp(App):
    """Workbench TUI -- split-pane diagnostics interface."""

    TITLE = "Workbench"
    SUB_TITLE = "Support & Diagnostics"

    BINDINGS = [
        Binding("ctrl+c", "quit", "Quit", priority=True),
        Binding("ctrl+l", "clear_chat", "Clear Chat"),
        Binding("ctrl+t", "show_tools", "Tools"),
    ]

    DEFAULT_CSS = """
    #main-container {
        height: 1fr;
    }
    #chat-pane {
        width: 2fr;
        border-right: solid $accent;
    }
    #events-pane {
        width: 1fr;
    }
    #chat-log {
        height: 1fr;
    }
    #events-log {
        height: 1fr;
    }
    #pane-label-chat {
        dock: top;
        height: 1;
        background: $accent;
        color: $text;
        text-style: bold;
        padding: 0 1;
    }
    #pane-label-events {
        dock: top;
        height: 1;
        background: $accent;
        color: $text;
        text-style: bold;
        padding: 0 1;
    }
    #input-area {
        dock: bottom;
        height: 3;
        padding: 0 1;
    }
    #user-input {
        width: 1fr;
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
        self._pending_confirm: asyncio.Future | None = None

    def compose(self) -> ComposeResult:
        yield Header()
        with Horizontal(id="main-container"):
            with Vertical(id="chat-pane"):
                yield Static(" Chat", id="pane-label-chat")
                yield RichLog(id="chat-log", wrap=True, highlight=True, markup=True)
            with Vertical(id="events-pane"):
                yield Static(" Events", id="pane-label-events")
                yield RichLog(id="events-log", wrap=True, highlight=True, markup=True)
        yield Input(placeholder="Type a message or /help for commands...", id="user-input")
        yield Footer()

    def on_mount(self) -> None:
        chat_log = self.query_one("#chat-log", RichLog)
        chat_log.write("[bold]Workbench[/bold] - Support & Diagnostics")
        chat_log.write("[dim]Type /help for commands, Ctrl+C to quit.[/dim]\n")

        events_log = self.query_one("#events-log", RichLog)
        if self.session and self.session.session_id:
            events_log.write(f"[dim]Session: {self.session.session_id[:8]}...[/dim]")
        if self.router and self.router.active_name:
            events_log.write(f"[dim]Provider: {self.router.active_name}[/dim]")
        events_log.write("")

        self.query_one("#user-input", Input).focus()

    @on(Input.Submitted, "#user-input")
    async def on_input_submitted(self, event: Input.Submitted) -> None:
        user_input = event.value.strip()
        if not user_input:
            return

        input_widget = self.query_one("#user-input", Input)
        input_widget.value = ""

        if user_input.startswith("/"):
            await self._handle_command(user_input)
            return

        chat_log = self.query_one("#chat-log", RichLog)
        chat_log.write(f"[bold blue]you>[/bold blue] {escape(user_input)}")

        if self.orchestrator:
            self._run_orchestrator(user_input)
        else:
            chat_log.write("[red]No orchestrator configured. Chat is unavailable.[/red]")

    @work(thread=False)
    async def _run_orchestrator(self, user_input: str) -> None:
        chat_log = self.query_one("#chat-log", RichLog)
        events_log = self.query_one("#events-log", RichLog)

        chat_log.write("[dim]assistant>[/dim] ", shrink=False)

        content_parts: list[str] = []

        try:
            async for chunk in self.orchestrator.run(user_input):
                if chunk.delta:
                    # Check if this is a tool result notification
                    if chunk.delta.startswith("\n[Tool:"):
                        tool_info = chunk.delta.strip()
                        events_log.write(f"[cyan]{escape(tool_info)}[/cyan]")
                    else:
                        content_parts.append(chunk.delta)
                if chunk.done:
                    break
        except Exception as e:
            chat_log.write(f"[red]Error: {e}[/red]")
            return

        if content_parts:
            full_text = "".join(content_parts)
            chat_log.write(full_text)
        chat_log.write("")

    async def confirm_tool(self, tool_name: str, tool_call: ToolCall) -> bool:
        """Confirmation callback for the orchestrator."""
        tool = self.registry.get(tool_name) if self.registry else None
        risk = tool.risk_level.name if tool else "UNKNOWN"
        target = tool_call.arguments.get("target")

        events_log = self.query_one("#events-log", RichLog)
        events_log.write(f"[yellow]Confirmation needed: {tool_name}[/yellow]")

        result = await self.push_screen_wait(
            ConfirmToolScreen(
                tool_name=tool_name,
                risk=risk,
                target=target,
                arguments=tool_call.arguments,
            )
        )

        confirmed = bool(result)
        status = "[green]confirmed[/green]" if confirmed else "[red]denied[/red]"
        events_log.write(f"  {tool_name}: {status}")

        return confirmed

    async def _handle_command(self, command: str) -> None:
        chat_log = self.query_one("#chat-log", RichLog)
        events_log = self.query_one("#events-log", RichLog)
        parts = command.strip().split(None, 1)
        cmd = parts[0].lower()
        arg = parts[1] if len(parts) > 1 else ""

        if cmd == "/quit":
            self.exit()
            return

        if cmd == "/help":
            chat_log.write(
                "[bold]Commands:[/bold]\n"
                "  /help     - Show this help\n"
                "  /quit     - Exit\n"
                "  /tools    - List available tools\n"
                "  /switch   - Switch LLM provider\n"
                "  /history  - Show recent events\n"
                "  /clear    - Clear chat\n"
            )
            return

        if cmd == "/tools":
            self.action_show_tools()
            return

        if cmd == "/clear":
            self.action_clear_chat()
            return

        if cmd == "/switch":
            if not self.router:
                chat_log.write("[red]No router configured.[/red]")
                return
            if not arg:
                names = self.router.provider_names
                active = self.router.active_name
                chat_log.write(f"  Available: {', '.join(names)}")
                chat_log.write(f"  Active: [bold]{active}[/bold]")
            else:
                try:
                    self.router.set_active(arg)
                    chat_log.write(f"  Switched to: [bold]{arg}[/bold]")
                except KeyError as e:
                    chat_log.write(f"  [red]{e}[/red]")
            return

        if cmd == "/history":
            if not self.session or not self.session.session_id:
                chat_log.write("[red]No active session.[/red]")
                return
            events = await self.session.store.get_events(self.session.session_id)
            for ev in events[-20:]:
                ts = ev.timestamp.strftime("%H:%M:%S") if isinstance(ev.timestamp, datetime) else str(ev.timestamp)
                events_log.write(f"[dim]{ts}[/dim] {ev.event_type}")
            return

        chat_log.write(f"[red]Unknown command: {cmd}[/red]")

    def action_clear_chat(self) -> None:
        chat_log = self.query_one("#chat-log", RichLog)
        chat_log.clear()
        chat_log.write("[dim]Chat cleared.[/dim]\n")

    def action_show_tools(self) -> None:
        events_log = self.query_one("#events-log", RichLog)
        if not self.registry:
            events_log.write("[red]No registry configured.[/red]")
            return
        tools = self.registry.list()
        events_log.write("[bold]Registered Tools:[/bold]")
        for t in tools:
            risk_colors = {"READ_ONLY": "green", "WRITE": "yellow", "DESTRUCTIVE": "red", "SHELL": "bold red"}
            color = risk_colors.get(t.risk_level.name, "white")
            events_log.write(f"  [{color}]{t.risk_level.name:10s}[/{color}] {t.name} - {t.description[:50]}")
        events_log.write("")


# ---------------------------------------------------------------------------
# Launcher
# ---------------------------------------------------------------------------

async def launch_tui(
    provider: str | None = None,
    profile: str | None = None,
    session_id: str | None = None,
) -> None:
    """Set up the full stack and launch the TUI."""
    from workbench.config import load_config
    from workbench.llm.router import LLMRouter
    from workbench.llm.token_counter import TokenCounter
    from workbench.orchestrator.core import Orchestrator
    from workbench.prompts.system import build_system_prompt
    from workbench.session.artifacts import ArtifactStore
    from workbench.session.session import Session
    from workbench.session.store import SessionStore
    from workbench.tools.base import ToolRisk
    from workbench.tools.policy import PolicyEngine
    from workbench.tools.registry import ToolRegistry

    # Find config
    config_path = None
    for candidate in [
        Path.cwd() / "workbench.yaml",
        Path.cwd() / "workbench.yml",
        Path.home() / ".config" / "workbench" / "config.yaml",
        Path.home() / ".workbench" / "config.yaml",
    ]:
        if candidate.is_file():
            config_path = candidate
            break

    cfg = load_config(config_path, profile=profile)

    # Session store
    store = SessionStore(cfg.session.history_db)
    await store.init()

    # Artifact store
    artifact_dir = Path(cfg.policy.audit_log_path).parent / "artifacts"
    artifact_store = ArtifactStore(str(artifact_dir))

    # Token counter + session
    token_counter = TokenCounter(cfg.llm.model)
    session = Session(store, artifact_store, token_counter)
    if session_id:
        await session.resume(session_id)
    else:
        await session.start({"profile": profile or "default", "interface": "tui"})

    # Tool registry
    registry = ToolRegistry()
    registry.load_plugins(
        enabled=cfg.plugins.enabled,
        allow_distributions=set(cfg.plugins.allow_distributions) if cfg.plugins.allow_distributions else None,
        allow_tools=set(cfg.plugins.allow_tools) if cfg.plugins.allow_tools else None,
    )

    # Demo backend bridge tools
    try:
        from workbench.backends.bridge import (
            ListDiagnosticsTool,
            ResolveTargetTool,
            RunDiagnosticTool,
            SummarizeArtifactTool,
        )
        from workbench.backends.demo import DemoBackend

        backend = DemoBackend()
        registry.register(ResolveTargetTool(backend))
        registry.register(ListDiagnosticsTool(backend))
        registry.register(RunDiagnosticTool(backend))
        registry.register(SummarizeArtifactTool(artifact_store))
    except Exception:
        pass

    # Policy
    risk_map = {r.name: r for r in ToolRisk}
    max_risk = risk_map.get(cfg.policy.max_risk, ToolRisk.READ_ONLY)
    policy = PolicyEngine(
        max_risk=max_risk,
        confirm_destructive=cfg.policy.confirm_destructive,
        confirm_shell=cfg.policy.confirm_shell,
        confirm_write=cfg.policy.confirm_write,
        blocked_patterns=cfg.policy.blocked_patterns,
        redaction_patterns=cfg.policy.redaction_patterns,
        audit_log_path=cfg.policy.audit_log_path,
        audit_max_size_mb=cfg.policy.audit_max_size_mb,
        audit_keep_files=cfg.policy.audit_keep_files,
    )

    # LLM Router
    router = LLMRouter()
    try:
        from workbench.llm.providers.openai_compat import OpenAICompatProvider

        api_key = os.environ.get(cfg.llm.api_key_env, "not-needed")
        llm_provider = OpenAICompatProvider(
            url=cfg.llm.api_base or "http://localhost:3333/v1",
            model=cfg.llm.model,
            api_key=api_key,
            timeout=float(cfg.llm.timeout_seconds),
        )
        router.register_provider(cfg.llm.name, llm_provider)
    except Exception:
        pass

    if provider:
        try:
            router.set_active(provider)
        except KeyError:
            pass

    # System prompt
    system_prompt = build_system_prompt(tools=registry.list())

    # Build app first so we can use its confirm method
    tui_app = WorkbenchApp(
        orchestrator=None,
        session=session,
        router=router,
        registry=registry,
    )

    # Orchestrator with TUI confirmation callback
    orchestrator = Orchestrator(
        session=session,
        registry=registry,
        router=router,
        policy=policy,
        system_prompt=system_prompt,
        tool_timeout=float(cfg.llm.timeout_seconds),
        max_turns=cfg.session.max_turns,
        confirmation_callback=tui_app.confirm_tool,
    )

    tui_app.orchestrator = orchestrator

    try:
        await tui_app.run_async()
    finally:
        await store.close()
