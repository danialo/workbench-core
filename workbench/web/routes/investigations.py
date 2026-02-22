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
    session_id: str | None = None
    metadata: dict | None = None


class FetchCaseRequest(BaseModel):
    """Request to fetch case data from an external source."""
    case_id: str = Field(..., min_length=1, max_length=200)


# -----------------------------------------------------------------------
# Table setup
# -----------------------------------------------------------------------

async def ensure_investigations_table(db_path: str) -> None:
    """Create the investigations table if it doesn't exist.

    Also migrates from the old 'incidents' table name if present,
    and renames incident_id → investigation_id if needed.
    """
    resolved = str(Path(db_path).expanduser())
    async with aiosqlite.connect(resolved) as db:
        # Check if old 'incidents' table exists and rename it
        cursor = await db.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='incidents'"
        )
        if await cursor.fetchone():
            await db.execute("ALTER TABLE incidents RENAME TO investigations")
            await db.commit()

        # Check if the column is still named incident_id and rename it
        cursor = await db.execute("PRAGMA table_info(investigations)")
        columns = await cursor.fetchall()
        col_names = [col[1] for col in columns]
        if "incident_id" in col_names and "investigation_id" not in col_names:
            await db.execute(
                "ALTER TABLE investigations RENAME COLUMN incident_id TO investigation_id"
            )
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


def build_investigation_context(investigation: dict) -> str:
    """Build a system prompt prefix from an investigation's context editor state.

    Returns an empty string if no context is configured.
    """
    meta = investigation.get("metadata") or {}
    if isinstance(meta, str):
        try:
            meta = json.loads(meta)
        except (json.JSONDecodeError, TypeError):
            return ""

    ctx = meta.get("context")
    if not ctx or not isinstance(ctx, dict):
        return ""

    fields = ctx.get("fields", {})
    labels = {
        "title": "Title",
        "severity": "Severity",
        "systems": "Affected Systems",
        "description": "Description",
        "case_data": "Case Data",
    }

    parts = []
    for key, label in labels.items():
        field = fields.get(key, {})
        if field.get("enabled") and field.get("value", "").strip():
            parts.append(f"{label}: {field['value'].strip()}")

    # Custom context pills
    for custom in ctx.get("custom", []):
        if isinstance(custom, dict) and custom.get("enabled", True):
            val = custom.get("value", "").strip()
            label = custom.get("label", "Note").strip()
            if val:
                parts.append(f"{label}: {val}")

    notes = ctx.get("notes", "").strip()
    if notes:
        parts.append(f"Notes: {notes}")

    if not parts:
        return ""

    return "## Investigation Context\n\n" + "\n".join(parts) + "\n\n"


async def get_investigation_context_for_session(
    db_path: str, session_id: str
) -> str:
    """Look up the investigation linked to a session and return its context prompt."""
    resolved = str(Path(db_path).expanduser())
    try:
        async with aiosqlite.connect(resolved) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                "SELECT * FROM investigations WHERE session_id = ? LIMIT 1",
                (session_id,),
            )
            row = cursor and await cursor.fetchone()
            if not row:
                return ""
            inv = _parse_json_fields(dict(row))
            return build_investigation_context(inv)
    except Exception:
        logger.debug("Could not fetch investigation context for session %s", session_id, exc_info=True)
        return ""


async def build_document_model_context(
    doc_store: Any,
    investigation_id: str,
    document_id: str,
) -> str:
    """
    Build a compact document-model context section for the LLM system prompt.

    Injected when a session has investigation_id + document_id in metadata,
    so the agent knows the current state: revision, pending review assertions,
    approval summary, and latest narrative IDs.
    """
    try:
        state = await doc_store.get_state(investigation_id, document_id)
        doc = await doc_store.get_document(investigation_id, document_id)
    except Exception:
        logger.debug(
            "Could not load document context for %s/%s", investigation_id, document_id,
            exc_info=True,
        )
        return ""

    if state is None or doc is None:
        return ""

    revision: int = doc.get("current_revision", 0)
    blocks: dict = state.get("blocks", {})
    assertion_states: dict = state.get("assertion_states", {})

    approved_count = sum(1 for ws in assertion_states.values() if ws == "approved")
    rejected_count = sum(1 for ws in assertion_states.values() if ws == "rejected")
    submitted_ids = [
        aid for aid, ws in assertion_states.items() if ws == "submitted"
    ]

    # Latest narratives per audience
    narratives: dict[str, dict] = {}
    for b in blocks.values():
        if b.get("type") == "narrative":
            aud = b.get("audience", "internal")
            existing = narratives.get(aud)
            if existing is None or b.get("source_revision", 0) > existing.get("source_revision", 0):
                narratives[aud] = b

    lines: list[str] = [
        f"## Agent Investigation Context",
        f"investigation_id={investigation_id}  document_id={document_id}  revision={revision}",
        f"Assertions: {approved_count} approved, {rejected_count} rejected, "
        f"{len(submitted_ids)} pending review",
    ]

    if submitted_ids:
        lines.append(f"\n### Pending Human Review ({len(submitted_ids)} assertions)")
        for aid in submitted_ids[:5]:  # cap to keep context short
            claim = blocks.get(aid, {}).get("claim", "")[:120]
            lines.append(f"- {aid}: {claim}")
        if len(submitted_ids) > 5:
            lines.append(f"  … and {len(submitted_ids) - 5} more")

    if narratives:
        lines.append("\n### Latest Narratives")
        for aud, narr in narratives.items():
            lines.append(
                f"- {aud}: id={narr.get('id', '')} "
                f"source_revision={narr.get('source_revision', 0)}"
            )

    if approved_count > 0 and not narratives:
        lines.append(
            f"\nNote: {approved_count} assertion(s) approved — "
            "you can call regenerate_narrative to produce a narrative."
        )

    return "\n".join(lines) + "\n\n"


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


# -----------------------------------------------------------------------
# Integration config & case fetch (must be before /{investigation_id} routes)
# -----------------------------------------------------------------------

_INTEGRATIONS_USER_PATH = Path.home() / ".workbench" / "integrations.json"
_INTEGRATIONS_EXAMPLE_PATH = Path(__file__).parent.parent / "integrations.json.example"
_TMP_DIR = Path.home() / ".workbench" / "tmp"


def _load_integrations_config() -> dict:
    """Load integrations config from user path, falling back to example."""
    for path in (_INTEGRATIONS_USER_PATH, _INTEGRATIONS_EXAMPLE_PATH):
        if path.exists():
            try:
                return json.loads(path.read_text())
            except (json.JSONDecodeError, OSError) as e:
                logger.warning("Failed to load integrations from %s: %s", path, e)
    return {"version": 1, "integrations": {"case_sources": []}}


@router.get("/integrations")
async def list_integrations():
    """Return configured integration sources (for UI dropdown)."""
    config = _load_integrations_config()
    sources = config.get("integrations", {}).get("case_sources", [])
    return JSONResponse({
        "sources": [
            {
                "name": s.get("name", ""),
                "type": s.get("type", ""),
                "enabled": s.get("enabled", False),
                "description": s.get("description", ""),
            }
            for s in sources
        ]
    })


@router.post("/fetch-case")
async def fetch_case_data(req: FetchCaseRequest, request: Request):
    """Fetch case data from configured external sources.

    Reads ~/.workbench/integrations.json for enabled sources.
    For 'agent' type sources, dispatches an orchestrator with the prompt template.
    For 'api' type sources, returns a stub (actual HTTP calls to be wired later).
    Saves fetched context to ~/.workbench/tmp/{case_id}.json.
    """
    config = _load_integrations_config()
    sources = config.get("integrations", {}).get("case_sources", [])
    enabled = [s for s in sources if s.get("enabled")]

    case_id = req.case_id.strip()

    # Ensure tmp directory exists
    _TMP_DIR.mkdir(parents=True, exist_ok=True)

    # Try each enabled source in order
    for source in enabled:
        source_type = source.get("type", "")
        source_name = source.get("name", "unknown")

        if source_type == "agent":
            result = await _fetch_case_via_agent(case_id, source, request)
            if result:
                _save_case_to_tmp(case_id, result, source_name)
                return JSONResponse(result)

        elif source_type == "api":
            # Stub — actual HTTP integration wired per-deployment
            logger.info(
                "API source '%s' enabled but not yet wired — skipping", source_name
            )
            continue

    # No enabled sources or all failed — return unfetched marker
    return JSONResponse({
        "case_id": case_id,
        "unfetched": True,
        "message": (
            "No integration sources are configured. "
            "Copy integrations.json.example to ~/.workbench/integrations.json "
            "and enable a source."
        ),
    })


# -----------------------------------------------------------------------
# Parameterized investigation routes
# -----------------------------------------------------------------------

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


async def _fetch_case_via_agent(
    case_id: str, source: dict, request: Request
) -> dict | None:
    """Dispatch an agent to fetch case data using the configured prompt template."""
    prompt_template = source.get("prompt_template", "")
    if not prompt_template:
        return None

    orchestrator_factory = getattr(request.app.state, "orchestrator_factory", None)
    if orchestrator_factory is None:
        logger.warning("No orchestrator factory available for agent-driven case fetch")
        return None

    session_store = getattr(request.app.state, "session_store", None)
    if session_store is None:
        return None

    prompt = prompt_template.replace("{case_id}", case_id)

    # Create temporary session for the fetch
    metadata = {
        "workspace": "case_fetch",
        "workspace_id": "global",
        "case_id": case_id,
        "status": "active",
    }
    temp_session_id = await session_store.create_session(metadata)

    try:
        orch = await orchestrator_factory.create(
            session_id=temp_session_id,
            confirmation_callback=None,
            allowed_patterns=[],
        )

        chunks: list[str] = []
        async for event in orch.run_streaming(prompt):
            if hasattr(event, "type") and event.type == "text_delta":
                delta = event.data.get("delta", "") if hasattr(event, "data") else ""
                chunks.append(delta)

        text = "".join(chunks)

        # Parse JSON from agent response
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            import re
            match = re.search(r"```(?:json)?\s*\n(.*?)\n```", text, re.DOTALL)
            if match:
                return json.loads(match.group(1))
            logger.warning("Agent returned non-JSON for case %s", case_id)
            return None

    except Exception as e:
        logger.error("Agent case fetch failed for %s: %s", case_id, e)
        return None


def _save_case_to_tmp(case_id: str, data: dict, source_name: str) -> None:
    """Save fetched case context to ~/.workbench/tmp/{case_id}.json."""
    _TMP_DIR.mkdir(parents=True, exist_ok=True)
    # Sanitize case_id for filename
    safe_id = "".join(c if c.isalnum() or c in "-_." else "_" for c in case_id)
    file_path = _TMP_DIR / f"{safe_id}.json"
    payload = {**data, "_source": source_name, "_fetched_at": datetime.now(timezone.utc).isoformat()}
    file_path.write_text(json.dumps(payload, indent=2))
    logger.info("Saved case context to %s", file_path)
