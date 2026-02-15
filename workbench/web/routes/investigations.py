"""Investigation CRUD endpoints for the Triage window."""

from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import aiosqlite
from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/investigations", tags=["investigations"])


# -----------------------------------------------------------------------
# Pydantic models
# -----------------------------------------------------------------------

class CreateInvestigationRequest(BaseModel):
    """Request to create a new investigation."""
    title: str = Field(..., min_length=1, max_length=500)
    severity: str = Field(default="medium")
    affected_systems: list[str] = Field(default_factory=list)
    description: str = Field(default="")
    workspace_id: str = Field(default="")


class UpdateInvestigationRequest(BaseModel):
    """Request to update an investigation."""
    title: str | None = None
    severity: str | None = None
    status: str | None = None
    affected_systems: list[str] | None = None
    description: str | None = None
    checklist: list[dict] | None = None
    metadata: dict | None = None


# -----------------------------------------------------------------------
# Table setup
# -----------------------------------------------------------------------

async def ensure_investigations_table(db_path: str) -> None:
    """Create the investigations table if it doesn't exist.

    Also migrates from the old 'incidents' table name if present.
    """
    resolved = str(Path(db_path).expanduser())
    async with aiosqlite.connect(resolved) as db:
        # Check if old 'incidents' table exists and rename it
        cursor = await db.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='incidents'"
        )
        if await cursor.fetchone():
            await db.execute("ALTER TABLE incidents RENAME TO investigations")
            # Rename the primary key column
            # SQLite doesn't support RENAME COLUMN before 3.25, so we
            # just keep incident_id as-is in the data — queries alias it
            await db.commit()

        await db.execute("""
            CREATE TABLE IF NOT EXISTS investigations (
                investigation_id TEXT PRIMARY KEY,
                title TEXT NOT NULL,
                severity TEXT NOT NULL DEFAULT 'medium',
                status TEXT NOT NULL DEFAULT 'open',
                affected_systems TEXT DEFAULT '[]',
                description TEXT DEFAULT '',
                session_id TEXT,
                workspace_id TEXT,
                checklist TEXT DEFAULT '[]',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                resolved_at TEXT,
                metadata TEXT DEFAULT '{}'
            )
        """)
        await db.commit()


def _get_db_path(request: Request) -> str:
    return request.app.state.investigations_db_path


def _parse_json_fields(row: dict) -> dict:
    """Parse JSON string fields into Python objects."""
    for field in ("affected_systems", "checklist", "metadata"):
        if isinstance(row.get(field), str):
            try:
                row[field] = json.loads(row[field])
            except (json.JSONDecodeError, TypeError):
                row[field] = [] if field != "metadata" else {}
    return row


# -----------------------------------------------------------------------
# Endpoints
# -----------------------------------------------------------------------

@router.get("")
async def list_investigations(request: Request, status: str = Query("", description="Filter by status")):
    """List investigations, optionally filtered by status."""
    db_path = _get_db_path(request)
    await ensure_investigations_table(db_path)

    resolved = str(Path(db_path).expanduser())
    async with aiosqlite.connect(resolved) as db:
        db.row_factory = aiosqlite.Row
        if status:
            cursor = await db.execute(
                "SELECT * FROM investigations WHERE status = ? ORDER BY created_at DESC",
                (status,),
            )
        else:
            cursor = await db.execute("SELECT * FROM investigations ORDER BY created_at DESC")
        rows = await cursor.fetchall()

    investigations = [_parse_json_fields(dict(row)) for row in rows]
    return JSONResponse({"investigations": investigations})


@router.post("", status_code=201)
async def create_investigation(req: CreateInvestigationRequest, request: Request):
    """Create a new investigation with optional auto-linked session."""
    from workbench.workspace import GLOBAL_WORKSPACE_ID

    db_path = _get_db_path(request)
    await ensure_investigations_table(db_path)

    investigation_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc).isoformat()

    # Auto-create a linked session
    linked_session_id = None
    session_store = request.app.state.session_store
    if session_store is not None:
        ws_id = req.workspace_id or GLOBAL_WORKSPACE_ID
        metadata = {
            "workspace": "investigation",
            "workspace_id": ws_id,
            "investigation_id": investigation_id,
            "status": "active",
        }
        linked_session_id = await session_store.create_session(metadata)

    # Default triage checklist
    checklist = [
        {"label": "Check health endpoints", "checked": False},
        {"label": "Review recent deployments", "checked": False},
        {"label": "Check error rate metrics", "checked": False},
        {"label": "Review relevant logs", "checked": False},
        {"label": "Identify blast radius", "checked": False},
    ]

    ws_id = req.workspace_id or GLOBAL_WORKSPACE_ID
    resolved = str(Path(db_path).expanduser())
    async with aiosqlite.connect(resolved) as db:
        await db.execute(
            """INSERT INTO investigations
               (investigation_id, title, severity, status, affected_systems, description,
                session_id, workspace_id, checklist, created_at, updated_at)
               VALUES (?, ?, ?, 'open', ?, ?, ?, ?, ?, ?, ?)""",
            (
                investigation_id,
                req.title,
                req.severity,
                json.dumps(req.affected_systems),
                req.description,
                linked_session_id,
                ws_id,
                json.dumps(checklist),
                now,
                now,
            ),
        )
        await db.commit()

    logger.info("Created investigation %s: %s", investigation_id, req.title)
    return JSONResponse({
        "investigation_id": investigation_id,
        "session_id": linked_session_id,
        "title": req.title,
        "severity": req.severity,
        "status": "open",
    })


@router.get("/{investigation_id}")
async def get_investigation(investigation_id: str, request: Request):
    """Get investigation detail."""
    db_path = _get_db_path(request)
    await ensure_investigations_table(db_path)

    resolved = str(Path(db_path).expanduser())
    async with aiosqlite.connect(resolved) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            "SELECT * FROM investigations WHERE investigation_id = ?", (investigation_id,)
        )
        row = await cursor.fetchone()

    if row is None:
        raise HTTPException(404, f"Investigation not found: {investigation_id}")

    return JSONResponse(_parse_json_fields(dict(row)))


@router.put("/{investigation_id}")
async def update_investigation(investigation_id: str, req: UpdateInvestigationRequest, request: Request):
    """Update an investigation."""
    db_path = _get_db_path(request)
    await ensure_investigations_table(db_path)

    now = datetime.now(timezone.utc).isoformat()
    fields = {k: v for k, v in req.model_dump().items() if v is not None}
    if not fields:
        raise HTTPException(400, "No fields to update")

    for field in ("affected_systems", "checklist", "metadata"):
        if field in fields:
            fields[field] = json.dumps(fields[field])

    fields["updated_at"] = now
    set_clause = ", ".join(f"{k} = ?" for k in fields)
    values = list(fields.values()) + [investigation_id]

    resolved = str(Path(db_path).expanduser())
    async with aiosqlite.connect(resolved) as db:
        result = await db.execute(
            f"UPDATE investigations SET {set_clause} WHERE investigation_id = ?", values
        )
        await db.commit()
        if result.rowcount == 0:
            raise HTTPException(404, f"Investigation not found: {investigation_id}")

    return JSONResponse({"status": "updated", "investigation_id": investigation_id})


@router.delete("/{investigation_id}")
async def delete_investigation(investigation_id: str, request: Request):
    """Delete an investigation."""
    db_path = _get_db_path(request)
    await ensure_investigations_table(db_path)

    resolved = str(Path(db_path).expanduser())
    async with aiosqlite.connect(resolved) as db:
        result = await db.execute(
            "DELETE FROM investigations WHERE investigation_id = ?", (investigation_id,)
        )
        await db.commit()
        if result.rowcount == 0:
            raise HTTPException(404, f"Investigation not found: {investigation_id}")

    return JSONResponse({"status": "deleted", "investigation_id": investigation_id})


@router.post("/{investigation_id}/escalate")
async def escalate_investigation(investigation_id: str, request: Request):
    """Escalate an investigation."""
    db_path = _get_db_path(request)
    await ensure_investigations_table(db_path)

    now = datetime.now(timezone.utc).isoformat()
    resolved = str(Path(db_path).expanduser())
    async with aiosqlite.connect(resolved) as db:
        result = await db.execute(
            "UPDATE investigations SET status = 'escalated', updated_at = ? WHERE investigation_id = ?",
            (now, investigation_id),
        )
        await db.commit()
        if result.rowcount == 0:
            raise HTTPException(404, f"Investigation not found: {investigation_id}")

    logger.info("Escalated investigation %s", investigation_id)
    return JSONResponse({"status": "escalated", "investigation_id": investigation_id})


@router.post("/{investigation_id}/resolve")
async def resolve_investigation(investigation_id: str, request: Request):
    """Resolve an investigation."""
    db_path = _get_db_path(request)
    await ensure_investigations_table(db_path)

    now = datetime.now(timezone.utc).isoformat()
    resolved_path = str(Path(db_path).expanduser())
    async with aiosqlite.connect(resolved_path) as db:
        result = await db.execute(
            "UPDATE investigations SET status = 'resolved', resolved_at = ?, updated_at = ? WHERE investigation_id = ?",
            (now, now, investigation_id),
        )
        await db.commit()
        if result.rowcount == 0:
            raise HTTPException(404, f"Investigation not found: {investigation_id}")

    logger.info("Resolved investigation %s", investigation_id)
    return JSONResponse({"status": "resolved", "investigation_id": investigation_id})
