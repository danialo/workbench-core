"""Memory provider interface."""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any


class MemoryProvider(ABC):
    """Abstract base for memory storage backends."""

    @abstractmethod
    async def get(self, workspace_id: str, key: str) -> str | None:
        """Get a memory value by key."""

    @abstractmethod
    async def set(self, workspace_id: str, key: str, value: str) -> None:
        """Set a memory value."""

    @abstractmethod
    async def delete(self, workspace_id: str, key: str) -> bool:
        """Delete a memory entry. Returns True if found."""

    @abstractmethod
    async def list_keys(self, workspace_id: str) -> list[str]:
        """List all keys for a workspace."""

    @abstractmethod
    async def get_all(self, workspace_id: str) -> dict[str, str]:
        """Get all key-value pairs for a workspace."""
