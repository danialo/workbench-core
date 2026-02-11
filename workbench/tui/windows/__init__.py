"""Window type implementations for the Workbench TUI."""

from workbench.tui.windows.chat_window import ChatWindowContent
from workbench.tui.windows.events_window import EventsWindowContent
from workbench.tui.windows.tools_window import ToolsWindowContent
from workbench.tui.windows.config_window import ConfigWindowContent
from workbench.tui.windows.artifacts_window import ArtifactsWindowContent

__all__ = [
    "ChatWindowContent",
    "EventsWindowContent",
    "ToolsWindowContent",
    "ConfigWindowContent",
    "ArtifactsWindowContent",
]
