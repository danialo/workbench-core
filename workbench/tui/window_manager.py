"""
Window manager container for the Workbench TUI.

Holds all Window instances, manages z-order stacking, provides layout
commands (cascade, tile horizontal/vertical, grid), snap-to-grid on drag
end, and a taskbar for minimized windows.
"""

from __future__ import annotations

import math
from enum import Enum

from textual import on
from textual.app import ComposeResult
from textual.containers import Container, Horizontal
from textual.message import Message
from textual.reactive import reactive
from textual.widgets import Static

from workbench.tui.window import Window, WindowState


# ---------------------------------------------------------------------------
# Window kind enum
# ---------------------------------------------------------------------------

class WindowKind(Enum):
    """Identifies the type of window for creation routing."""

    CHAT = "chat"
    EVENTS = "events"
    TOOLS = "tools"
    CONFIG = "config"
    ARTIFACTS = "artifacts"


# ---------------------------------------------------------------------------
# Taskbar
# ---------------------------------------------------------------------------

class _TaskbarItem(Static):
    """A clickable label in the taskbar representing a minimized window."""

    DEFAULT_CSS = """
    _TaskbarItem {
        width: auto;
        height: 1;
        padding: 0 2;
        background: $accent-darken-1;
        color: $text;
        margin: 0 1;
    }
    _TaskbarItem:hover {
        background: $accent;
    }
    """

    def __init__(self, window_id: str, title: str) -> None:
        super().__init__(f"\u25a0 {title}")
        self.window_id = window_id

    async def on_click(self, event) -> None:
        event.stop()
        self.post_message(Taskbar.RestoreRequested(self.window_id))


class Taskbar(Container):
    """Docked-bottom bar showing minimized windows as clickable labels."""

    DEFAULT_CSS = """
    Taskbar {
        dock: bottom;
        height: 1;
        background: $accent-darken-2;
        layout: horizontal;
        display: none;
    }
    Taskbar.--visible {
        display: block;
    }
    """

    class RestoreRequested(Message):
        """Posted when a taskbar item is clicked to restore a window."""

        def __init__(self, window_id: str) -> None:
            super().__init__()
            self.window_id = window_id

    def update_items(self, minimized: list[tuple[str, str]]) -> None:
        """Rebuild taskbar with current minimized windows.

        Args:
            minimized: List of (window_id, title) tuples.
        """
        self.query("_TaskbarItem").remove()
        if minimized:
            self.add_class("--visible")
            for win_id, title in minimized:
                self.mount(_TaskbarItem(win_id, title))
        else:
            self.remove_class("--visible")


# ---------------------------------------------------------------------------
# Window manager
# ---------------------------------------------------------------------------

class WindowManager(Container):
    """Container for managing floating windows.

    Holds Window instances with absolute positioning, manages z-order
    stacking, and provides layout arrangement commands.

    Messages:
        WindowManager.ActiveWindowChanged -- when the focused window changes.
    """

    DEFAULT_CSS = """
    WindowManager {
        width: 1fr;
        height: 1fr;
        layers: default windows context-menu menu-dropdown;
    }
    """

    class ActiveWindowChanged(Message):
        """Posted when the active (focused) window changes."""

        def __init__(self, window: Window | None) -> None:
            super().__init__()
            self.window = window

    # -- Reactive state --------------------------------------------------------

    active_window: reactive[Window | None] = reactive(None)
    _z_counter: int = 0

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self._windows: dict[str, Window] = {}
        self._taskbar = Taskbar()

    def compose(self) -> ComposeResult:
        yield self._taskbar

    # -- Window lifecycle ------------------------------------------------------

    def open_window(self, window: Window, offset: tuple[int, int] | None = None) -> None:
        """Add and display a new window.

        Args:
            window: The Window widget to add.
            offset: Optional (x, y) initial position.
        """
        win_id = window.id or f"win-{len(self._windows)}"
        if window.id is None:
            window.id = win_id

        self._windows[win_id] = window
        self.mount(window, before=self._taskbar)

        if offset is not None:
            window.styles.offset = offset

        self.bring_to_front(win_id)

    def close_window(self, win_id: str) -> None:
        """Close and remove a window by ID."""
        window = self._windows.pop(win_id, None)
        if window is None:
            return
        window.remove()
        if self.active_window is window:
            # Activate the next available window
            remaining = [w for w in self._windows.values()
                         if w.window_state != WindowState.MINIMIZED]
            self.active_window = remaining[-1] if remaining else None
        self._refresh_taskbar()

    def bring_to_front(self, win_id: str) -> None:
        """Raise a window to the top of the z-order."""
        window = self._windows.get(win_id)
        if window is None:
            return
        self._z_counter += 1
        # Reorder the window DOM node to be last (highest z) before the taskbar.
        # Using move_child instead of remove/mount preserves the widget tree
        # so that children (compose results) are not destroyed and recreated.
        self.move_child(window, before=self._taskbar)
        self.active_window = window

    def get_window(self, win_id: str) -> Window | None:
        """Look up a window by ID."""
        return self._windows.get(win_id)

    @property
    def windows(self) -> list[Window]:
        """All managed windows."""
        return list(self._windows.values())

    @property
    def visible_windows(self) -> list[Window]:
        """Windows that are not minimized."""
        return [w for w in self._windows.values()
                if w.window_state != WindowState.MINIMIZED]

    # -- Reactive watchers -----------------------------------------------------

    def watch_active_window(self, old: Window | None, new: Window | None) -> None:
        self.post_message(self.ActiveWindowChanged(new))

    # -- Window message handlers -----------------------------------------------

    @on(Window.Focused)
    def _on_window_focused(self, event: Window.Focused) -> None:
        event.stop()
        win_id = event.window.id
        if win_id:
            self.bring_to_front(win_id)

    @on(Window.Closed)
    def _on_window_closed(self, event: Window.Closed) -> None:
        event.stop()
        win_id = event.window.id
        if win_id:
            self.close_window(win_id)

    @on(Window.StateChanged)
    def _on_window_state_changed(self, event: Window.StateChanged) -> None:
        event.stop()
        self._refresh_taskbar()

    @on(Taskbar.RestoreRequested)
    def _on_restore_requested(self, event: Taskbar.RestoreRequested) -> None:
        event.stop()
        window = self._windows.get(event.window_id)
        if window is not None:
            window.restore()
            self.bring_to_front(event.window_id)

    # -- Taskbar ---------------------------------------------------------------

    def _refresh_taskbar(self) -> None:
        """Update the taskbar with currently minimized windows."""
        minimized = [
            (w.id or "", w.window_title)
            for w in self._windows.values()
            if w.window_state == WindowState.MINIMIZED
        ]
        self._taskbar.update_items(minimized)

    def toggle_taskbar(self) -> None:
        """Toggle taskbar visibility."""
        if self._taskbar.has_class("--visible"):
            self._taskbar.remove_class("--visible")
        else:
            self._taskbar.add_class("--visible")

    # -- Layout commands -------------------------------------------------------

    def _workspace_size(self) -> tuple[int, int]:
        """Return the usable (width, height) of the manager area."""
        return (self.size.width, self.size.height - 1)  # -1 for taskbar

    def cascade(self) -> None:
        """Arrange visible windows in a cascading pattern."""
        visible = self.visible_windows
        if not visible:
            return
        w, h = self._workspace_size()
        win_w = max(int(w * 0.6), 30)
        win_h = max(int(h * 0.6), 10)
        for i, window in enumerate(visible):
            if window.window_state == WindowState.MAXIMIZED:
                window.restore()
            x = (i * 4) % max(w - win_w, 1)
            y = (i * 2) % max(h - win_h, 1)
            window.styles.offset = (x, y)
            window.styles.width = win_w
            window.styles.height = win_h

    def tile_horizontal(self) -> None:
        """Stack visible windows top-to-bottom, full width."""
        visible = self.visible_windows
        if not visible:
            return
        w, h = self._workspace_size()
        each_h = max(h // len(visible), 6)
        for i, window in enumerate(visible):
            if window.window_state == WindowState.MAXIMIZED:
                window.restore()
            window.styles.offset = (0, i * each_h)
            window.styles.width = w
            window.styles.height = each_h

    def tile_vertical(self) -> None:
        """Stack visible windows left-to-right, full height."""
        visible = self.visible_windows
        if not visible:
            return
        w, h = self._workspace_size()
        each_w = max(w // len(visible), 20)
        for i, window in enumerate(visible):
            if window.window_state == WindowState.MAXIMIZED:
                window.restore()
            window.styles.offset = (i * each_w, 0)
            window.styles.width = each_w
            window.styles.height = h

    def tile_grid(self) -> None:
        """Arrange visible windows in an auto-calculated grid."""
        visible = self.visible_windows
        if not visible:
            return
        n = len(visible)
        w, h = self._workspace_size()

        cols = math.ceil(math.sqrt(n))
        rows = math.ceil(n / cols)

        cell_w = max(w // cols, 20)
        cell_h = max(h // rows, 6)

        for i, window in enumerate(visible):
            if window.window_state == WindowState.MAXIMIZED:
                window.restore()
            row = i // cols
            col = i % cols
            window.styles.offset = (col * cell_w, row * cell_h)
            window.styles.width = cell_w
            window.styles.height = cell_h
