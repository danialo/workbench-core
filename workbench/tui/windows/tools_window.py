"""Tools browser window content for the Workbench TUI."""

from __future__ import annotations

import json
from typing import Any

from textual.app import ComposeResult
from textual.containers import Vertical
from textual.widgets import DataTable, RichLog

from workbench.tui.context_menu import MenuItem
from workbench.tui.menu_bar import MenuAction, MenuSection


class ToolsWindowContent(Vertical):
    """Browse and inspect registered tools.

    Displays a DataTable of tools with name, risk level, and description.
    Selecting a row shows the full JSON schema in a detail pane below.
    """

    DEFAULT_CSS = """
    ToolsWindowContent {
        width: 1fr;
        height: 1fr;
    }
    ToolsWindowContent > #tools-table {
        height: 2fr;
    }
    ToolsWindowContent > #tool-detail {
        height: 1fr;
        border-top: solid $accent;
    }
    """

    def __init__(self, registry: Any = None, **kwargs) -> None:
        super().__init__(**kwargs)
        self.registry = registry
        self._current_filter: str | None = None

    def compose(self) -> ComposeResult:
        yield DataTable(id="tools-table")
        yield RichLog(id="tool-detail", wrap=True, highlight=True, markup=True)

    def on_mount(self) -> None:
        table = self.query_one("#tools-table", DataTable)
        table.add_columns("Name", "Risk", "Description")
        table.cursor_type = "row"
        self._populate_table()

    def _populate_table(self) -> None:
        table = self.query_one("#tools-table", DataTable)
        table.clear()
        if not self.registry:
            return
        tools = self.registry.list()
        for t in tools:
            if self._current_filter and t.risk_level.name != self._current_filter:
                continue
            risk_display = t.risk_level.name
            table.add_row(t.name, risk_display, t.description[:60], key=t.name)

    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        """Show tool schema when a row is selected."""
        detail = self.query_one("#tool-detail", RichLog)
        detail.clear()
        if not self.registry or event.row_key is None:
            return
        tool = self.registry.get(str(event.row_key.value))
        if tool is None:
            return
        detail.write(f"[bold]{tool.name}[/bold]")
        detail.write(f"Risk: {tool.risk_level.name}")
        detail.write(f"Privacy: {tool.privacy_scope.value}")
        detail.write(f"Description: {tool.description}")
        detail.write(f"\n[bold]Parameters:[/bold]")
        detail.write(json.dumps(tool.parameters, indent=2))

    def refresh_tools(self) -> None:
        self._current_filter = None
        self._populate_table()

    def filter_read_only(self) -> None:
        self._current_filter = "READ_ONLY"
        self._populate_table()

    def filter_write(self) -> None:
        self._current_filter = "WRITE"
        self._populate_table()

    def filter_all(self) -> None:
        self._current_filter = None
        self._populate_table()

    # -- Menu protocol ---------------------------------------------------------

    def get_context_menu_items(self) -> list[MenuItem]:
        return [
            MenuItem("Refresh", callback=self.refresh_tools),
            MenuItem(separator=True),
            MenuItem("Show All", callback=self.filter_all),
            MenuItem("Read Only", callback=self.filter_read_only),
            MenuItem("Write+", callback=self.filter_write),
        ]

    def get_menu_bar_sections(self) -> list[MenuSection]:
        return [
            MenuSection("Tools", [
                MenuAction("Refresh", callback=self.refresh_tools),
                MenuAction(separator=True),
                MenuAction("Filter: All", callback=self.filter_all),
                MenuAction("Filter: Read Only", callback=self.filter_read_only),
                MenuAction("Filter: Write+", callback=self.filter_write),
            ]),
        ]
