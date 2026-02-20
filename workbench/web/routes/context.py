"""Context pill CRUD endpoints for the workspace-scoped context bar."""

from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import aiosqlite
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/workspaces", tags=["context"])


# -----------------------------------------------------------------------
# Pydantic models
# -----------------------------------------------------------------------

class CreatePillRequest(BaseModel):
    """Request to create a new context pill."""
    pill_type: str = Field(..., pattern=r"^(custom|timeline)$")
    label: str = Field(..., min_length=1, max_length=200)
    fields: dict[str, Any] = Field(default_factory=dict)


class UpdatePillRequest(BaseModel):
    """Request to update a context pill."""
    label: str | None = None
    enabled: bool | None = None
    fields: dict[str, Any] | None = None
    sort_order: int | None = None


# -----------------------------------------------------------------------
# Table setup
# -----------------------------------------------------------------------

async def ensure_context_pills_table(db_path: str) -> None:
    """Create the context_pills table if it doesn't exist."""
    resolved = str(Path(db_path).expanduser())
    async with aiosqlite.connect(resolved) as db:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS context_pills (
                pill_id TEXT PRIMARY KEY,
                workspace_id TEXT NOT NULL,
                pill_type TEXT NOT NULL,
                label TEXT NOT NULL,
                enabled INTEGER DEFAULT 1,
                fields TEXT NOT NULL DEFAULT '{}',
                sort_order INTEGER DEFAULT 0,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
        """)
        await db.execute("""
            CREATE INDEX IF NOT EXISTS idx_context_pills_workspace
            ON context_pills(workspace_id)
        """)
        await db.commit()


def _get_db_path(request: Request) -> str:
    return request.app.state.investigations_db_path


def _parse_pill(row: dict) -> dict:
    """Parse JSON fields and convert enabled to bool."""
    if isinstance(row.get("fields"), str):
        try:
            row["fields"] = json.loads(row["fields"])
        except (json.JSONDecodeError, TypeError):
            row["fields"] = {}
    row["enabled"] = bool(row.get("enabled", 1))
    return row


# -----------------------------------------------------------------------
# Context builder — formats enabled pills into system prompt prefix
# -----------------------------------------------------------------------

async def build_context_pills_prefix(
    db_path: str,
    workspace_id: str,
    date_format: str = "YYYY-MM-DD",
) -> str:
    """Build system prompt section from enabled context pills.

    Returns a markdown string like:
        ## Workspace Context

        Environment: production
        Timeline: 2026-02-15 to 2026-02-20

    Only includes pills that are enabled and have at least one enabled field
    with a non-empty value.
    """
    resolved = str(Path(db_path).expanduser())
    try:
        async with aiosqlite.connect(resolved) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                "SELECT * FROM context_pills WHERE workspace_id = ? AND enabled = 1 ORDER BY sort_order, created_at",
                (workspace_id,),
            )
            rows = await cursor.fetchall()
    except Exception:
        logger.debug("Could not fetch context pills for workspace %s", workspace_id, exc_info=True)
        return ""

    if not rows:
        return ""

    parts: list[str] = []
    for row in rows:
        pill = _parse_pill(dict(row))
        pill_type = pill.get("pill_type", "custom")
        label = pill.get("label", "")
        fields = pill.get("fields", {})

        if pill_type == "custom":
            value_field = fields.get("value", {})
            if isinstance(value_field, dict) and value_field.get("enabled", True):
                val = value_field.get("value", "").strip()
                if val:
                    parts.append(f"{label}: {val}")

        elif pill_type == "timeline":
            timeline_parts = []
            start = fields.get("start_date", {})
            end = fields.get("end_date", {})

            if isinstance(start, dict) and start.get("enabled", True):
                val = start.get("value", "").strip()
                if val:
                    timeline_parts.append(f"from {_format_date(val, date_format)}")

            if isinstance(end, dict) and end.get("enabled", True):
                val = end.get("value", "").strip()
                if val:
                    timeline_parts.append(f"to {_format_date(val, date_format)}")

            if timeline_parts:
                parts.append(f"{label}: {' '.join(timeline_parts)}")

    if not parts:
        return ""

    return "## Workspace Context\n\n" + "\n".join(parts) + "\n\n"


def _format_date(iso_str: str, fmt: str) -> str:
    """Format an ISO date string according to the given format spec.

    Supports: YYYY-MM-DD, MM/DD/YYYY, DD/MM/YYYY, MMM D YYYY, YYYY-MM-DD HH:mm
    Falls back to the raw string on parse failure.
    """
    try:
        dt = datetime.fromisoformat(iso_str.replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        return iso_str

    formats = {
        "YYYY-MM-DD": "%Y-%m-%d",
        "MM/DD/YYYY": "%m/%d/%Y",
        "DD/MM/YYYY": "%d/%m/%Y",
        "MMM D YYYY": "%b %-d %Y",
        "YYYY-MM-DD HH:mm": "%Y-%m-%d %H:%M",
    }
    py_fmt = formats.get(fmt, "%Y-%m-%d")
    try:
        return dt.strftime(py_fmt)
    except ValueError:
        # %-d not supported on Windows, fall back
        return dt.strftime(py_fmt.replace("%-d", "%d"))


# -----------------------------------------------------------------------
# Endpoints
# -----------------------------------------------------------------------

@router.get("/{workspace_id}/context")
async def list_context_pills(workspace_id: str, request: Request):
    """List all context pills for a workspace."""
    db_path = _get_db_path(request)
    await ensure_context_pills_table(db_path)

    resolved = str(Path(db_path).expanduser())
    async with aiosqlite.connect(resolved) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            "SELECT * FROM context_pills WHERE workspace_id = ? ORDER BY sort_order, created_at",
            (workspace_id,),
        )
        rows = await cursor.fetchall()

    pills = [_parse_pill(dict(row)) for row in rows]
    return JSONResponse({"pills": pills})


@router.post("/{workspace_id}/context", status_code=201)
async def create_context_pill(workspace_id: str, req: CreatePillRequest, request: Request):
    """Create a new context pill."""
    db_path = _get_db_path(request)
    await ensure_context_pills_table(db_path)

    pill_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc).isoformat()

    # Ensure fields have the right structure
    fields = req.fields
    if req.pill_type == "custom" and "value" not in fields:
        fields["value"] = {"value": "", "enabled": True}
    elif req.pill_type == "timeline":
        if "start_date" not in fields:
            fields["start_date"] = {"value": "", "enabled": True}
        if "end_date" not in fields:
            fields["end_date"] = {"value": "", "enabled": True}

    resolved = str(Path(db_path).expanduser())
    async with aiosqlite.connect(resolved) as db:
        await db.execute(
            """INSERT INTO context_pills
               (pill_id, workspace_id, pill_type, label, enabled, fields, sort_order, created_at, updated_at)
               VALUES (?, ?, ?, ?, 1, ?, 0, ?, ?)""",
            (pill_id, workspace_id, req.pill_type, req.label, json.dumps(fields), now, now),
        )
        await db.commit()

    logger.info("Created context pill %s (%s) for workspace %s", pill_id, req.pill_type, workspace_id)
    return JSONResponse({
        "pill_id": pill_id,
        "workspace_id": workspace_id,
        "pill_type": req.pill_type,
        "label": req.label,
        "enabled": True,
        "fields": fields,
        "sort_order": 0,
        "created_at": now,
        "updated_at": now,
    })


@router.put("/{workspace_id}/context/{pill_id}")
async def update_context_pill(workspace_id: str, pill_id: str, req: UpdatePillRequest, request: Request):
    """Update a context pill (toggle, edit fields, reorder)."""
    db_path = _get_db_path(request)
    await ensure_context_pills_table(db_path)

    now = datetime.now(timezone.utc).isoformat()
    updates: dict[str, Any] = {}

    if req.label is not None:
        updates["label"] = req.label
    if req.enabled is not None:
        updates["enabled"] = 1 if req.enabled else 0
    if req.fields is not None:
        updates["fields"] = json.dumps(req.fields)
    if req.sort_order is not None:
        updates["sort_order"] = req.sort_order

    if not updates:
        raise HTTPException(400, "No fields to update")

    updates["updated_at"] = now
    set_clause = ", ".join(f"{k} = ?" for k in updates)
    values = list(updates.values()) + [pill_id, workspace_id]

    resolved = str(Path(db_path).expanduser())
    async with aiosqlite.connect(resolved) as db:
        result = await db.execute(
            f"UPDATE context_pills SET {set_clause} WHERE pill_id = ? AND workspace_id = ?",
            values,
        )
        await db.commit()
        if result.rowcount == 0:
            raise HTTPException(404, f"Context pill not found: {pill_id}")

    return JSONResponse({"status": "updated", "pill_id": pill_id})


@router.delete("/{workspace_id}/context/{pill_id}")
async def delete_context_pill(workspace_id: str, pill_id: str, request: Request):
    """Delete a context pill."""
    db_path = _get_db_path(request)
    await ensure_context_pills_table(db_path)

    resolved = str(Path(db_path).expanduser())
    async with aiosqlite.connect(resolved) as db:
        result = await db.execute(
            "DELETE FROM context_pills WHERE pill_id = ? AND workspace_id = ?",
            (pill_id, workspace_id),
        )
        await db.commit()
        if result.rowcount == 0:
            raise HTTPException(404, f"Context pill not found: {pill_id}")

    return JSONResponse({"status": "deleted", "pill_id": pill_id})
