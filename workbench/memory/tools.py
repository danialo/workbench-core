"""Bridge tools connecting Memory Providers to the Tool Registry."""
from __future__ import annotations

import logging

from workbench.memory.provider import MemoryProvider
from workbench.tools.base import Tool, ToolRisk
from workbench.types import ToolResult, ErrorCode

logger = logging.getLogger(__name__)


class MemoryReadTool(Tool):
    """Read or list workspace memory entries."""

    def __init__(self, provider: MemoryProvider, workspace_id: str) -> None:
        self._provider = provider
        self._workspace_id = workspace_id

    @property
    def name(self) -> str:
        return "memory_read"

    @property
    def description(self) -> str:
        return (
            "Read workspace memory. Use action 'get' with a key to retrieve a "
            "specific entry, or 'list' to see all available keys."
        )

    @property
    def parameters(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": ["get", "list"],
                    "description": "Action to perform: 'get' retrieves a value by key, 'list' shows all keys.",
                },
                "key": {
                    "type": "string",
                    "description": "Memory key to retrieve (required for 'get' action).",
                },
            },
            "required": ["action"],
        }

    @property
    def risk_level(self) -> ToolRisk:
        return ToolRisk.READ_ONLY

    async def execute(self, **kwargs) -> ToolResult:
        action = kwargs["action"]

        if action == "list":
            keys = await self._provider.list_keys(self._workspace_id)
            if not keys:
                return ToolResult(success=True, content="No memory entries found.")
            return ToolResult(
                success=True,
                content="Memory keys:\n" + "\n".join(f"- {k}" for k in keys),
                data={"keys": keys},
            )

        if action == "get":
            key = kwargs.get("key")
            if not key:
                return ToolResult(
                    success=False,
                    content="Key is required for 'get' action.",
                    error="Missing required parameter: key",
                    error_code=ErrorCode.VALIDATION_ERROR,
                )
            value = await self._provider.get(self._workspace_id, key)
            if value is None:
                return ToolResult(
                    success=True,
                    content=f"No memory entry found for key: {key}",
                )
            return ToolResult(
                success=True,
                content=value,
                data={"key": key, "value": value},
            )

        return ToolResult(
            success=False,
            content=f"Unknown action: {action}",
            error=f"Unknown action: {action}",
            error_code=ErrorCode.VALIDATION_ERROR,
        )


class MemoryWriteTool(Tool):
    """Write or delete workspace memory entries."""

    def __init__(self, provider: MemoryProvider, workspace_id: str) -> None:
        self._provider = provider
        self._workspace_id = workspace_id

    @property
    def name(self) -> str:
        return "memory_write"

    @property
    def description(self) -> str:
        return (
            "Write or delete workspace memory. Use action 'set' to store a "
            "key-value pair, or 'delete' to remove an entry."
        )

    @property
    def parameters(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": ["set", "delete"],
                    "description": "Action to perform: 'set' stores a value, 'delete' removes an entry.",
                },
                "key": {
                    "type": "string",
                    "description": "Memory key to write or delete.",
                },
                "value": {
                    "type": "string",
                    "description": "Value to store (required for 'set' action).",
                },
            },
            "required": ["action", "key"],
        }

    @property
    def risk_level(self) -> ToolRisk:
        return ToolRisk.WRITE

    async def execute(self, **kwargs) -> ToolResult:
        action = kwargs["action"]
        key = kwargs["key"]

        if action == "set":
            value = kwargs.get("value")
            if not value:
                return ToolResult(
                    success=False,
                    content="Value is required for 'set' action.",
                    error="Missing required parameter: value",
                    error_code=ErrorCode.VALIDATION_ERROR,
                )
            await self._provider.set(self._workspace_id, key, value)
            return ToolResult(
                success=True,
                content=f"Stored memory entry: {key}",
                data={"key": key},
            )

        if action == "delete":
            deleted = await self._provider.delete(self._workspace_id, key)
            if deleted:
                return ToolResult(
                    success=True,
                    content=f"Deleted memory entry: {key}",
                    data={"key": key, "deleted": True},
                )
            return ToolResult(
                success=True,
                content=f"No memory entry found for key: {key}",
                data={"key": key, "deleted": False},
            )

        return ToolResult(
            success=False,
            content=f"Unknown action: {action}",
            error=f"Unknown action: {action}",
            error_code=ErrorCode.VALIDATION_ERROR,
        )


async def build_memory_context(
    sqlite_provider: MemoryProvider,
    file_provider: MemoryProvider,
    workspace_id: str,
    workspace_path: str,
) -> str:
    """Build a memory context string for system prompt injection.

    Reads from both SQLite (persistent agent memory) and file provider
    (workspace files like CLAUDE.md, README.md) and formats them as
    a markdown section suitable for prepending to the system prompt.
    """
    sections: list[str] = []

    # File-based memory (CLAUDE.md, README.md, etc.)
    try:
        file_entries = await file_provider.get_all(workspace_path)
        for key, content in file_entries.items():
            # Truncate large files to keep context reasonable
            truncated = content[:4000]
            if len(content) > 4000:
                truncated += "\n... (truncated)"
            sections.append(f"### {key}\n{truncated}")
    except Exception:
        logger.warning("Failed to read file memory for %s", workspace_path, exc_info=True)

    # SQLite-stored memory (agent-written notes)
    try:
        sqlite_entries = await sqlite_provider.get_all(workspace_id)
        for key, entry in sqlite_entries.items():
            value = entry["value"] if isinstance(entry, dict) else entry
            sections.append(f"### {key}\n{value}")
    except Exception:
        logger.warning("Failed to read sqlite memory for %s", workspace_id, exc_info=True)

    if not sections:
        return ""

    return "## Workspace Memory\n\n" + "\n\n".join(sections) + "\n\n"
