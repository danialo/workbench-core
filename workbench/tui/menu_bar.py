"""
Adaptive top menu bar widget for the Workbench TUI.

Provides a horizontal menu bar with dropdown sections, supporting both
static (always-present) and dynamic (context-dependent) menu sections.
Dropdowns are keyboard-navigable, support separators and hotkey hints,
and communicate selections via Textual messages.
"""

from __future__ import annotations

import inspect
import logging
from dataclasses import dataclass, field
from typing import Awaitable, Callable

_log = logging.getLogger("workbench.tui.menu_bar")

from textual import events, on
from textual.app import ComposeResult
from textual.containers import Horizontal
from textual.message import Message
from textual.reactive import reactive
from textual.widget import Widget
from textual.widgets import Static


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------


@dataclass
class MenuAction:
    """A single menu item within a dropdown section.

    Set ``separator=True`` to render a visual divider line instead of a
    clickable item.  When used as a separator, all other fields are ignored.
    """

    label: str = ""
    callback: Callable[[], None] | Callable[[], Awaitable[None]] | None = None
    hotkey_hint: str = ""
    disabled: bool = False
    separator: bool = False


@dataclass
class MenuSection:
    """A named group of menu actions displayed as a dropdown."""

    name: str
    items: list[MenuAction] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Individual menu item widget (rendered inside the dropdown)
# ---------------------------------------------------------------------------


class _MenuItemWidget(Static):
    """Single row inside a :class:`MenuDropdown`."""

    DEFAULT_CSS = """
    _MenuItemWidget {
        width: 1fr;
        height: 1;
        padding: 0 1;
    }
    _MenuItemWidget:hover {
        background: $accent;
        color: $text;
    }
    _MenuItemWidget.--disabled {
        color: $text-muted;
    }
    _MenuItemWidget.--disabled:hover {
        background: transparent;
    }
    _MenuItemWidget.--separator {
        color: $text-muted;
        height: 1;
    }
    _MenuItemWidget.--separator:hover {
        background: transparent;
    }
    _MenuItemWidget.--highlighted {
        background: $accent;
        color: $text;
    }
    """

    def __init__(
        self,
        action: MenuAction,
        section_name: str,
        index: int,
        **kwargs,
    ) -> None:
        self.action = action
        self.section_name = section_name
        self.item_index = index

        if action.separator:
            markup_text = "\u2500" * 30
            super().__init__(markup_text, classes="--separator", **kwargs)
        else:
            hint_part = f"  [{action.hotkey_hint}]" if action.hotkey_hint else ""
            markup_text = f"{action.label}{hint_part}"
            classes = "--disabled" if action.disabled else ""
            super().__init__(markup_text, classes=classes, **kwargs)

    @property
    def is_selectable(self) -> bool:
        """Whether this item can be selected/activated."""
        return not self.action.separator and not self.action.disabled

    def highlight(self, on: bool) -> None:
        """Toggle visual highlight state."""
        if on:
            self.add_class("--highlighted")
        else:
            self.remove_class("--highlighted")

    async def on_click(self, event: events.Click) -> None:
        """Handle click on this menu item."""
        event.stop()
        if not self.is_selectable:
            return
        dropdown = self.parent
        if isinstance(dropdown, MenuDropdown):
            dropdown._select_action(self.action)


# ---------------------------------------------------------------------------
# Dropdown panel
# ---------------------------------------------------------------------------


class MenuDropdown(Widget):
    """Absolute-positioned dropdown that appears below a menu section label.

    Contains a vertical list of :class:`_MenuItemWidget` instances.
    Supports keyboard navigation with Up/Down arrows and Enter.
    """

    DEFAULT_CSS = """
    MenuDropdown {
        position: absolute;
        layer: menu-dropdown;
        border: solid $accent;
        background: $surface;
        min-width: 25;
        height: auto;
        max-height: 20;
        width: auto;
        padding: 0;
    }
    """

    class ItemSelected(Message):
        """Posted when a menu item is selected from the dropdown."""

        def __init__(
            self,
            section: str,
            label: str,
            action: MenuAction,
        ) -> None:
            super().__init__()
            self.section = section
            self.label = label
            self.action = action

    highlighted_index: reactive[int] = reactive(-1, repaint=False)

    def __init__(
        self,
        section: MenuSection,
        menu_bar: MenuBar | None = None,
        **kwargs,
    ) -> None:
        super().__init__(**kwargs)
        self.section = section
        self.menu_bar: MenuBar | None = menu_bar
        self._item_widgets: list[_MenuItemWidget] = []

    def compose(self) -> ComposeResult:
        self._item_widgets = []
        for i, action in enumerate(self.section.items):
            widget = _MenuItemWidget(
                action=action,
                section_name=self.section.name,
                index=i,
            )
            self._item_widgets.append(widget)
            yield widget

    @property
    def is_visible(self) -> bool:
        """Whether the dropdown is currently mounted/displayed."""
        try:
            return self.parent is not None
        except Exception:
            return False

    def watch_highlighted_index(self, old: int, new: int) -> None:
        """Update visual highlight when the index changes."""
        if 0 <= old < len(self._item_widgets):
            self._item_widgets[old].highlight(False)
        if 0 <= new < len(self._item_widgets):
            self._item_widgets[new].highlight(True)

    def _selectable_indices(self) -> list[int]:
        """Return indices of items that can be highlighted."""
        return [
            i
            for i, w in enumerate(self._item_widgets)
            if w.is_selectable
        ]

    def _move_highlight(self, direction: int) -> None:
        """Move the highlight up (-1) or down (+1)."""
        selectable = self._selectable_indices()
        if not selectable:
            return
        if self.highlighted_index < 0:
            self.highlighted_index = selectable[0] if direction > 0 else selectable[-1]
            return
        try:
            current_pos = selectable.index(self.highlighted_index)
        except ValueError:
            current_pos = -1 if direction > 0 else len(selectable)
        next_pos = current_pos + direction
        if 0 <= next_pos < len(selectable):
            self.highlighted_index = selectable[next_pos]

    def _select_action(self, action: MenuAction) -> None:
        """Handle selection of an action — notify the menu bar directly."""
        _log.info("Dropdown item selected: %s", action.label)
        if self.menu_bar:
            self.menu_bar._handle_item_selected(self.section.name, action)

    def _activate_highlighted(self) -> None:
        """Select the currently highlighted item."""
        if 0 <= self.highlighted_index < len(self._item_widgets):
            widget = self._item_widgets[self.highlighted_index]
            if widget.is_selectable:
                self._select_action(widget.action)

    async def on_key(self, event: events.Key) -> None:
        """Handle keyboard navigation within the dropdown."""
        if event.key == "down":
            event.stop()
            event.prevent_default()
            self._move_highlight(1)
        elif event.key == "up":
            event.stop()
            event.prevent_default()
            self._move_highlight(-1)
        elif event.key == "enter":
            event.stop()
            event.prevent_default()
            self._activate_highlighted()
        elif event.key == "escape":
            event.stop()
            event.prevent_default()
            if self.menu_bar:
                self.menu_bar._close_dropdown()
        elif event.key == "left":
            event.stop()
            event.prevent_default()
            if self.menu_bar:
                self.menu_bar._switch_section(-1)
        elif event.key == "right":
            event.stop()
            event.prevent_default()
            if self.menu_bar:
                self.menu_bar._switch_section(1)

    def on_blur(self, event: events.Blur) -> None:
        """Close when focus leaves the dropdown."""
        self.set_timer(0.15, self._check_close_on_blur)

    def _check_close_on_blur(self) -> None:
        """Conditionally close if the dropdown lost focus."""
        bar = self.menu_bar
        if bar is None:
            return
        try:
            focused = self.app.focused
        except Exception:
            focused = None
        # If focus moved to another part of the menu bar, let it handle things.
        if focused is not None and bar is not None:
            if focused is bar or focused in bar.query("*"):
                return
        bar._close_dropdown()


# ---------------------------------------------------------------------------
# Section label in the menu bar
# ---------------------------------------------------------------------------


class _MenuSectionLabel(Static):
    """Clickable label for a single menu section in the horizontal bar."""

    DEFAULT_CSS = """
    _MenuSectionLabel {
        padding: 0 2;
        height: 1;
        width: auto;
    }
    _MenuSectionLabel:hover {
        background: $accent;
    }
    _MenuSectionLabel.--active {
        background: $accent;
        text-style: bold;
    }
    """

    def __init__(self, section_name: str, **kwargs) -> None:
        super().__init__(f" {section_name} ", **kwargs)
        self.section_name = section_name

    async def on_click(self, event: events.Click) -> None:
        """Toggle the dropdown for this section."""
        event.stop()
        _log.debug("SectionLabel clicked: %s, parent=%s", self.section_name, type(self.parent).__name__)
        bar = self.parent
        if isinstance(bar, Horizontal):
            bar = bar.parent
        _log.debug("Resolved bar type: %s", type(bar).__name__)
        if isinstance(bar, MenuBar):
            bar._toggle_section(self.section_name)
        else:
            _log.warning("Could not find MenuBar from label parent chain")

    async def on_enter(self, event: events.Enter) -> None:
        """Switch dropdown when hovering while another is open."""
        bar = self.parent
        if isinstance(bar, Horizontal):
            bar = bar.parent
        if isinstance(bar, MenuBar) and bar._active_section is not None:
            if bar._active_section != self.section_name:
                bar._open_section(self.section_name)


# ---------------------------------------------------------------------------
# Main MenuBar widget
# ---------------------------------------------------------------------------


class MenuBar(Widget):
    """Adaptive top menu bar with dropdown sections.

    Renders section names horizontally. Clicking toggles a dropdown.
    Only one dropdown is open at a time. Hovering over another section
    while a dropdown is open will switch to that section's dropdown.

    Sections are split into *static* (always present) and *dynamic*
    (context-dependent, swapped via :meth:`set_dynamic_sections`).
    """

    DEFAULT_CSS = """
    MenuBar {
        dock: top;
        height: 1;
        background: $accent-darken-2;
        color: $text;
    }
    MenuBar > Horizontal {
        height: 1;
        width: 1fr;
    }
    """

    class ActionSelected(Message):
        """Posted when a user selects any menu item.

        Attributes:
            section: Name of the section (e.g. ``"Window"``).
            label:   Label of the selected item.
            action:  The full :class:`MenuAction` dataclass, including callback.
        """

        def __init__(
            self,
            section: str,
            label: str,
            action: MenuAction | None = None,
        ) -> None:
            super().__init__()
            self.section = section
            self.label = label
            self.action = action

    def __init__(
        self,
        static_sections: list[MenuSection] | None = None,
        dynamic_sections: list[MenuSection] | None = None,
        **kwargs,
    ) -> None:
        super().__init__(**kwargs)
        self._static_sections: list[MenuSection] = static_sections or self._default_static_sections()
        self._dynamic_sections: list[MenuSection] = dynamic_sections or []
        self._active_section: str | None = None
        self._dropdowns: dict[str, MenuDropdown] = {}
        self._labels: dict[str, _MenuSectionLabel] = {}
        self._label_container: Horizontal | None = None
        self._rebuild_counter: int = 0

    # -- Default sections ---------------------------------------------------

    @staticmethod
    def _default_static_sections() -> list[MenuSection]:
        """Build the default static menu sections with ``None`` callbacks."""
        return [
            MenuSection(
                "Window",
                [
                    MenuAction("New Chat", hotkey_hint="Ctrl+N"),
                    MenuAction("Close Window", hotkey_hint="Ctrl+W"),
                    MenuAction(separator=True),
                    MenuAction("Cascade", hotkey_hint="F5"),
                    MenuAction("Tile Horizontal"),
                    MenuAction("Tile Vertical"),
                    MenuAction("Tile Grid", hotkey_hint="F6"),
                ],
            ),
            MenuSection(
                "View",
                [
                    MenuAction("Toggle Taskbar"),
                ],
            ),
        ]

    # -- Properties ---------------------------------------------------------

    @property
    def all_sections(self) -> list[MenuSection]:
        """Combined list of static + dynamic sections in display order."""
        return self._static_sections + self._dynamic_sections

    @property
    def section_names(self) -> list[str]:
        """Names of all current sections."""
        return [s.name for s in self.all_sections]

    # -- Public API ---------------------------------------------------------

    def set_static_sections(self, sections: list[MenuSection]) -> None:
        """Replace the static menu sections and rebuild the bar."""
        self._static_sections = sections
        self._rebuild()

    def set_dynamic_sections(self, sections: list[MenuSection]) -> None:
        """Replace the dynamic menu sections and rebuild the bar.

        Call this when the focused window or context changes to update
        the available menu items.
        """
        self._dynamic_sections = sections
        self._rebuild()

    def get_section(self, name: str) -> MenuSection | None:
        """Look up a section by name, or return ``None``."""
        for section in self.all_sections:
            if section.name == name:
                return section
        return None

    # -- Compose & Rebuild --------------------------------------------------

    def _dropdown_id(self, section_name: str) -> str:
        """Generate a unique dropdown ID using the rebuild counter."""
        slug = section_name.lower().replace(" ", "-")
        return f"menu-dd-{slug}-{self._rebuild_counter}"

    def compose(self) -> ComposeResult:
        self._label_container = Horizontal()
        with self._label_container:
            for section in self.all_sections:
                label = _MenuSectionLabel(section.name)
                self._labels[section.name] = label
                yield label
        # Dropdowns are mounted on the screen (not here) to avoid clipping.
        # They are created lazily in _open_section.

    def _rebuild(self) -> None:
        """Tear down and rebuild all child widgets after sections change."""
        self._close_dropdown()

        # Bump counter so new widgets get fresh IDs
        self._rebuild_counter += 1

        # Remove any screen-mounted dropdowns from previous build
        for dropdown in self._dropdowns.values():
            dropdown.remove()
        self._dropdowns.clear()

        # Collect old label widgets to remove
        old_widgets = list(self._nodes)
        self._labels.clear()

        # Build new label container
        self._label_container = Horizontal()
        new_labels: list[_MenuSectionLabel] = []
        for section in self.all_sections:
            label = _MenuSectionLabel(section.name)
            self._labels[section.name] = label
            new_labels.append(label)

        # Mount new container, then remove old ones
        self.mount(self._label_container)
        for label in new_labels:
            self._label_container.mount(label)

        for widget in old_widgets:
            widget.remove()

    # -- Dropdown management ------------------------------------------------

    def _calculate_offset(self, section_name: str) -> int:
        """Calculate the horizontal offset for a dropdown."""
        offset = 0
        for name in self.section_names:
            if name == section_name:
                break
            label = self._labels.get(name)
            if label is not None:
                try:
                    offset += label.region.width
                except Exception:
                    offset += len(name) + 4  # fallback: name + padding
        return offset

    def _toggle_section(self, section_name: str) -> None:
        """Toggle a section's dropdown open or closed."""
        _log.info("_toggle_section(%s), active=%s", section_name, self._active_section)
        if self._active_section == section_name:
            self._close_dropdown()
        else:
            self._open_section(section_name)

    def _open_section(self, section_name: str) -> None:
        """Open the dropdown for the named section, closing any other."""
        _log.info("_open_section(%s)", section_name)
        if self._active_section is not None:
            self._close_dropdown(clear_active=False)

        # Find the section data
        section = self.get_section(section_name)
        if section is None:
            _log.warning("No section data for %s", section_name)
            return

        self._active_section = section_name
        for name, label in self._labels.items():
            if name == section_name:
                label.add_class("--active")
            else:
                label.remove_class("--active")

        # Create and mount dropdown on the screen (not inside MenuBar)
        # so it isn't clipped by MenuBar's height: 1.
        dropdown = self._dropdowns.get(section_name)
        if dropdown is not None:
            dropdown.remove()
        dd_id = self._dropdown_id(section_name)
        dropdown = MenuDropdown(section, menu_bar=self, id=dd_id)
        self._dropdowns[section_name] = dropdown

        # Calculate position: x from label offset, y from MenuBar's bottom edge
        offset_x = self._calculate_offset(section_name)
        try:
            bar_y = self.region.y + self.region.height
        except Exception:
            bar_y = 2  # fallback: below header + menu bar
        _log.info("Mounting dropdown %s at (%d, %d)", dd_id, offset_x, bar_y)
        self.screen.mount(dropdown)
        dropdown.styles.offset = (offset_x, bar_y)
        dropdown.add_class("--visible")
        dropdown.focus()

    def _close_dropdown(self, clear_active: bool = True) -> None:
        """Close the currently open dropdown."""
        if self._active_section is not None:
            dropdown = self._dropdowns.get(self._active_section)
            if dropdown is not None:
                _log.debug("Closing dropdown for %s", self._active_section)
                dropdown.remove()
                del self._dropdowns[self._active_section]
            label = self._labels.get(self._active_section)
            if label is not None:
                label.remove_class("--active")
        if clear_active:
            self._active_section = None

    def _switch_section(self, direction: int) -> None:
        """Move to the next/previous section (for left/right arrow keys)."""
        names = self.section_names
        if not names or self._active_section is None:
            return
        try:
            current_idx = names.index(self._active_section)
        except ValueError:
            return
        next_idx = (current_idx + direction) % len(names)
        self._open_section(names[next_idx])

    # -- Message handling ---------------------------------------------------

    def _handle_item_selected(self, section_name: str, action: MenuAction) -> None:
        """Called by MenuDropdown when an item is selected."""
        _log.info("MenuBar._handle_item_selected: section=%s label=%s", section_name, action.label)
        self._close_dropdown()

        # Post the public message for the parent app
        self.post_message(
            MenuBar.ActionSelected(
                section=section_name,
                label=action.label,
                action=action,
            )
        )

        # Invoke the callback deferred so the DOM has settled after
        # the dropdown close (which triggers focus changes / reorders).
        if action.callback is not None:
            self.call_later(self._run_action_callback, action.callback)

    @staticmethod
    def _run_action_callback(callback: Callable) -> None:
        """Execute a menu action callback, handling async if needed."""
        result = callback()
        if inspect.isawaitable(result):
            import asyncio
            asyncio.ensure_future(result)

    # -- Global click handling (close on click-outside) ---------------------

    def on_click(self, event: events.Click) -> None:
        """Close dropdown if clicking on the bar background (not a label)."""
        self._close_dropdown()

    # -- Escape from bar itself ---------------------------------------------

    async def on_key(self, event: events.Key) -> None:
        """Handle Escape when the bar itself has focus."""
        if event.key == "escape" and self._active_section is not None:
            event.stop()
            event.prevent_default()
            self._close_dropdown()
