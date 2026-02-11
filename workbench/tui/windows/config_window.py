"""Config viewer window content for the Workbench TUI."""

from __future__ import annotations

import json
from dataclasses import asdict
from typing import Any

from textual.app import ComposeResult
from textual.containers import Vertical
from textual.widgets import RichLog

from workbench.tui.context_menu import MenuItem
from workbench.tui.menu_bar import MenuAction, MenuSection


class ConfigWindowContent(Vertical):
    """Display the current effective configuration.

    Shows a pretty-printed JSON view of the WorkbenchConfig dataclass.
    """

    DEFAULT_CSS = """
    ConfigWindowContent {
        width: 1fr;
        height: 1fr;
    }
    ConfigWindowContent > #config-log {
        height: 1fr;
    }
    """

    def __init__(self, config: Any = None, **kwargs) -> None:
        super().__init__(**kwargs)
        self.config = config

    def compose(self) -> ComposeResult:
        yield RichLog(id="config-log", wrap=True, highlight=True, markup=True)

    def on_mount(self) -> None:
        self.show_config()

    def show_config(self) -> None:
        log = self.query_one("#config-log", RichLog)
        log.clear()
        log.write("[bold]Effective Configuration[/bold]\n")
        if self.config is None:
            log.write("[red]No configuration loaded.[/red]")
            return
        try:
            d = self.config.to_dict() if hasattr(self.config, "to_dict") else asdict(self.config)
            formatted = json.dumps(d, indent=2, default=str)
            log.write(formatted)
        except Exception as e:
            log.write(f"[red]Error displaying config: {e}[/red]")

    def reload_config(self) -> None:
        self.show_config()

    # -- Menu protocol ---------------------------------------------------------

    def get_context_menu_items(self) -> list[MenuItem]:
        return [
            MenuItem("Reload", callback=self.reload_config),
        ]

    def get_menu_bar_sections(self) -> list[MenuSection]:
        return [
            MenuSection("Config", [
                MenuAction("Reload", callback=self.reload_config),
            ]),
        ]
