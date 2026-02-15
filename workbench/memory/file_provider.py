"""File-based memory provider for workspace-local memory files."""
from __future__ import annotations

import logging
from pathlib import Path

from workbench.memory.provider import MemoryProvider

logger = logging.getLogger(__name__)

# Files to look for in workspace directories
MEMORY_FILES = [
    "CLAUDE.md",
    ".workbench/memory.md",
    ".workbench/memory.json",
    "README.md",
]


class FileMemoryProvider(MemoryProvider):
    """
    Read-only memory provider that reads files from workspace paths.

    Useful for loading workspace-local context like CLAUDE.md, README.md, etc.
    """

    async def get(self, workspace_id: str, key: str) -> str | None:
        """Key is treated as a relative file path within the workspace."""
        # workspace_id is actually the workspace path for file provider
        path = Path(workspace_id) / key
        if path.is_file():
            try:
                return path.read_text(encoding="utf-8")
            except Exception as e:
                logger.warning("Failed to read memory file %s: %s", path, e)
        return None

    async def set(self, workspace_id: str, key: str, value: str) -> None:
        """Write a memory file to the workspace."""
        path = Path(workspace_id) / key
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(value, encoding="utf-8")

    async def delete(self, workspace_id: str, key: str) -> bool:
        """Delete a memory file from the workspace."""
        path = Path(workspace_id) / key
        if path.is_file():
            path.unlink()
            return True
        return False

    async def list_keys(self, workspace_id: str) -> list[str]:
        """List known memory files that exist in the workspace."""
        result = []
        ws_path = Path(workspace_id)
        if not ws_path.is_dir():
            return result
        for fname in MEMORY_FILES:
            if (ws_path / fname).is_file():
                result.append(fname)
        return result

    async def get_all(self, workspace_id: str) -> dict[str, str]:
        """Read all known memory files."""
        result = {}
        keys = await self.list_keys(workspace_id)
        for key in keys:
            content = await self.get(workspace_id, key)
            if content is not None:
                result[key] = content
        return result
