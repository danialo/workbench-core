"""
SQLite-backed session and event store.

Uses ``aiosqlite`` for async database access with a write lock to serialise
mutations (SQLite only supports one writer at a time in WAL mode).

Schema is version-tracked via a ``schema_version`` table.  Migrations are
applied automatically on ``init()``.
"""

from __future__ import annotations

import asyncio
import json
import uuid
from datetime import datetime, timezone
from pathlib import Path

import aiosqlite

from workbench.session.events import SessionEvent

# ---------------------------------------------------------------------------
# Schema management
# ---------------------------------------------------------------------------

SCHEMA_VERSION = 1

MIGRATIONS: dict[int, list[str]] = {
    1: [
        """CREATE TABLE IF NOT EXISTS schema_version (
            version INTEGER NOT NULL
        )""",
        """CREATE TABLE IF NOT EXISTS sessions (
            session_id TEXT PRIMARY KEY,
            created_at TEXT NOT NULL,
            metadata TEXT NOT NULL DEFAULT '{}'
        )""",
        """CREATE TABLE IF NOT EXISTS events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id TEXT NOT NULL,
            event_id TEXT NOT NULL UNIQUE,
            turn_id TEXT NOT NULL,
            event_type TEXT NOT NULL,
            timestamp TEXT NOT NULL,
            payload TEXT NOT NULL,
            FOREIGN KEY (session_id) REFERENCES sessions(session_id) ON DELETE CASCADE
        )""",
        """CREATE INDEX IF NOT EXISTS idx_events_session ON events(session_id)""",
        """CREATE INDEX IF NOT EXISTS idx_events_turn ON events(turn_id)""",
    ],
}


# ---------------------------------------------------------------------------
# Store
# ---------------------------------------------------------------------------


class SessionStore:
    """
    Async SQLite store for sessions and their events.

    Usage::

        store = SessionStore("~/.workbench/sessions.db")
        await store.init()
        sid = await store.create_session()
        await store.append_event(sid, event)
        events = await store.get_events(sid)
        await store.close()
    """

    def __init__(self, db_path: str) -> None:
        self.db_path = Path(db_path).expanduser().resolve()
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._write_lock = asyncio.Lock()
        self._db: aiosqlite.Connection | None = None

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def init(self) -> None:
        """Open the database and ensure the schema is up to date."""
        self._db = await aiosqlite.connect(str(self.db_path))
        self._db.row_factory = aiosqlite.Row
        await self._db.execute("PRAGMA journal_mode=WAL")
        await self._db.execute("PRAGMA foreign_keys=ON")
        await self._run_migrations()

    async def close(self) -> None:
        """Close the database connection."""
        if self._db is not None:
            await self._db.close()
            self._db = None

    # ------------------------------------------------------------------
    # Migration runner
    # ------------------------------------------------------------------

    async def _get_schema_version(self) -> int:
        """Return the current schema version, or 0 if not initialised."""
        assert self._db is not None
        # Check whether the schema_version table exists at all.
        cursor = await self._db.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='schema_version'"
        )
        row = await cursor.fetchone()
        if row is None:
            return 0
        cursor = await self._db.execute("SELECT version FROM schema_version LIMIT 1")
        row = await cursor.fetchone()
        if row is None:
            return 0
        return int(row[0])

    async def _set_schema_version(self, version: int) -> None:
        assert self._db is not None
        cursor = await self._db.execute("SELECT COUNT(*) FROM schema_version")
        row = await cursor.fetchone()
        assert row is not None
        if row[0] == 0:
            await self._db.execute(
                "INSERT INTO schema_version (version) VALUES (?)", (version,)
            )
        else:
            await self._db.execute(
                "UPDATE schema_version SET version = ?", (version,)
            )

    async def _run_migrations(self) -> None:
        """Apply any pending migrations sequentially."""
        assert self._db is not None
        current = await self._get_schema_version()
        target = SCHEMA_VERSION

        if current >= target:
            return

        for version in range(current + 1, target + 1):
            stmts = MIGRATIONS.get(version)
            if stmts is None:
                raise RuntimeError(
                    f"Missing migration for schema version {version}"
                )
            for stmt in stmts:
                await self._db.execute(stmt)
            await self._set_schema_version(version)

        await self._db.commit()

    # ------------------------------------------------------------------
    # Session CRUD
    # ------------------------------------------------------------------

    async def create_session(self, metadata: dict | None = None) -> str:
        """Create a new session and return its id."""
        assert self._db is not None
        session_id = str(uuid.uuid4())
        now = datetime.now(timezone.utc).isoformat()
        meta_json = json.dumps(metadata or {})

        async with self._write_lock:
            await self._db.execute(
                "INSERT INTO sessions (session_id, created_at, metadata) VALUES (?, ?, ?)",
                (session_id, now, meta_json),
            )
            await self._db.commit()

        return session_id

    async def get_session(self, session_id: str) -> dict | None:
        """Return session metadata dict, or ``None`` if not found."""
        assert self._db is not None
        cursor = await self._db.execute(
            "SELECT session_id, created_at, metadata FROM sessions WHERE session_id = ?",
            (session_id,),
        )
        row = await cursor.fetchone()
        if row is None:
            return None
        return {
            "session_id": row[0],
            "created_at": row[1],
            "metadata": json.loads(row[2]),
        }

    async def list_sessions(self) -> list[dict]:
        """Return all sessions ordered by creation time (newest first)."""
        assert self._db is not None
        cursor = await self._db.execute(
            "SELECT session_id, created_at, metadata FROM sessions ORDER BY created_at DESC"
        )
        rows = await cursor.fetchall()
        return [
            {
                "session_id": row[0],
                "created_at": row[1],
                "metadata": json.loads(row[2]),
            }
            for row in rows
        ]

    async def delete_session(self, session_id: str) -> None:
        """Delete a session and all its events."""
        assert self._db is not None
        async with self._write_lock:
            # Delete events first (foreign key cascade should handle this but
            # we're explicit for clarity and portability).
            await self._db.execute(
                "DELETE FROM events WHERE session_id = ?", (session_id,)
            )
            await self._db.execute(
                "DELETE FROM sessions WHERE session_id = ?", (session_id,)
            )
            await self._db.commit()

    # ------------------------------------------------------------------
    # Event operations
    # ------------------------------------------------------------------

    async def append_event(self, session_id: str, event: SessionEvent) -> None:
        """Persist a new event to the given session."""
        assert self._db is not None
        payload_json = json.dumps(event.payload)
        ts = event.timestamp.isoformat()

        async with self._write_lock:
            await self._db.execute(
                """INSERT INTO events
                   (session_id, event_id, turn_id, event_type, timestamp, payload)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (
                    session_id,
                    event.event_id,
                    event.turn_id,
                    event.event_type,
                    ts,
                    payload_json,
                ),
            )
            await self._db.commit()

    async def get_events(
        self,
        session_id: str,
        event_type: str | None = None,
    ) -> list[SessionEvent]:
        """
        Return events for a session in chronological order.

        Optionally filter by ``event_type``.
        """
        assert self._db is not None
        if event_type is not None:
            cursor = await self._db.execute(
                """SELECT event_id, turn_id, event_type, timestamp, payload
                   FROM events
                   WHERE session_id = ? AND event_type = ?
                   ORDER BY id ASC""",
                (session_id, event_type),
            )
        else:
            cursor = await self._db.execute(
                """SELECT event_id, turn_id, event_type, timestamp, payload
                   FROM events
                   WHERE session_id = ?
                   ORDER BY id ASC""",
                (session_id,),
            )
        rows = await cursor.fetchall()
        events: list[SessionEvent] = []
        for row in rows:
            events.append(
                SessionEvent(
                    event_id=row[0],
                    turn_id=row[1],
                    event_type=row[2],
                    timestamp=datetime.fromisoformat(row[3]),
                    payload=json.loads(row[4]),
                )
            )
        return events

    async def get_schema_version(self) -> int:
        """Public accessor for the current schema version."""
        return await self._get_schema_version()
