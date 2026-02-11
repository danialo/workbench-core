"""
Draggable, resizable, minimizable/maximizable window widget for Textual.

Provides a Window container that can be composed into any Textual screen
as a floating, desktop-style window with title bar, content area, resize
grip, and full window management (minimize, maximize, close, drag, resize).

Usage:
    class MyApp(App):
        def compose(self) -> ComposeResult:
            with Window(title="Editor", id="editor-win"):
                yield TextArea()
            with Window(title="Logs", id="log-win"):
                yield RichLog()

Messages posted:
    Window.Focused  -- when the window receives focus (click anywhere)
    Window.Closed   -- when the close button is pressed
    Window.StateChanged -- when minimized, maximized, or restored
"""

from __future__ import annotations

import logging
import time
from enum import Enum
from typing import Callable

_log = logging.getLogger("workbench.tui.window")

from textual import events, on
from textual.app import ComposeResult
from textual.containers import Container, ScrollableContainer
from textual.geometry import Offset
from textual.message import Message
from textual.reactive import reactive
from textual.widgets import Static


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------

class WindowState(Enum):
    """Possible states for a Window widget."""

    NORMAL = "normal"
    MINIMIZED = "minimized"
    MAXIMIZED = "maximized"


# ---------------------------------------------------------------------------
# Internal sub-widgets
# ---------------------------------------------------------------------------

class _TitleBar(Container):
    """Title bar for the Window widget.

    Contains the title label on the left and window-control buttons on
    the right.  Mouse events on the title bar drive window dragging.
    """

    DEFAULT_CSS = """
    _TitleBar {
        layout: horizontal;
        width: 1fr;
        height: 1;
        background: $accent;
        color: $text;
    }
    _TitleBar > .window-title-label {
        width: 1fr;
        height: 1;
        padding: 0 1;
        text-style: bold;
    }
    _TitleBar > .window-btn {
        width: 4;
        height: 1;
        min-width: 4;
        text-align: center;
        background: $accent;
        color: $text;
    }
    _TitleBar > .window-btn:hover {
        background: $accent-lighten-2;
    }
    _TitleBar > .window-btn-close:hover {
        background: $error;
        color: $text;
    }
    """

    def __init__(self, title: str) -> None:
        super().__init__()
        self._title_text = title

    def compose(self) -> ComposeResult:
        yield Static(self._title_text, classes="window-title-label")
        yield Static("[_]", classes="window-btn window-btn-minimize")
        yield Static("[\u25a1]", classes="window-btn window-btn-maximize")
        yield Static("[X]", classes="window-btn window-btn-close")

    def set_title(self, title: str) -> None:
        """Update the title text displayed in the bar."""
        self._title_text = title
        try:
            label = self.query_one(".window-title-label", Static)
            label.update(title)
        except Exception:
            pass

    def set_maximize_icon(self, maximized: bool) -> None:
        """Toggle the maximize button icon between maximize/restore."""
        try:
            btn = self.query_one(".window-btn-maximize", Static)
            btn.update("[\u229e]" if maximized else "[\u25a1]")
        except Exception:
            pass


class _ResizeGrip(Static):
    """Small grip in the bottom-right corner that enables window resizing."""

    DEFAULT_CSS = """
    _ResizeGrip {
        dock: bottom;
        width: 2;
        height: 1;
        content-align: right bottom;
        color: $text-muted;
    }
    _ResizeGrip:hover {
        color: $accent;
    }
    """

    def __init__(self) -> None:
        super().__init__("\u22f1")  # down-right diagonal ellipsis


class _ContentArea(ScrollableContainer):
    """Scrollable container for window content."""

    DEFAULT_CSS = """
    _ContentArea {
        width: 1fr;
        height: 1fr;
    }
    """


# ---------------------------------------------------------------------------
# Window widget
# ---------------------------------------------------------------------------

class Window(Container):
    """A draggable, resizable, minimizable/maximizable floating window.

    Args:
        title: Text displayed in the title bar.
        grid_size: Tuple (x, y) for snap-to-grid on drag release.
            Default ``(4, 2)``.  Set to ``(1, 1)`` to disable snapping.
        min_width: Minimum allowed width in columns.
        min_height: Minimum allowed height in rows.
        *children: Child widgets placed inside the content area.
        **kwargs: Passed through to ``Container.__init__``.
    """

    # -- Textual CSS -----------------------------------------------------------

    DEFAULT_CSS = """
    Window {
        position: absolute;
        width: 60;
        height: 20;
        border: solid $secondary;
        background: $surface;
        overflow: hidden;
        layer: windows;
    }
    Window:focus-within {
        border: thick $primary;
    }
    Window.-maximized {
        border: thick $primary;
    }
    Window.-minimized {
        display: none;
    }
    Window > ._grip-row {
        dock: bottom;
        height: 1;
        width: 1fr;
        layout: horizontal;
    }
    Window > ._grip-row > ._grip-spacer {
        width: 1fr;
        height: 1;
    }
    """

    # -- Messages --------------------------------------------------------------

    class Focused(Message):
        """Posted when the window is focused (clicked anywhere)."""

        def __init__(self, window: Window) -> None:
            super().__init__()
            self.window: Window = window

    class Closed(Message):
        """Posted when the close button is clicked."""

        def __init__(self, window: Window) -> None:
            super().__init__()
            self.window: Window = window

    class StateChanged(Message):
        """Posted when the window state changes (minimize/maximize/restore)."""

        def __init__(self, window: Window, state: str) -> None:
            super().__init__()
            self.window: Window = window
            self.state: str = state

    # -- Reactive state --------------------------------------------------------

    window_state: reactive[WindowState] = reactive(WindowState.NORMAL)
    window_title: reactive[str] = reactive("")

    # -- Construction ----------------------------------------------------------

    def __init__(
        self,
        *children,
        title: str = "Window",
        grid_size: tuple[int, int] = (4, 2),
        min_width: int = 20,
        min_height: int = 6,
        name: str | None = None,
        id: str | None = None,
        classes: str | None = None,
        disabled: bool = False,
    ) -> None:
        super().__init__(
            name=name,
            id=id,
            classes=classes,
            disabled=disabled,
        )
        self.window_title = title
        self._child_widgets = children
        self._grid_size = grid_size
        self._min_width = min_width
        self._min_height = min_height

        # Drag state
        self._drag_mouse_start: Offset | None = None
        self._drag_offset_start: Offset | None = None

        # Resize state
        self._resize_mouse_start: Offset | None = None
        self._resize_size_start: tuple[int, int] | None = None

        # Double-click detection on title bar
        self._last_titlebar_click: float = 0.0

        # Saved geometry for restore from maximized
        self._saved_offset: Offset | None = None
        self._saved_width: int | None = None
        self._saved_height: int | None = None

    # -- Compose ---------------------------------------------------------------

    def compose(self) -> ComposeResult:
        yield _TitleBar(self.window_title)
        with _ContentArea():
            yield from self._child_widgets
        with Container(classes="_grip-row"):
            yield Static("", classes="_grip-spacer")
            yield _ResizeGrip()

    # -- Reactive watchers -----------------------------------------------------

    def watch_window_state(self, old: WindowState, new: WindowState) -> None:
        """React to state changes by updating CSS classes and geometry."""
        _log.info("Window %s state: %s -> %s", self.id, old.value, new.value)

        # Save geometry BEFORE class changes so we read the real dimensions,
        # not the CSS-overridden values.
        if old == WindowState.NORMAL and new != WindowState.NORMAL:
            self._save_geometry()

        # Remove old state classes
        self.remove_class("-normal", "-minimized", "-maximized")
        # Add new
        self.add_class(f"-{new.value}")

        if new == WindowState.MAXIMIZED:
            # Set dimensions programmatically — CSS `1fr` doesn't resolve
            # correctly for `position: absolute` elements in Textual.
            parent = self.parent
            if parent is not None:
                self.styles.offset = (0, 0)
                self.styles.width = parent.size.width
                self.styles.height = parent.size.height
            title_bar = self.query_one(_TitleBar)
            title_bar.set_maximize_icon(True)

        elif new == WindowState.NORMAL:
            self._restore_geometry()
            title_bar = self.query_one(_TitleBar)
            title_bar.set_maximize_icon(False)

        elif new == WindowState.MINIMIZED:
            pass  # display: none via CSS class

        self.post_message(self.StateChanged(self, new.value))

    def watch_window_title(self, old: str, new: str) -> None:
        """Update the title bar label when the reactive title changes."""
        try:
            self.query_one(_TitleBar).set_title(new)
        except Exception:
            pass

    # -- Geometry helpers ------------------------------------------------------

    def _current_offset(self) -> Offset:
        """Return the current offset as an integer Offset."""
        x = round(self.styles.offset.x.value) if self.styles.offset.x else 0
        y = round(self.styles.offset.y.value) if self.styles.offset.y else 0
        return Offset(x, y)

    def _current_size(self) -> tuple[int, int]:
        """Return current explicit (width, height) as integers.

        Falls back to actual rendered size if no explicit style is set.
        """
        w = (
            round(self.styles.width.value)
            if self.styles.width and hasattr(self.styles.width, "value")
            else self.size.width
        )
        h = (
            round(self.styles.height.value)
            if self.styles.height and hasattr(self.styles.height, "value")
            else self.size.height
        )
        return (max(w, self._min_width), max(h, self._min_height))

    def _save_geometry(self) -> None:
        """Persist current offset and size so we can restore later."""
        self._saved_offset = self._current_offset()
        w, h = self._current_size()
        self._saved_width = w
        self._saved_height = h

    def _restore_geometry(self) -> None:
        """Restore previously saved geometry (offset and size)."""
        if self._saved_offset is not None:
            self.styles.offset = (self._saved_offset.x, self._saved_offset.y)
        if self._saved_width is not None:
            self.styles.width = self._saved_width
        if self._saved_height is not None:
            self.styles.height = self._saved_height

    def _snap_to_grid(self, x: int, y: int) -> tuple[int, int]:
        """Snap coordinates to the configured grid."""
        gx, gy = self._grid_size
        return (round(x / gx) * gx, round(y / gy) * gy)

    # -- Focus / z-order -------------------------------------------------------

    def on_click(self, event: events.Click) -> None:
        """Post Focused message when clicked anywhere in the window."""
        _log.debug("Window %s clicked, posting Focused", self.id)
        self.post_message(self.Focused(self))

    # -- Title bar button handlers ---------------------------------------------

    @on(events.Click, ".window-btn-minimize")
    def _on_minimize_click(self, event: events.Click) -> None:
        event.stop()
        self.minimize()

    @on(events.Click, ".window-btn-maximize")
    def _on_maximize_click(self, event: events.Click) -> None:
        event.stop()
        self.toggle_maximize()

    @on(events.Click, ".window-btn-close")
    def _on_close_click(self, event: events.Click) -> None:
        event.stop()
        self.post_message(self.Closed(self))

    # -- Title bar drag --------------------------------------------------------

    def _is_titlebar_target(self, event: events.MouseEvent) -> bool:
        """Return True if the event originated from the title bar (not a button)."""
        widget = event.widget if hasattr(event, "widget") else None
        if widget is None:
            return False
        # Accept the title bar itself or its title label child
        if isinstance(widget, _TitleBar):
            return True
        if hasattr(widget, "parent") and isinstance(widget.parent, _TitleBar):
            # But not the control buttons
            if widget.has_class("window-btn"):
                return False
            return True
        return False

    def _is_resize_grip(self, event: events.MouseEvent) -> bool:
        """Return True if the event originated from the resize grip."""
        widget = event.widget if hasattr(event, "widget") else None
        return isinstance(widget, _ResizeGrip)

    def on_mouse_down(self, event: events.MouseDown) -> None:
        """Start drag or resize depending on target."""
        if self.window_state == WindowState.MAXIMIZED:
            # No drag/resize while maximized
            if self._is_resize_grip(event):
                event.stop()
                return
            if self._is_titlebar_target(event):
                event.stop()
                return
            return

        if self._is_resize_grip(event):
            event.stop()
            self._resize_mouse_start = event.screen_offset
            self._resize_size_start = self._current_size()
            self.capture_mouse()
            return

        if self._is_titlebar_target(event):
            event.stop()
            # Double-click detection
            now = time.monotonic()
            if now - self._last_titlebar_click < 0.4:
                self._last_titlebar_click = 0.0
                self.toggle_maximize()
                return
            self._last_titlebar_click = now

            self._drag_mouse_start = event.screen_offset
            self._drag_offset_start = self._current_offset()
            self.capture_mouse()
            return

    def on_mouse_move(self, event: events.MouseMove) -> None:
        """Handle in-progress drag or resize."""
        if self._resize_mouse_start is not None and self._resize_size_start is not None:
            event.stop()
            dx = event.screen_x - self._resize_mouse_start.x
            dy = event.screen_y - self._resize_mouse_start.y
            new_w = max(self._resize_size_start[0] + dx, self._min_width)
            new_h = max(self._resize_size_start[1] + dy, self._min_height)
            self.styles.width = new_w
            self.styles.height = new_h
            return

        if self._drag_mouse_start is not None and self._drag_offset_start is not None:
            event.stop()
            x = self._drag_offset_start.x + (event.screen_x - self._drag_mouse_start.x)
            y = self._drag_offset_start.y + (event.screen_y - self._drag_mouse_start.y)
            self.styles.offset = (x, y)
            return

    def on_mouse_up(self, event: events.MouseUp) -> None:
        """Finish drag or resize, snap to grid."""
        if self._resize_mouse_start is not None:
            event.stop()
            self._resize_mouse_start = None
            self._resize_size_start = None
            self.release_mouse()
            return

        if self._drag_mouse_start is not None:
            event.stop()
            current = self._current_offset()
            sx, sy = self._snap_to_grid(current.x, current.y)
            self.styles.offset = (sx, sy)
            self._drag_mouse_start = None
            self._drag_offset_start = None
            self.release_mouse()
            return

    # -- Public API: window state management -----------------------------------

    def minimize(self) -> None:
        """Minimize the window (hide it)."""
        if self.window_state != WindowState.MINIMIZED:
            self.window_state = WindowState.MINIMIZED

    def maximize(self) -> None:
        """Maximize the window to fill its parent."""
        if self.window_state != WindowState.MAXIMIZED:
            self.window_state = WindowState.MAXIMIZED

    def restore(self) -> None:
        """Restore the window to its normal size and position."""
        if self.window_state != WindowState.NORMAL:
            self.window_state = WindowState.NORMAL

    def toggle_maximize(self) -> None:
        """Toggle between maximized and normal states."""
        if self.window_state == WindowState.MAXIMIZED:
            self.restore()
        else:
            self.maximize()

    def close(self) -> None:
        """Post Closed message and remove the window."""
        self.post_message(self.Closed(self))
        self.remove()

    # -- Protocol: menus (default no-op implementations) -----------------------

    def get_context_menu_items(self) -> list[tuple[str, Callable]]:
        """Return context menu items as ``(label, callback)`` pairs.

        Subclasses may override to provide window-specific context menus.
        By default returns an empty list.
        """
        return []

    def get_menu_bar_sections(self) -> list[tuple[str, list[tuple[str, Callable]]]]:
        """Return menu bar sections as ``(section_name, items)`` pairs.

        Each item is a ``(label, callback)`` tuple.  Subclasses may override
        to populate a shared menu bar when this window is focused.
        By default returns an empty list.
        """
        return []
