"""
Workbench TUI -- Textual windowed desktop interface.

Desktop-style workspace where every UI element (chat, events, tools,
config, artifacts) is an independent, movable window with minimize/
restore/maximize, contextual menus, snap-to-grid, and keyboard tiling.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from pathlib import Path
from typing import Any

from textual import on
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import Footer, Header, Static

# Debug logging — writes to ~/.workbench/tui.log
_log_dir = Path.home() / ".workbench"
_log_dir.mkdir(parents=True, exist_ok=True)
_log = logging.getLogger("workbench.tui")
_log.setLevel(logging.DEBUG)
_fh = logging.FileHandler(_log_dir / "tui.log", mode="w")
_fh.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
_log.addHandler(_fh)

from workbench.llm.types import ToolCall
from workbench.tui.context_menu import ContextMenu, MenuItem
from workbench.tui.menu_bar import MenuBar, MenuSection, MenuAction
from workbench.tui.window import Window, WindowState
from workbench.tui.window_manager import WindowManager, WindowKind
from workbench.tui.windows.chat_window import ChatWindowContent
from workbench.tui.windows.events_window import EventsWindowContent
from workbench.tui.windows.tools_window import ToolsWindowContent
from workbench.tui.windows.config_window import ConfigWindowContent
from workbench.tui.windows.artifacts_window import ArtifactsWindowContent


# ---------------------------------------------------------------------------
# Confirmation modal (preserved from original)
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
    """Workbench TUI -- windowed desktop diagnostics interface."""

    TITLE = "Workbench"
    SUB_TITLE = "Support & Diagnostics"

    BINDINGS = [
        Binding("ctrl+c", "quit", "Quit", priority=True),
        Binding("ctrl+n", "new_chat", "New Chat", priority=True),
        Binding("ctrl+w", "close_window", "Close Win", priority=True),
        Binding("ctrl+l", "clear_chat", "Clear Chat", priority=True),
        Binding("ctrl+t", "open_tools", "Tools", priority=True),
        Binding("f5", "cascade", "Cascade"),
        Binding("f6", "tile_grid", "Tile Grid"),
        Binding("f7", "cycle_windows", "Next Win"),
        Binding("f10", "context_menu", "Menu"),
    ]

    DEFAULT_CSS = """
    Screen {
        layers: default windows context-menu menu-dropdown;
    }
    """

    def __init__(
        self,
        orchestrator: Any = None,
        session: Any = None,
        router: Any = None,
        registry: Any = None,
        config: Any = None,
        artifact_store: Any = None,
        **kwargs,
    ) -> None:
        super().__init__(**kwargs)
        self.orchestrator = orchestrator
        self.session = session
        self.router = router
        self.registry = registry
        self.config = config
        self.artifact_store = artifact_store
        self._window_counter = 0
        self._events_content: EventsWindowContent | None = None

    def compose(self) -> ComposeResult:
        yield Header()
        yield MenuBar(id="menu-bar")
        yield WindowManager(id="wm")
        yield Footer()

    def on_mount(self) -> None:
        _log.info("WorkbenchApp mounted")
        # Open default windows: Chat + Events side by side
        self._open_chat_window(offset=(0, 0))
        self._open_events_window(offset=(62, 0))
        _log.info("Default windows opened (Chat + Events)")

    def on_key(self, event) -> None:
        """Log all key events for debugging."""
        _log.debug("KEY: key=%r character=%r", event.key, event.character)

    # -- Window creation helpers -----------------------------------------------

    def _next_window_id(self, prefix: str) -> str:
        self._window_counter += 1
        return f"{prefix}-{self._window_counter}"

    def _get_wm(self) -> WindowManager:
        return self.query_one("#wm", WindowManager)

    def _open_chat_window(self, offset: tuple[int, int] | None = None) -> Window:
        content = ChatWindowContent(
            orchestrator=self.orchestrator,
            session=self.session,
            router=self.router,
            registry=self.registry,
        )
        win = Window(content, title="Chat", id=self._next_window_id("chat"))
        win.get_context_menu_items = content.get_context_menu_items
        win.get_menu_bar_sections = content.get_menu_bar_sections
        self._get_wm().open_window(win, offset=offset)
        return win

    def _open_events_window(self, offset: tuple[int, int] | None = None) -> Window:
        content = EventsWindowContent(
            session=self.session,
            router=self.router,
            registry=self.registry,
        )
        self._events_content = content
        win = Window(content, title="Events", id=self._next_window_id("events"))
        win.get_context_menu_items = content.get_context_menu_items
        win.get_menu_bar_sections = content.get_menu_bar_sections
        self._get_wm().open_window(win, offset=offset)
        return win

    def _open_tools_window(self, offset: tuple[int, int] | None = None) -> Window:
        content = ToolsWindowContent(registry=self.registry)
        win = Window(content, title="Tools", id=self._next_window_id("tools"))
        win.get_context_menu_items = content.get_context_menu_items
        win.get_menu_bar_sections = content.get_menu_bar_sections
        self._get_wm().open_window(win, offset=offset)
        return win

    def _open_config_window(self, offset: tuple[int, int] | None = None) -> Window:
        content = ConfigWindowContent(config=self.config)
        win = Window(content, title="Config", id=self._next_window_id("config"))
        win.get_context_menu_items = content.get_context_menu_items
        win.get_menu_bar_sections = content.get_menu_bar_sections
        self._get_wm().open_window(win, offset=offset)
        return win

    def _open_artifacts_window(self, offset: tuple[int, int] | None = None) -> Window:
        content = ArtifactsWindowContent(artifact_store=self.artifact_store)
        win = Window(content, title="Artifacts", id=self._next_window_id("artifacts"))
        win.get_context_menu_items = content.get_context_menu_items
        win.get_menu_bar_sections = content.get_menu_bar_sections
        self._get_wm().open_window(win, offset=offset)
        return win

    # -- Active window tracking / menu bar updates -----------------------------

    @on(WindowManager.ActiveWindowChanged)
    def _on_active_window_changed(self, event: WindowManager.ActiveWindowChanged) -> None:
        event.stop()
        win_id = event.window.id if event.window else None
        _log.info("ActiveWindowChanged: %s", win_id)
        menu_bar = self.query_one("#menu-bar", MenuBar)
        if event.window is not None:
            try:
                sections = event.window.get_menu_bar_sections()
                _log.debug("Dynamic sections from window: %s", [s.name if hasattr(s, 'name') else s[0] for s in sections] if sections else [])
                menu_bar.set_dynamic_sections(
                    [MenuSection(s[0], [MenuAction(label=a[0], callback=a[1]) for a in s[1]])
                     for s in sections]
                    if sections and isinstance(sections[0], tuple) else sections
                )
            except Exception as e:
                _log.exception("Error updating dynamic sections: %s", e)
                menu_bar.set_dynamic_sections([])
        else:
            menu_bar.set_dynamic_sections([])

    # -- Menu bar action handler -----------------------------------------------

    @on(MenuBar.ActionSelected)
    def _on_menu_action(self, event: MenuBar.ActionSelected) -> None:
        event.stop()
        label = event.label
        _log.info("MenuBar action selected: section=%s label=%s", event.section, label)

        # Window menu actions
        if label == "New Chat":
            self.action_new_chat()
        elif label == "Close Window":
            self.action_close_window()
        elif label == "Cascade":
            self.action_cascade()
        elif label == "Tile Horizontal":
            self._get_wm().tile_horizontal()
        elif label == "Tile Vertical":
            self._get_wm().tile_vertical()
        elif label == "Tile Grid":
            self.action_tile_grid()
        elif label == "Toggle Taskbar":
            self._get_wm().toggle_taskbar()
        # Callback-based actions are handled by the MenuBar itself

    # -- Keybinding actions ----------------------------------------------------

    def action_new_chat(self) -> None:
        _log.info("action_new_chat")
        n = len(self._get_wm().windows)
        self._open_chat_window(offset=(n * 4, n * 2))

    def action_close_window(self) -> None:
        _log.info("action_close_window")
        wm = self._get_wm()
        if wm.active_window and wm.active_window.id:
            wm.close_window(wm.active_window.id)

    def action_clear_chat(self) -> None:
        _log.info("action_clear_chat")
        wm = self._get_wm()
        if wm.active_window:
            try:
                content = wm.active_window.query_one(ChatWindowContent)
                content.clear_chat()
            except Exception:
                pass

    def action_open_tools(self) -> None:
        _log.info("action_open_tools")
        n = len(self._get_wm().windows)
        self._open_tools_window(offset=(n * 4, n * 2))

    def action_cascade(self) -> None:
        _log.info("action_cascade")
        self._get_wm().cascade()

    def action_tile_grid(self) -> None:
        _log.info("action_tile_grid")
        self._get_wm().tile_grid()

    def action_cycle_windows(self) -> None:
        _log.info("action_cycle_windows")
        wm = self._get_wm()
        visible = wm.visible_windows
        if len(visible) < 2:
            return
        active = wm.active_window
        if active in visible:
            idx = visible.index(active)
            next_win = visible[(idx + 1) % len(visible)]
        else:
            next_win = visible[0]
        if next_win.id:
            wm.bring_to_front(next_win.id)

    def action_context_menu(self) -> None:
        """Open context menu for the active window."""
        wm = self._get_wm()
        if wm.active_window is None:
            return

        items = [
            MenuItem("Minimize", callback=wm.active_window.minimize),
            MenuItem("Maximize", callback=wm.active_window.toggle_maximize),
            MenuItem("Close", callback=lambda: self.action_close_window()),
            MenuItem(separator=True),
        ]

        # Add window-specific items
        try:
            window_items = wm.active_window.get_context_menu_items()
            if window_items:
                items.extend(window_items)
        except Exception:
            pass

        items.extend([
            MenuItem(separator=True),
            MenuItem("Cascade All", callback=self.action_cascade),
            MenuItem("Tile Grid", callback=self.action_tile_grid),
        ])

        menu = ContextMenu(items=items, position=(10, 3))
        self.mount(menu)

    # -- Tool confirmation callback (for orchestrator) -------------------------

    async def confirm_tool(self, tool_name: str, tool_call: ToolCall) -> bool:
        """Confirmation callback for the orchestrator."""
        tool = self.registry.get(tool_name) if self.registry else None
        risk = tool.risk_level.name if tool else "UNKNOWN"
        target = tool_call.arguments.get("target")

        if self._events_content:
            self._events_content.write_event(f"[yellow]Confirmation needed: {tool_name}[/yellow]")

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
        if self._events_content:
            self._events_content.write_event(f"  {tool_name}: {status}")

        return confirmed


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
        config=cfg,
        artifact_store=artifact_store,
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
