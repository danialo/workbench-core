"""
SQLite-backed document graph store.

Separate from the session/investigation databases — clean domain boundary.

Schema philosophy:
- document_events: append-only canonical log (one row per doc.* event)
- documents: materialized current state (rebuilt from events on replay)
- artifact_indexes: derived per-artifact line/byte maps (rebuildable)

Write lock serializes all mutations (same pattern as SessionStore).
"""

from __future__ import annotations

import asyncio
import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import aiosqlite

# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------

SCHEMA_VERSION = 1

MIGRATIONS: dict[int, list[str]] = {
    1: [
        # Version tracking
        """CREATE TABLE IF NOT EXISTS schema_version (
            version INTEGER NOT NULL
        )""",

        # Append-only event log — one row per doc.* event
        """CREATE TABLE IF NOT EXISTS document_events (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            event_id        TEXT NOT NULL UNIQUE,
            investigation_id TEXT NOT NULL,
            document_id     TEXT NOT NULL,
            actor_id        TEXT NOT NULL,
            actor_type      TEXT NOT NULL DEFAULT 'human',
            actor_source    TEXT NOT NULL DEFAULT 'placeholder',
            event_type      TEXT NOT NULL,
            occurred_at     TEXT NOT NULL,
            payload         TEXT NOT NULL,
            prior_revision  INTEGER NOT NULL,
            next_revision   INTEGER NOT NULL
        )""",
        """CREATE INDEX IF NOT EXISTS idx_docevents_doc
            ON document_events(investigation_id, document_id)""",
        """CREATE INDEX IF NOT EXISTS idx_docevents_inv
            ON document_events(investigation_id)""",
        """CREATE INDEX IF NOT EXISTS idx_docevents_type
            ON document_events(event_type)""",

        # Materialized current state — rebuilt from events on replay
        """CREATE TABLE IF NOT EXISTS documents (
            document_id      TEXT NOT NULL,
            investigation_id TEXT NOT NULL,
            current_revision INTEGER NOT NULL DEFAULT 0,
            state            TEXT NOT NULL DEFAULT '{}',
            created_at       TEXT NOT NULL,
            updated_at       TEXT NOT NULL,
            PRIMARY KEY (document_id, investigation_id)
        )""",
        """CREATE INDEX IF NOT EXISTS idx_documents_inv
            ON documents(investigation_id)""",

        # Derived artifact indexes — rebuildable, keyed by artifact sha256
        """CREATE TABLE IF NOT EXISTS artifact_indexes (
            artifact_ref    TEXT PRIMARY KEY,
            index_ref       TEXT NOT NULL,
            index_version   INTEGER NOT NULL DEFAULT 1,
            indexer_build   TEXT NOT NULL,
            indexed_at      TEXT NOT NULL,
            line_map        TEXT NOT NULL DEFAULT '{}',
            reverse_map     TEXT NOT NULL DEFAULT '{}'
        )""",
    ],
}

# Bump this when adding indexer features; stored in artifact_indexes.indexer_build
INDEXER_BUILD = "1.0.0"


# ---------------------------------------------------------------------------
# Actor resolution helpers
# ---------------------------------------------------------------------------

def resolve_actor(
    actor_id: str | None = None,
    actor_type: str | None = None,
    session_meta: dict | None = None,
) -> tuple[str, str, str]:
    """
    Return (actor_id, actor_type, actor_source) using precedence:
    1. Explicit header/param values
    2. Derived from session metadata
    3. Deterministic placeholder
    """
    if actor_id:
        return (
            actor_id,
            actor_type or "human",
            "header",
        )

    if session_meta:
        sid = session_meta.get("session_id") or session_meta.get("actor_id")
        atype = session_meta.get("actor_type", "agent")
        if sid:
            return (f"{atype}:{sid}", atype, "session")

    # Placeholder
    atype = actor_type or "human"
    placeholder = f"{atype}:unknown" if atype != "system" else "system:ise"
    return (placeholder, atype, "placeholder")


# ---------------------------------------------------------------------------
# Document state — block graph operations
# ---------------------------------------------------------------------------

def _apply_event(state: dict[str, Any], event_type: str, payload: dict[str, Any]) -> dict[str, Any]:
    """
    Apply a single document event to a state dict, returning updated state.

    State shape:
    {
        "blocks": { block_id: {block data} },
        "block_order": [block_id, ...],          # insertion order
        "assertion_states": { assertion_id: "draft|submitted|approved|rejected" },
    }
    """
    state = dict(state)  # shallow copy at top level
    blocks = dict(state.get("blocks", {}))
    block_order = list(state.get("block_order", []))
    assertion_states = dict(state.get("assertion_states", {}))

    if event_type in (
        "doc.command.created",
        "doc.output.created",
        "doc.assertion.created",
        "doc.review.created",
        "doc.narrative.regenerated",
        "doc.derivation.created",
    ):
        block = payload.get("block", {})
        bid = block.get("id")
        if bid:
            blocks[bid] = block
            if bid not in block_order:
                block_order.append(bid)
            # Track assertion workflow state (only draft/submitted from the block;
            # approved/rejected are derived exclusively from review events)
            if block.get("type") == "assertion":
                ws = block.get("workflow_state", "draft")
                if ws not in ("draft", "submitted"):
                    ws = "draft"
                assertion_states[bid] = ws
            # Apply review decisions — latest review wins, no guard
            if block.get("type") == "review":
                decision = block.get("decision")
                for aid in block.get("target_assertion_ids", []):
                    assertion_states[aid] = decision

    elif event_type == "doc.assertion.patched":
        bid = payload.get("assertion_id")
        patch = payload.get("patch", {})
        if bid and bid in blocks:
            updated = dict(blocks[bid])
            updated.update(patch)
            blocks[bid] = updated
            if "workflow_state" in patch:
                assertion_states[bid] = patch["workflow_state"]

    state["blocks"] = blocks
    state["block_order"] = block_order
    state["assertion_states"] = assertion_states
    return state


def replay_events(events: list[dict[str, Any]]) -> dict[str, Any]:
    """
    Reconstruct document state by replaying events in revision order.
    Events must be sorted ascending by next_revision.
    """
    state: dict[str, Any] = {}
    for ev in sorted(events, key=lambda e: e["next_revision"]):
        state = _apply_event(state, ev["event_type"], json.loads(ev["payload"]))
    return state


def replay_events_at_revision(
    events: list[dict[str, Any]], revision: int
) -> dict[str, Any]:
    """Replay only events with next_revision <= revision."""
    filtered = [e for e in events if e["next_revision"] <= revision]
    return replay_events(filtered)


# ---------------------------------------------------------------------------
# DocumentStore
# ---------------------------------------------------------------------------


class DocumentStore:
    """
    Async SQLite store for the document block graph.

    Separate DB from sessions/investigations.  Same append-only event
    philosophy as SessionStore — mutations are new events + materialized state
    refresh.

    Usage::

        store = DocumentStore("~/.workbench/documents.db")
        await store.init()
        doc_id = await store.create_document(investigation_id="...")
        await store.append_event(investigation_id, doc_id, actor, event_type, payload)
        state = await store.get_state(investigation_id, doc_id)
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
        self._db = await aiosqlite.connect(str(self.db_path))
        self._db.row_factory = aiosqlite.Row
        await self._db.execute("PRAGMA journal_mode=WAL")
        await self._db.execute("PRAGMA foreign_keys=ON")
        await self._run_migrations()

    async def close(self) -> None:
        if self._db is not None:
            await self._db.close()
            self._db = None

    # ------------------------------------------------------------------
    # Migration runner
    # ------------------------------------------------------------------

    async def _get_schema_version(self) -> int:
        assert self._db is not None
        cursor = await self._db.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='schema_version'"
        )
        if await cursor.fetchone() is None:
            return 0
        cursor = await self._db.execute("SELECT version FROM schema_version LIMIT 1")
        row = await cursor.fetchone()
        return int(row[0]) if row else 0

    async def _set_schema_version(self, version: int) -> None:
        assert self._db is not None
        cursor = await self._db.execute("SELECT COUNT(*) FROM schema_version")
        row = await cursor.fetchone()
        assert row is not None
        if row[0] == 0:
            await self._db.execute("INSERT INTO schema_version (version) VALUES (?)", (version,))
        else:
            await self._db.execute("UPDATE schema_version SET version = ?", (version,))

    async def _run_migrations(self) -> None:
        assert self._db is not None
        current = await self._get_schema_version()
        if current >= SCHEMA_VERSION:
            return
        for version in range(current + 1, SCHEMA_VERSION + 1):
            stmts = MIGRATIONS.get(version)
            if stmts is None:
                raise RuntimeError(f"Missing migration for schema version {version}")
            for stmt in stmts:
                await self._db.execute(stmt)
            await self._set_schema_version(version)
        await self._db.commit()

    async def get_schema_version(self) -> int:
        return await self._get_schema_version()

    # ------------------------------------------------------------------
    # Document lifecycle
    # ------------------------------------------------------------------

    async def create_document(self, investigation_id: str) -> str:
        """Create a new document scoped to an investigation. Returns document_id."""
        assert self._db is not None
        document_id = str(uuid.uuid4())
        now = datetime.now(timezone.utc).isoformat()
        async with self._write_lock:
            await self._db.execute(
                """INSERT INTO documents
                   (document_id, investigation_id, current_revision, state, created_at, updated_at)
                   VALUES (?, ?, 0, '{}', ?, ?)""",
                (document_id, investigation_id, now, now),
            )
            await self._db.commit()
        return document_id

    async def get_document(self, investigation_id: str, document_id: str) -> dict | None:
        """Return document metadata + current state, or None if not found."""
        assert self._db is not None
        cursor = await self._db.execute(
            """SELECT document_id, investigation_id, current_revision, state, created_at, updated_at
               FROM documents WHERE investigation_id = ? AND document_id = ?""",
            (investigation_id, document_id),
        )
        row = await cursor.fetchone()
        if row is None:
            return None
        return {
            "document_id": row[0],
            "investigation_id": row[1],
            "current_revision": row[2],
            "state": json.loads(row[3]),
            "created_at": row[4],
            "updated_at": row[5],
        }

    async def list_documents(self, investigation_id: str) -> list[dict]:
        """List all documents for an investigation (summary, no full state)."""
        assert self._db is not None
        cursor = await self._db.execute(
            """SELECT document_id, investigation_id, current_revision, created_at, updated_at
               FROM documents WHERE investigation_id = ? ORDER BY created_at ASC""",
            (investigation_id,),
        )
        rows = await cursor.fetchall()
        return [
            {
                "document_id": row[0],
                "investigation_id": row[1],
                "current_revision": row[2],
                "created_at": row[3],
                "updated_at": row[4],
            }
            for row in rows
        ]

    # ------------------------------------------------------------------
    # Event append + state materialization
    # ------------------------------------------------------------------

    async def append_event(
        self,
        investigation_id: str,
        document_id: str,
        actor_id: str,
        actor_type: str,
        actor_source: str,
        event_type: str,
        payload: dict[str, Any],
        *,
        expected_revision: int | None = None,
    ) -> dict[str, Any]:
        """
        Append a document event and refresh materialized state.

        For append-only creates (doc.*.created), expected_revision is not
        required.  For mutations (doc.assertion.patched, doc.narrative.regenerated),
        expected_revision must match current_revision or a 409-style dict is
        returned instead of raising.

        Returns:
            {"ok": True, "event_id": ..., "revision": ...}
          or
            {"ok": False, "conflict": True, "current_revision": ..., "changed_block_ids": [...]}
        """
        assert self._db is not None

        MUTATION_EVENTS = {"doc.assertion.patched", "doc.narrative.regenerated"}

        async with self._write_lock:
            # Load current document
            cursor = await self._db.execute(
                "SELECT current_revision, state FROM documents WHERE investigation_id = ? AND document_id = ?",
                (investigation_id, document_id),
            )
            row = await cursor.fetchone()
            if row is None:
                raise ValueError(f"Document not found: {document_id} in investigation {investigation_id}")

            current_revision = row[0]
            current_state = json.loads(row[1])

            # Optimistic locking check for mutations
            if event_type in MUTATION_EVENTS and expected_revision is not None:
                if expected_revision != current_revision:
                    # Build changed block IDs since expected_revision
                    changed = await self._changed_blocks_since(
                        investigation_id, document_id, expected_revision
                    )
                    return {
                        "ok": False,
                        "conflict": True,
                        "current_revision": current_revision,
                        "changed_block_ids": changed,
                    }

            # Compute new revision
            prior_revision = current_revision
            next_revision = current_revision + 1

            # Apply event to state
            new_state = _apply_event(current_state, event_type, payload)

            # Persist event
            event_id = str(uuid.uuid4())
            now = datetime.now(timezone.utc).isoformat()
            await self._db.execute(
                """INSERT INTO document_events
                   (event_id, investigation_id, document_id, actor_id, actor_type, actor_source,
                    event_type, occurred_at, payload, prior_revision, next_revision)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    event_id,
                    investigation_id,
                    document_id,
                    actor_id,
                    actor_type,
                    actor_source,
                    event_type,
                    now,
                    json.dumps(payload),
                    prior_revision,
                    next_revision,
                ),
            )

            # Update materialized state
            await self._db.execute(
                """UPDATE documents
                   SET current_revision = ?, state = ?, updated_at = ?
                   WHERE investigation_id = ? AND document_id = ?""",
                (next_revision, json.dumps(new_state), now, investigation_id, document_id),
            )

            await self._db.commit()

        return {"ok": True, "event_id": event_id, "revision": next_revision}

    async def _changed_blocks_since(
        self, investigation_id: str, document_id: str, since_revision: int
    ) -> list[str]:
        """Return block IDs touched by events after since_revision."""
        assert self._db is not None
        cursor = await self._db.execute(
            """SELECT payload FROM document_events
               WHERE investigation_id = ? AND document_id = ? AND next_revision > ?
               ORDER BY next_revision ASC""",
            (investigation_id, document_id, since_revision),
        )
        rows = await cursor.fetchall()
        block_ids: list[str] = []
        seen: set[str] = set()
        for row in rows:
            payload = json.loads(row[0])
            bid = (
                payload.get("block", {}).get("id")
                or payload.get("assertion_id")
            )
            if bid and bid not in seen:
                seen.add(bid)
                block_ids.append(bid)
        return block_ids

    # ------------------------------------------------------------------
    # Event log access
    # ------------------------------------------------------------------

    async def get_events(
        self,
        investigation_id: str,
        document_id: str,
        *,
        event_type: str | None = None,
    ) -> list[dict[str, Any]]:
        """Return events for a document in revision order."""
        assert self._db is not None
        if event_type is not None:
            cursor = await self._db.execute(
                """SELECT event_id, investigation_id, document_id, actor_id, actor_type,
                          actor_source, event_type, occurred_at, payload, prior_revision, next_revision
                   FROM document_events
                   WHERE investigation_id = ? AND document_id = ? AND event_type = ?
                   ORDER BY next_revision ASC""",
                (investigation_id, document_id, event_type),
            )
        else:
            cursor = await self._db.execute(
                """SELECT event_id, investigation_id, document_id, actor_id, actor_type,
                          actor_source, event_type, occurred_at, payload, prior_revision, next_revision
                   FROM document_events
                   WHERE investigation_id = ? AND document_id = ?
                   ORDER BY next_revision ASC""",
                (investigation_id, document_id),
            )
        rows = await cursor.fetchall()
        return [
            {
                "event_id": row[0],
                "investigation_id": row[1],
                "document_id": row[2],
                "actor_id": row[3],
                "actor_type": row[4],
                "actor_source": row[5],
                "event_type": row[6],
                "occurred_at": row[7],
                "payload": row[8],   # raw JSON string — caller decodes as needed
                "prior_revision": row[9],
                "next_revision": row[10],
            }
            for row in rows
        ]

    # ------------------------------------------------------------------
    # Deterministic replay
    # ------------------------------------------------------------------

    async def get_state(
        self, investigation_id: str, document_id: str, *, at_revision: int | None = None
    ) -> dict[str, Any] | None:
        """
        Return document state.

        If at_revision is None, returns the materialized current state (fast).
        If at_revision is specified, replays events up to that revision (deterministic).
        """
        assert self._db is not None

        if at_revision is None:
            doc = await self.get_document(investigation_id, document_id)
            return doc["state"] if doc else None

        # Replay path
        raw_events = await self.get_events(investigation_id, document_id)
        if not raw_events:
            return None
        return replay_events_at_revision(raw_events, at_revision)

    # ------------------------------------------------------------------
    # Artifact index store
    # ------------------------------------------------------------------

    async def store_artifact_index(
        self,
        artifact_ref: str,
        line_map: dict[int, tuple[int, int]],
        reverse_map: dict[tuple[int, int], tuple[int, int]],
    ) -> str:
        """
        Persist a line/byte index for an artifact.

        line_map: { line_number(0-based) -> (byte_start, byte_end) }
        reverse_map: { (byte_start, byte_end) -> (line_start, line_end) }

        Returns the index_ref (sha256 of the line_map content).
        """
        assert self._db is not None
        import hashlib

        # Normalize keys for JSON serialization (tuples -> lists)
        lm_serializable = {str(k): list(v) for k, v in line_map.items()}
        rm_serializable = {f"{k[0]}:{k[1]}": list(v) for k, v in reverse_map.items()}

        lm_json = json.dumps(lm_serializable, sort_keys=True)
        index_ref = hashlib.sha256(lm_json.encode()).hexdigest()
        now = datetime.now(timezone.utc).isoformat()

        async with self._write_lock:
            await self._db.execute(
                """INSERT OR REPLACE INTO artifact_indexes
                   (artifact_ref, index_ref, index_version, indexer_build, indexed_at, line_map, reverse_map)
                   VALUES (?, ?, 1, ?, ?, ?, ?)""",
                (artifact_ref, index_ref, INDEXER_BUILD, now, lm_json, json.dumps(rm_serializable)),
            )
            await self._db.commit()

        return index_ref

    async def get_artifact_index(self, artifact_ref: str) -> dict | None:
        """Return the stored index for an artifact, or None if not indexed."""
        assert self._db is not None
        cursor = await self._db.execute(
            """SELECT artifact_ref, index_ref, index_version, indexer_build, indexed_at, line_map, reverse_map
               FROM artifact_indexes WHERE artifact_ref = ?""",
            (artifact_ref,),
        )
        row = await cursor.fetchone()
        if row is None:
            return None
        return {
            "artifact_ref": row[0],
            "index_ref": row[1],
            "index_version": row[2],
            "indexer_build": row[3],
            "indexed_at": row[4],
            "line_map": json.loads(row[5]),
            "reverse_map": json.loads(row[6]),
        }
