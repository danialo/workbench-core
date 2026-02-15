"""SQLite-backed memory provider."""
from __future__ import annotations

import logging
from datetime import datetime, timezone

import aiosqlite

from workbench.memory.provider import MemoryProvider

logger = logging.getLogger(__name__)


class SQLiteMemoryProvider(MemoryProvider):
    """Memory storage using SQLite (shares DB with session store)."""

    def __init__(self, db_path: str) -> None:
        self._db_path = db_path
        self._initialized = False

    async def init(self) -> None:
        """Create memory table if it doesn't exist."""
        async with aiosqlite.connect(self._db_path) as db:
            await db.execute("""
                CREATE TABLE IF NOT EXISTS memory (
                    workspace_id TEXT NOT NULL,
                    key TEXT NOT NULL,
                    value TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY (workspace_id, key)
                )
            """)
            await db.execute("""
                CREATE INDEX IF NOT EXISTS idx_memory_workspace
                ON memory(workspace_id)
            """)
            await db.commit()
        self._initialized = True

    async def _ensure_init(self) -> None:
        if not self._initialized:
            await self.init()

    async def get(self, workspace_id: str, key: str) -> str | None:
        await self._ensure_init()
        async with aiosqlite.connect(self._db_path) as db:
            cursor = await db.execute(
                "SELECT value FROM memory WHERE workspace_id = ? AND key = ?",
                (workspace_id, key),
            )
            row = await cursor.fetchone()
            return row[0] if row else None

    async def set(self, workspace_id: str, key: str, value: str) -> None:
        await self._ensure_init()
        now = datetime.now(timezone.utc).isoformat()
        async with aiosqlite.connect(self._db_path) as db:
            await db.execute(
                """INSERT INTO memory (workspace_id, key, value, updated_at)
                   VALUES (?, ?, ?, ?)
                   ON CONFLICT(workspace_id, key)
                   DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at""",
                (workspace_id, key, value, now),
            )
            await db.commit()

    async def delete(self, workspace_id: str, key: str) -> bool:
        await self._ensure_init()
        async with aiosqlite.connect(self._db_path) as db:
            cursor = await db.execute(
                "DELETE FROM memory WHERE workspace_id = ? AND key = ?",
                (workspace_id, key),
            )
            await db.commit()
            return cursor.rowcount > 0

    async def list_keys(self, workspace_id: str) -> list[str]:
        await self._ensure_init()
        async with aiosqlite.connect(self._db_path) as db:
            cursor = await db.execute(
                "SELECT key FROM memory WHERE workspace_id = ? ORDER BY key",
                (workspace_id,),
            )
            rows = await cursor.fetchall()
            return [r[0] for r in rows]

    async def get_all(self, workspace_id: str) -> dict[str, str]:
        await self._ensure_init()
        async with aiosqlite.connect(self._db_path) as db:
            cursor = await db.execute(
                "SELECT key, value, updated_at FROM memory WHERE workspace_id = ? ORDER BY key",
                (workspace_id,),
            )
            rows = await cursor.fetchall()
            return {r[0]: {"value": r[1], "updated_at": r[2]} for r in rows}
