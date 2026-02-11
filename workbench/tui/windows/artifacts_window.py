"""Artifacts browser window content for the Workbench TUI."""

from __future__ import annotations

import os
from typing import Any

from textual.app import ComposeResult
from textual.containers import Vertical
from textual.widgets import DataTable, RichLog

from workbench.tui.context_menu import MenuItem
from workbench.tui.menu_bar import MenuAction, MenuSection


class ArtifactsWindowContent(Vertical):
    """Browse stored artifacts.

    Displays a DataTable of artifacts with hash, name, size, and type.
    Selecting a row shows content preview in a detail pane below.
    """

    DEFAULT_CSS = """
    ArtifactsWindowContent {
        width: 1fr;
        height: 1fr;
    }
    ArtifactsWindowContent > #artifacts-table {
        height: 2fr;
    }
    ArtifactsWindowContent > #artifact-detail {
        height: 1fr;
        border-top: solid $accent;
    }
    """

    def __init__(self, artifact_store: Any = None, **kwargs) -> None:
        super().__init__(**kwargs)
        self.artifact_store = artifact_store

    def compose(self) -> ComposeResult:
        yield DataTable(id="artifacts-table")
        yield RichLog(id="artifact-detail", wrap=True, highlight=True, markup=True)

    def on_mount(self) -> None:
        table = self.query_one("#artifacts-table", DataTable)
        table.add_columns("SHA256 (short)", "Name", "Size", "Type")
        table.cursor_type = "row"
        self.refresh_artifacts()

    def refresh_artifacts(self) -> None:
        """Scan the artifact store and populate the table."""
        table = self.query_one("#artifacts-table", DataTable)
        table.clear()
        if self.artifact_store is None:
            return

        base = self.artifact_store.base_dir
        if not base.exists():
            return

        for subdir in sorted(base.iterdir()):
            if not subdir.is_dir() or len(subdir.name) != 2:
                continue
            for artifact_file in sorted(subdir.iterdir()):
                if artifact_file.is_file() and not artifact_file.suffix:
                    sha = artifact_file.name
                    size = artifact_file.stat().st_size
                    size_str = self._format_size(size)
                    table.add_row(
                        sha[:12] + "...",
                        artifact_file.name,
                        size_str,
                        "binary",
                        key=sha,
                    )

    @staticmethod
    def _format_size(size: int) -> str:
        for unit in ("B", "KB", "MB", "GB"):
            if size < 1024:
                return f"{size:.0f} {unit}"
            size /= 1024
        return f"{size:.1f} TB"

    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        """Show artifact preview when a row is selected."""
        detail = self.query_one("#artifact-detail", RichLog)
        detail.clear()
        if self.artifact_store is None or event.row_key is None:
            return

        sha = str(event.row_key.value)
        path = self.artifact_store._artifact_path(sha)

        if not path.exists():
            detail.write(f"[red]Artifact not found: {sha}[/red]")
            return

        detail.write(f"[bold]SHA256:[/bold] {sha}")
        detail.write(f"[bold]Path:[/bold] {path}")
        detail.write(f"[bold]Size:[/bold] {self._format_size(path.stat().st_size)}")

        # Try to show content preview (first 500 bytes as text)
        try:
            raw = path.read_bytes()[:500]
            text = raw.decode("utf-8", errors="replace")
            detail.write(f"\n[bold]Preview:[/bold]\n{text}")
        except Exception as e:
            detail.write(f"\n[dim]Cannot preview: {e}[/dim]")

    # -- Menu protocol ---------------------------------------------------------

    def get_context_menu_items(self) -> list[MenuItem]:
        return [
            MenuItem("Refresh", callback=self.refresh_artifacts),
        ]

    def get_menu_bar_sections(self) -> list[MenuSection]:
        return [
            MenuSection("Artifacts", [
                MenuAction("Refresh", callback=self.refresh_artifacts),
            ]),
        ]
