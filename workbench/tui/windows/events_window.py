"""Events window content for the Workbench TUI."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from textual.app import ComposeResult
from textual.containers import Vertical
from textual.widgets import RichLog

from workbench.tui.context_menu import MenuItem
from workbench.tui.menu_bar import MenuAction, MenuSection


class EventsWindowContent(Vertical):
    """Session events log viewer.

    Displays tool calls, confirmations, provider changes, and other
    session events in a scrolling RichLog.
    """

    DEFAULT_CSS = """
    EventsWindowContent {
        width: 1fr;
        height: 1fr;
    }
    EventsWindowContent > #events-log {
        height: 1fr;
    }
    """

    def __init__(
        self,
        session: Any = None,
        router: Any = None,
        registry: Any = None,
        **kwargs,
    ) -> None:
        super().__init__(**kwargs)
        self.session = session
        self.router = router
        self.registry = registry

    def compose(self) -> ComposeResult:
        yield RichLog(id="events-log", wrap=True, highlight=True, markup=True)

    def on_mount(self) -> None:
        log = self.query_one("#events-log", RichLog)
        if self.session and self.session.session_id:
            log.write(f"[dim]Session: {self.session.session_id[:8]}...[/dim]")
        if self.router and self.router.active_name:
            log.write(f"[dim]Provider: {self.router.active_name}[/dim]")
        log.write("")

    def write_event(self, text: str) -> None:
        """Append an event line to the log."""
        log = self.query_one("#events-log", RichLog)
        log.write(text)

    def clear_events(self) -> None:
        log = self.query_one("#events-log", RichLog)
        log.clear()
        log.write("[dim]Events cleared.[/dim]\n")

    async def show_history(self) -> None:
        """Load and display recent session events."""
        log = self.query_one("#events-log", RichLog)
        if not self.session or not self.session.session_id:
            log.write("[red]No active session.[/red]")
            return
        events = await self.session.store.get_events(self.session.session_id)
        for ev in events[-20:]:
            ts = ev.timestamp.strftime("%H:%M:%S") if isinstance(ev.timestamp, datetime) else str(ev.timestamp)
            log.write(f"[dim]{ts}[/dim] {ev.event_type}")

    def show_tools(self) -> None:
        """Display registered tools in the events log."""
        log = self.query_one("#events-log", RichLog)
        if not self.registry:
            log.write("[red]No registry configured.[/red]")
            return
        tools = self.registry.list()
        log.write("[bold]Registered Tools:[/bold]")
        risk_colors = {"READ_ONLY": "green", "WRITE": "yellow", "DESTRUCTIVE": "red", "SHELL": "bold red"}
        for t in tools:
            color = risk_colors.get(t.risk_level.name, "white")
            log.write(f"  [{color}]{t.risk_level.name:10s}[/{color}] {t.name} - {t.description[:50]}")
        log.write("")

    # -- Menu protocol ---------------------------------------------------------

    def get_context_menu_items(self) -> list[MenuItem]:
        return [
            MenuItem("Clear Events", callback=self.clear_events),
            MenuItem("Show History", callback=self.show_history),
            MenuItem(separator=True),
            MenuItem("Show Tools", callback=self.show_tools),
        ]

    def get_menu_bar_sections(self) -> list[MenuSection]:
        return [
            MenuSection("Events", [
                MenuAction("Clear", callback=self.clear_events),
                MenuAction("Show History", callback=self.show_history),
                MenuAction(separator=True),
                MenuAction("Show Tools", callback=self.show_tools),
            ]),
        ]
