"""
Context menu widget for the Workbench TUI.

Provides a popup context menu with keyboard and mouse navigation,
separator support, disabled items, and hotkey hints.

Usage:
    menu = ContextMenu(
        items=[
            MenuItem("Copy", callback=do_copy, hotkey_hint="Ctrl+C"),
            MenuItem(separator=True),
            MenuItem("Delete", callback=do_delete, hotkey_hint="Del"),
        ],
        position=(10, 5),
    )
    await self.mount(menu)
    # Listen for ContextMenu.ItemSelected / ContextMenu.Closed messages.
"""

from __future__ import annotations

import asyncio
import inspect
from dataclasses import dataclass
from typing import Awaitable, Callable

from textual import events, on
from textual.app import ComposeResult
from textual.binding import Binding
from textual.message import Message
from textual.reactive import reactive
from textual.widget import Widget
from textual.widgets import Static


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------

@dataclass
class MenuItem:
    """A single item in a context menu.

    Args:
        label: Display text for the item.
        callback: Function (sync or async) invoked when the item is selected.
        hotkey_hint: Right-aligned hint text (e.g. "Ctrl+C").
        disabled: If True the item is shown dimmed and cannot be selected.
        separator: If True the item renders as a horizontal divider line.
                   All other fields are ignored for separators.
    """

    label: str = ""
    callback: Callable[[], None] | Callable[[], Awaitable[None]] | None = None
    hotkey_hint: str = ""
    disabled: bool = False
    separator: bool = False


# ---------------------------------------------------------------------------
# Internal row widgets
# ---------------------------------------------------------------------------

class _MenuSeparator(Static):
    """Horizontal divider rendered as a line of box-drawing characters."""

    DEFAULT_CSS = """
    _MenuSeparator {
        height: 1;
        color: $text-muted;
        padding: 0 1;
    }
    """

    def __init__(self, width: int) -> None:
        super().__init__("\u2500" * max(width, 1))


class _MenuItemRow(Static):
    """A selectable menu row showing label and optional hotkey hint."""

    DEFAULT_CSS = """
    _MenuItemRow {
        height: 1;
        padding: 0 1;
        width: 1fr;
    }
    _MenuItemRow:hover {
        background: $accent;
        color: $text;
    }
    _MenuItemRow.--highlighted {
        background: $accent;
        color: $text;
    }
    _MenuItemRow.--disabled {
        color: $text-disabled;
    }
    _MenuItemRow.--disabled:hover {
        background: transparent;
        color: $text-disabled;
    }
    """

    def __init__(
        self,
        item: MenuItem,
        row_width: int,
        index: int,
    ) -> None:
        self._item = item
        self._index = index
        self._row_width = row_width
        super().__init__(
            self._format_text(),
            classes="--disabled" if item.disabled else "",
        )

    def _format_text(self) -> str:
        """Build the row text: label left-aligned, hotkey_hint right-aligned."""
        label = self._item.label
        hint = self._item.hotkey_hint
        if hint:
            # Pad between label and hint so total equals row_width.
            gap = max(self._row_width - len(label) - len(hint), 2)
            return f"{label}{' ' * gap}{hint}"
        return label

    @property
    def item(self) -> MenuItem:
        return self._item

    @property
    def index(self) -> int:
        return self._index


# ---------------------------------------------------------------------------
# Context menu widget
# ---------------------------------------------------------------------------

class ContextMenu(Widget):
    """Popup context menu with keyboard/mouse navigation.

    Mount this widget into a parent to display a floating context menu.
    The menu uses absolute positioning via CSS and should be placed on a
    layer above normal content.

    The parent is responsible for mounting and removing the widget.
    Typical pattern::

        menu = ContextMenu(items=items, position=(x, y))
        await self.mount(menu)

    Listen for :class:`ContextMenu.Closed` (dismiss) and
    :class:`ContextMenu.ItemSelected` (item chosen) messages.
    """

    ALLOW_FOCUS = True
    can_focus = True

    BINDINGS = [
        Binding("up", "cursor_up", "Up", show=False),
        Binding("down", "cursor_down", "Down", show=False),
        Binding("enter", "select", "Select", show=False),
        Binding("escape", "close", "Close", show=False),
    ]

    DEFAULT_CSS = """
    ContextMenu {
        position: absolute;
        layer: context-menu;
        border: solid $accent;
        background: $surface;
        padding: 0;
        width: auto;
        height: auto;
        max-height: 20;
        overflow-y: auto;
    }
    """

    # -- Messages -----------------------------------------------------------

    class Closed(Message):
        """Posted when the context menu is dismissed (Escape or click-away)."""

    class ItemSelected(Message):
        """Posted when a menu item is selected.

        Attributes:
            item: The :class:`MenuItem` that was chosen.
        """

        def __init__(self, item: MenuItem) -> None:
            super().__init__()
            self.item = item

    # -- Reactive state -----------------------------------------------------

    highlight_index: reactive[int] = reactive(-1, repaint=False)
    """Index (into the full items list) of the currently highlighted row."""

    # -- Construction -------------------------------------------------------

    def __init__(
        self,
        items: list[MenuItem],
        position: tuple[int, int] = (0, 0),
        *,
        name: str | None = None,
        id: str | None = None,
        classes: str | None = None,
    ) -> None:
        super().__init__(name=name, id=id, classes=classes)
        self._items = list(items)
        self._position = position
        # Build the list of selectable (non-separator, non-disabled) indices.
        self._selectable_indices: list[int] = [
            i
            for i, item in enumerate(self._items)
            if not item.separator and not item.disabled
        ]
        self._row_widgets: list[_MenuItemRow] = []
        self._content_width = self._compute_width()

    # -- Layout helpers -----------------------------------------------------

    def _compute_width(self) -> int:
        """Calculate the inner content width based on item labels and hints."""
        widths: list[int] = []
        for item in self._items:
            if item.separator:
                continue
            w = len(item.label)
            if item.hotkey_hint:
                # label + minimum_gap(2) + hint
                w += 2 + len(item.hotkey_hint)
            widths.append(w)
        return max(widths, default=10)

    # -- Compose ------------------------------------------------------------

    def compose(self) -> ComposeResult:
        """Build the menu rows."""
        for i, item in enumerate(self._items):
            if item.separator:
                yield _MenuSeparator(self._content_width)
            else:
                row = _MenuItemRow(
                    item=item,
                    row_width=self._content_width,
                    index=i,
                )
                self._row_widgets.append(row)
                yield row

    # -- Lifecycle ----------------------------------------------------------

    def on_mount(self) -> None:
        """Position the menu and set initial highlight."""
        x, y = self._position
        self.styles.offset = (x, y)
        # Explicit width: content width + left/right padding (1 each) + border (1 each).
        self.styles.width = self._content_width + 4

        # Set initial highlight to the first selectable item.
        if self._selectable_indices:
            self.highlight_index = self._selectable_indices[0]

        self.focus()

    # -- Highlight management -----------------------------------------------

    def watch_highlight_index(self, old: int, new: int) -> None:
        """Update CSS classes when the highlight moves."""
        for row in self._row_widgets:
            if row.index == old:
                row.remove_class("--highlighted")
            if row.index == new:
                row.add_class("--highlighted")
                # Scroll the highlighted row into view if needed.
                self.scroll_visible(row, animate=False)

    def _row_for_index(self, index: int) -> _MenuItemRow | None:
        """Return the row widget for a given item index, or None."""
        for row in self._row_widgets:
            if row.index == index:
                return row
        return None

    # -- Keyboard actions ---------------------------------------------------

    def action_cursor_up(self) -> None:
        """Move highlight to the previous selectable item."""
        if not self._selectable_indices:
            return
        try:
            pos = self._selectable_indices.index(self.highlight_index)
        except ValueError:
            pos = 0
        new_pos = (pos - 1) % len(self._selectable_indices)
        self.highlight_index = self._selectable_indices[new_pos]

    def action_cursor_down(self) -> None:
        """Move highlight to the next selectable item."""
        if not self._selectable_indices:
            return
        try:
            pos = self._selectable_indices.index(self.highlight_index)
        except ValueError:
            pos = -1
        new_pos = (pos + 1) % len(self._selectable_indices)
        self.highlight_index = self._selectable_indices[new_pos]

    def action_select(self) -> None:
        """Select the currently highlighted item."""
        if self.highlight_index < 0:
            return
        item = self._items[self.highlight_index]
        if item.disabled or item.separator:
            return
        self._select_item(item)

    def action_close(self) -> None:
        """Close the menu without selecting anything."""
        self._dismiss()

    # -- Mouse handling -----------------------------------------------------

    @on(events.Click)
    def _on_row_click(self, event: events.Click) -> None:
        """Handle clicks on menu item rows."""
        # Prevent the click from propagating and closing via the blur handler.
        event.stop()

        # Walk up from the clicked widget to find the _MenuItemRow.
        target = event.widget
        if isinstance(target, _MenuItemRow):
            item = target.item
            if not item.disabled:
                self.highlight_index = target.index
                self._select_item(item)

    def on_blur(self, _event: events.Blur) -> None:
        """Close the menu when it loses focus (click outside)."""
        self._dismiss()

    # -- Selection / dismissal ----------------------------------------------

    def _select_item(self, item: MenuItem) -> None:
        """Post the selection message and invoke the callback."""
        self.post_message(self.ItemSelected(item))
        self._dismiss()

        if item.callback is not None:
            if inspect.iscoroutinefunction(item.callback):
                # Schedule the async callback on the running event loop.
                asyncio.ensure_future(item.callback())
            else:
                item.callback()

    def _dismiss(self) -> None:
        """Post the Closed message and remove from the DOM."""
        self.post_message(self.Closed())
        self.remove()
