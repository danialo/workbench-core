"""
Document graph API — nested under /api/investigations/{investigation_id}/documents.

Implements the v1 vertical slice:
  command -> output (artifact + index) -> assertion -> review -> narrative

Actor identity:
  1. X-Actor-Id / X-Actor-Type request headers (explicit)
  2. session metadata (if resolvable)
  3. placeholder (human:unknown / agent:ise / system:ise)
"""

from __future__ import annotations

import hashlib
import json
import logging
import uuid
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from workbench.documents.store import DocumentStore, resolve_actor
from workbench.documents.indexer import (
    index_bytes, excerpt_bytes, resolve_span, get_context_lines, validate_span,
)
from workbench.documents.ingest import (
    process_bytes_ingest,
    is_indexable,
    make_artifact_ref,
    ensure_artifact_index,
    INDEX_SIZE_THRESHOLD,
    MAX_UPLOAD_SIZE,
)
from workbench.documents.templates import build_narrative, generation_inputs_hash
from workbench.types import ArtifactRef

# Backward-compat aliases (M4 tests import these private names from here)
_is_indexable = is_indexable

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/api/investigations/{investigation_id}/documents",
    tags=["documents"],
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _get_doc_store(request: Request) -> DocumentStore:
    store = getattr(request.app.state, "document_store", None)
    if store is None:
        raise HTTPException(503, "Document store not available")
    return store


def _get_artifact_store(request: Request):
    store = getattr(request.app.state, "artifact_store", None)
    if store is None:
        raise HTTPException(503, "Artifact store not available")
    return store


def _get_backend_router(request: Request):
    router = getattr(request.app.state, "backend_router", None)
    if router is None:
        raise HTTPException(503, "Backend router not available")
    return router


# Aliases for call sites that still use the private names
_make_artifact_ref = make_artifact_ref
_ensure_artifact_index = ensure_artifact_index


def _extract_actor(request: Request) -> tuple[str, str, str]:
    actor_id = request.headers.get("X-Actor-Id")
    actor_type = request.headers.get("X-Actor-Type")
    return resolve_actor(actor_id=actor_id, actor_type=actor_type)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _block_id() -> str:
    return str(uuid.uuid4())


# ---------------------------------------------------------------------------
# File ingest backward-compat wrapper
# ---------------------------------------------------------------------------

async def _process_file_ingest(
    doc_store: DocumentStore,
    artifact_store: Any,
    investigation_id: str,
    document_id: str,
    actor_id: str,
    actor_type: str,
    actor_source: str,
    raw: bytes,
    filename: str,
    content_type: str,
    encoding: str,
    newline_mode: str,
    stream: str,
    label: str,
) -> dict[str, Any]:
    """Thin wrapper kept for backward compat with M4 tests. Delegates to ingest.py."""
    return await process_bytes_ingest(
        doc_store=doc_store,
        artifact_store=artifact_store,
        investigation_id=investigation_id,
        document_id=document_id,
        actor_id=actor_id,
        actor_type=actor_type,
        actor_source=actor_source,
        raw=raw,
        filename=filename,
        content_type=content_type,
        encoding=encoding,
        newline_mode=newline_mode,
        stream=stream,
        label=label,
        tool="file_ingest",
    )


# ---------------------------------------------------------------------------
# Pydantic request models
# ---------------------------------------------------------------------------

class CreateDocumentRequest(BaseModel):
    pass  # investigation_id comes from path


class CreateCommandRequest(BaseModel):
    tool: str = Field(..., min_length=1)
    executor: str = Field(default="human", pattern="^(human|agent)$")
    run_context: dict[str, Any] = Field(default_factory=dict)
    input_command: str = Field(..., min_length=1, alias="input_command")
    input_args: list[str] = Field(default_factory=list)
    started_at: str = Field(default="")
    finished_at: str = Field(default="")
    exit_code: int | None = None
    labels: list[str] = Field(default_factory=list)
    error_summary: str = Field(default="")

    model_config = {"populate_by_name": True}


class CreateOutputRequest(BaseModel):
    source_command_id: str = Field(..., min_length=1)
    stream: str = Field(default="stdout", pattern="^(stdout|stderr|combined)$")
    content: str = Field(..., description="Raw output text to store as artifact")
    content_type: str = Field(default="text/plain")
    content_encoding: str = Field(default="utf-8")
    newline_mode: str = Field(default="lf", pattern="^(lf|crlf|mixed|unknown)$")
    truncated: bool = Field(default=False)
    provenance: dict[str, Any] = Field(default_factory=dict)


class EvidenceItem(BaseModel):
    output_id: str = Field(..., min_length=1)
    artifact_ref: str = Field(..., min_length=1)
    line_start: int = Field(..., ge=0)
    line_end: int = Field(..., ge=0)
    byte_start: int = Field(..., ge=0)
    byte_end: int = Field(..., ge=0)
    excerpt_hash: str = Field(default="")
    note: str = Field(default="")


class CreateAssertionRequest(BaseModel):
    claim: str = Field(..., min_length=1)
    # Approval (approved/rejected) is derived from reviews, not set on the block
    workflow_state: str = Field(default="draft", pattern="^(draft|submitted)$")
    evidence: list[EvidenceItem] = Field(default_factory=list)


class PatchAssertionRequest(BaseModel):
    claim: str | None = None
    # Approval cannot be set via PATCH — use POST /reviews instead
    workflow_state: str | None = Field(default=None, pattern="^(draft|submitted)$")
    evidence: list[EvidenceItem] | None = None
    expected_revision: int = Field(..., description="Required for optimistic locking")


class CreateReviewRequest(BaseModel):
    target_assertion_ids: list[str] = Field(..., min_length=1)
    decision: str = Field(..., pattern="^(approved|rejected)$")
    reason: str = Field(..., min_length=1, description="Required free text reason")
    reason_code: str = Field(default="")


class RegenerateNarrativeRequest(BaseModel):
    audience: str = Field(default="internal", pattern="^(internal|customer)$")
    template_id: str = Field(default="")
    render_format: str = Field(default="markdown", pattern="^(markdown|plain|html)$")
    expected_revision: int = Field(..., description="Required for optimistic locking")


# ---------------------------------------------------------------------------
# Document CRUD
# ---------------------------------------------------------------------------

@router.post("", status_code=201)
async def create_document(investigation_id: str, request: Request):
    """Create a new document scoped to an investigation."""
    store = _get_doc_store(request)
    document_id = await store.create_document(investigation_id)
    return JSONResponse(
        {"document_id": document_id, "investigation_id": investigation_id},
        status_code=201,
    )


@router.get("")
async def list_documents(investigation_id: str, request: Request):
    """List documents for an investigation."""
    store = _get_doc_store(request)
    docs = await store.list_documents(investigation_id)
    return JSONResponse({"documents": docs, "investigation_id": investigation_id})


@router.get("/{document_id}")
async def get_document(
    investigation_id: str,
    document_id: str,
    request: Request,
    include: str = "",
    at_revision: int | None = None,
):
    """
    Get document.  Pass ?include=graph to include full block state.
    Pass ?at_revision=N for deterministic point-in-time replay.
    """
    store = _get_doc_store(request)

    if at_revision is not None:
        state = await store.get_state(investigation_id, document_id, at_revision=at_revision)
        if state is None:
            raise HTTPException(404, f"Document not found: {document_id}")
        doc = await store.get_document(investigation_id, document_id)
        return JSONResponse({
            "document_id": document_id,
            "investigation_id": investigation_id,
            "replayed_at_revision": at_revision,
            "current_revision": doc["current_revision"] if doc else at_revision,
            "state": state,
        })

    doc = await store.get_document(investigation_id, document_id)
    if doc is None:
        raise HTTPException(404, f"Document not found: {document_id}")

    result = {
        "document_id": doc["document_id"],
        "investigation_id": doc["investigation_id"],
        "current_revision": doc["current_revision"],
        "created_at": doc["created_at"],
        "updated_at": doc["updated_at"],
    }
    if "graph" in include:
        result["state"] = doc["state"]

    return JSONResponse(result)


# ---------------------------------------------------------------------------
# Block: command
# ---------------------------------------------------------------------------

@router.post("/{document_id}/commands", status_code=201)
async def create_command(
    investigation_id: str,
    document_id: str,
    req: CreateCommandRequest,
    request: Request,
):
    """Record a command execution block."""
    store = _get_doc_store(request)
    actor_id, actor_type, actor_source = _extract_actor(request)
    now = _now()

    block = {
        "id": _block_id(),
        "type": "command",
        "tool": req.tool,
        "executor": req.executor,
        "run_context": req.run_context,
        "input": {
            "command": req.input_command,
            "args": req.input_args,
        },
        "started_at": req.started_at or now,
        "finished_at": req.finished_at or now,
        "exit_code": req.exit_code,
        "labels": req.labels,
        "error_summary": req.error_summary,
        "created_at": now,
        "created_by": actor_id,
    }

    result = await store.append_event(
        investigation_id, document_id,
        actor_id, actor_type, actor_source,
        "doc.command.created",
        {"block": block},
    )

    if not result["ok"]:
        raise HTTPException(500, "Failed to append event")

    return JSONResponse(
        {"block_id": block["id"], "revision": result["revision"]},
        status_code=201,
    )


# ---------------------------------------------------------------------------
# Block: output
# ---------------------------------------------------------------------------

@router.post("/{document_id}/commands/{command_id}/outputs", status_code=201)
async def create_output(
    investigation_id: str,
    document_id: str,
    command_id: str,
    req: CreateOutputRequest,
    request: Request,
):
    """
    Store command output as an immutable artifact and record an output block.

    The raw content is stored in ArtifactStore (SHA-256 addressed).
    An artifact index (line/byte maps) is built and stored in the document DB.
    """
    doc_store = _get_doc_store(request)
    artifact_store = _get_artifact_store(request)
    actor_id, actor_type, actor_source = _extract_actor(request)
    now = _now()

    # Store artifact
    from workbench.types import ArtifactPayload  # noqa: PLC0415 (local import ok here)
    raw = req.content.encode(req.content_encoding, errors="replace")
    payload_obj = ArtifactPayload(
        content=raw,
        original_name=f"{command_id}_{req.stream}.txt",
        media_type=req.content_type,
        description=f"Output ({req.stream}) for command {command_id}",
    )
    artifact_ref_obj = artifact_store.store(payload_obj)
    artifact_ref = artifact_ref_obj.sha256

    # Build and store index
    line_map, reverse_map = index_bytes(
        raw,
        content_encoding=req.content_encoding,
        newline_mode=req.newline_mode,
    )
    index_ref = await doc_store.store_artifact_index(artifact_ref, line_map, reverse_map)

    block = {
        "id": _block_id(),
        "type": "output",
        "source_command_id": command_id,
        "stream": req.stream,
        "artifact_ref": artifact_ref,
        "checksum": artifact_ref,   # SHA-256 is the checksum
        "byte_length": len(raw),
        "line_count": len(line_map),
        "index_ref": index_ref,
        "index_version": 1,
        "truncated": req.truncated,
        "content_type": req.content_type,
        "content_encoding": req.content_encoding,
        "newline_mode": req.newline_mode,
        "provenance": req.provenance,
        "indexed_at": now,
        "created_at": now,
        "created_by": actor_id,
    }

    result = await doc_store.append_event(
        investigation_id, document_id,
        actor_id, actor_type, actor_source,
        "doc.output.created",
        {"block": block},
    )

    if not result["ok"]:
        raise HTTPException(500, "Failed to append event")

    return JSONResponse(
        {
            "block_id": block["id"],
            "artifact_ref": artifact_ref,
            "index_ref": index_ref,
            "line_count": len(line_map),
            "byte_length": len(raw),
            "revision": result["revision"],
        },
        status_code=201,
    )


# ---------------------------------------------------------------------------
# Block: assertion
# ---------------------------------------------------------------------------

@router.post("/{document_id}/assertions", status_code=201)
async def create_assertion(
    investigation_id: str,
    document_id: str,
    req: CreateAssertionRequest,
    request: Request,
):
    """
    Create an assertion block with validated, normalized evidence spans.

    Validation (per evidence item):
    - byte_start < byte_end, both within artifact length
    - output_id (if given) resolves to an output block whose artifact_ref matches
    - span clamped and index-authoritative line_start/line_end computed

    Evidence with submitted/approved workflow_state must include at least one item.
    """
    store = _get_doc_store(request)
    artifact_store = _get_artifact_store(request)
    actor_id, actor_type, actor_source = _extract_actor(request)
    now = _now()

    # Require evidence for submitted/approved
    if req.workflow_state in ("submitted", "approved") and not req.evidence:
        raise HTTPException(400, "Evidence required for submitted/approved assertions")

    # Load document state for output block lookup
    state = await store.get_state(investigation_id, document_id)
    if state is None:
        raise HTTPException(404, f"Document not found: {document_id}")
    blocks = state.get("blocks", {})

    evidence: list[dict[str, Any]] = []
    for i, ev in enumerate(req.evidence):
        ev_dict = ev.model_dump()

        # Resolve and validate artifact ref
        art_ref = ev.artifact_ref

        # If output_id provided, verify artifact_ref matches output block
        if ev.output_id:
            output_block = blocks.get(ev.output_id)
            if output_block is None:
                raise HTTPException(400, f"evidence[{i}]: output_id '{ev.output_id}' not found in document")
            if output_block.get("type") != "output":
                raise HTTPException(400, f"evidence[{i}]: output_id '{ev.output_id}' is not an output block")
            expected_ref = output_block.get("artifact_ref", "")
            if art_ref != expected_ref:
                raise HTTPException(
                    400,
                    f"evidence[{i}]: artifact_ref '{art_ref}' does not match "
                    f"output block artifact_ref '{expected_ref}'"
                )
            # Inherit encoding metadata from output block for index build
            content_encoding = output_block.get("content_encoding", "utf-8")
            newline_mode = output_block.get("newline_mode", "lf")
            truncated = output_block.get("truncated", False)
        else:
            content_encoding = "utf-8"
            newline_mode = "lf"
            truncated = False

        # Load artifact bytes for span validation
        try:
            raw_art = artifact_store.get(_make_artifact_ref(artifact_store, art_ref))
        except FileNotFoundError:
            raise HTTPException(400, f"evidence[{i}]: artifact '{art_ref}' not found in store")

        total_bytes = len(raw_art)

        # For truncated artifacts, byte_end must not exceed stored length
        span_err = validate_span(ev.byte_start, ev.byte_end, total_bytes)
        if span_err:
            raise HTTPException(400, f"evidence[{i}]: {span_err}")

        # Ensure index exists (build inline if needed)
        idx = await _ensure_artifact_index(
            store, artifact_store, art_ref, content_encoding, newline_mode
        )

        # Compute authoritative line range from index — byte range is source of truth
        if idx:
            lm = {int(k): tuple(v) for k, v in idx["line_map"].items()}
            span_info = resolve_span(ev.byte_start, ev.byte_end, lm, total_bytes)
            if span_info:
                ev_dict["line_start"], ev_dict["line_end"], _ = span_info

        # Compute excerpt_hash
        excerpt = excerpt_bytes(raw_art, ev.byte_start, ev.byte_end)
        ev_dict["excerpt_hash"] = hashlib.sha256(excerpt).hexdigest()

        evidence.append(ev_dict)

    block = {
        "id": _block_id(),
        "type": "assertion",
        "claim": req.claim,
        "workflow_state": req.workflow_state,
        "authored_by": actor_id,
        "authored_at": now,
        "evidence": evidence,
        "created_at": now,
        "created_by": actor_id,
    }

    result = await store.append_event(
        investigation_id, document_id,
        actor_id, actor_type, actor_source,
        "doc.assertion.created",
        {"block": block},
    )

    if not result["ok"]:
        raise HTTPException(500, "Failed to append event")

    return JSONResponse(
        {"block_id": block["id"], "revision": result["revision"]},
        status_code=201,
    )


@router.patch("/{document_id}/assertions/{assertion_id}")
async def patch_assertion(
    investigation_id: str,
    document_id: str,
    assertion_id: str,
    req: PatchAssertionRequest,
    request: Request,
):
    """Mutate an assertion (claim, evidence, workflow_state). Requires expected_revision."""
    store = _get_doc_store(request)
    actor_id, actor_type, actor_source = _extract_actor(request)

    # Approval state is derived from reviews only — belt + suspenders guard
    if req.workflow_state in ("approved", "rejected"):
        raise HTTPException(
            400,
            "Approval state must be set via POST /reviews, not assertion PATCH"
        )

    # Build patch dict — only fields explicitly provided
    patch: dict[str, Any] = {}
    if req.claim is not None:
        patch["claim"] = req.claim
    if req.workflow_state is not None:
        patch["workflow_state"] = req.workflow_state
    if req.evidence is not None:
        patch["evidence"] = [e.model_dump() for e in req.evidence]

    if not patch:
        raise HTTPException(400, "No fields to patch")

    result = await store.append_event(
        investigation_id, document_id,
        actor_id, actor_type, actor_source,
        "doc.assertion.patched",
        {"assertion_id": assertion_id, "patch": patch},
        expected_revision=req.expected_revision,
    )

    if not result.get("ok"):
        if result.get("conflict"):
            return JSONResponse(
                {
                    "error": "revision_conflict",
                    "current_revision": result["current_revision"],
                    "changed_block_ids": result["changed_block_ids"],
                },
                status_code=409,
            )
        raise HTTPException(500, "Failed to patch assertion")

    return JSONResponse({"revision": result["revision"]})


# ---------------------------------------------------------------------------
# Block: review
# ---------------------------------------------------------------------------

@router.post("/{document_id}/reviews", status_code=201)
async def create_review(
    investigation_id: str,
    document_id: str,
    req: CreateReviewRequest,
    request: Request,
):
    """Record a review decision against one or more assertions."""
    store = _get_doc_store(request)
    actor_id, actor_type, actor_source = _extract_actor(request)
    now = _now()

    # Validate all target IDs exist and are assertion blocks
    state = await store.get_state(investigation_id, document_id)
    if state is None:
        raise HTTPException(404, f"Document not found: {document_id}")

    blocks = state.get("blocks", {})
    for aid in req.target_assertion_ids:
        b = blocks.get(aid)
        if b is None:
            raise HTTPException(400, f"Assertion not found: {aid}")
        if b.get("type") != "assertion":
            raise HTTPException(400, f"Block '{aid}' is not an assertion")

    block = {
        "id": _block_id(),
        "type": "review",
        "target_assertion_ids": req.target_assertion_ids,
        "decision": req.decision,
        "reason": req.reason,
        "reason_code": req.reason_code,
        "reviewed_by": actor_id,
        "reviewed_at": now,
        "created_at": now,
        "created_by": actor_id,
    }

    result = await store.append_event(
        investigation_id, document_id,
        actor_id, actor_type, actor_source,
        "doc.review.created",
        {"block": block},
    )

    if not result["ok"]:
        raise HTTPException(500, "Failed to append event")

    # Effective approval state for targeted assertions (UI convenience)
    effective_approval_state = {aid: req.decision for aid in req.target_assertion_ids}

    return JSONResponse(
        {
            "block_id": block["id"],
            "revision": result["revision"],
            "effective_approval_state": effective_approval_state,
        },
        status_code=201,
    )


# ---------------------------------------------------------------------------
# Block: narrative
# ---------------------------------------------------------------------------

@router.post("/{document_id}/narratives:regenerate")
async def regenerate_narrative(
    investigation_id: str,
    document_id: str,
    req: RegenerateNarrativeRequest,
    request: Request,
):
    """
    Generate a narrative from approved assertions only.

    Blocked if zero approved assertions exist at expected_revision.
    Uses the orchestrator_factory for LLM generation.
    """
    store = _get_doc_store(request)
    actor_id, actor_type, actor_source = _extract_actor(request)

    # Load state at expected_revision — approved assertions are snapshotted here
    state = await store.get_state(investigation_id, document_id)
    if state is None:
        raise HTTPException(404, f"Document not found: {document_id}")

    doc = await store.get_document(investigation_id, document_id)
    if doc is None:
        raise HTTPException(404, f"Document not found: {document_id}")

    source_revision = doc["current_revision"]

    assertion_states = state.get("assertion_states", {})
    approved_ids = [aid for aid, ws in assertion_states.items() if ws == "approved"]

    if not approved_ids:
        raise HTTPException(400, "Cannot regenerate narrative: no approved assertions")

    blocks = state.get("blocks", {})
    approved_assertions = [blocks[aid] for aid in approved_ids if aid in blocks]
    rejected_ids = [aid for aid, ws in assertion_states.items() if ws == "rejected"]
    rejected_assertions = [blocks[aid] for aid in rejected_ids if aid in blocks]

    now = _now()
    template_id = req.template_id or ""

    # Deterministic inputs hash (audience + template + assertions + evidence)
    inputs_hash = generation_inputs_hash(
        audience=req.audience,
        template_id=template_id,
        source_assertion_ids=approved_ids,
        approved_assertions=approved_assertions,
    )

    # Render via deterministic template (no LLM in M3)
    content = build_narrative(
        audience=req.audience,
        template_id=template_id,
        investigation_id=investigation_id,
        document_id=document_id,
        source_revision=source_revision,
        generated_at=now,
        approved_assertions=approved_assertions,
        rejected_assertions=rejected_assertions,
    )

    block = {
        "id": _block_id(),
        "type": "narrative",
        "source_assertion_ids": approved_ids,
        "source_revision": source_revision,
        "audience": req.audience,
        "template_id": template_id,
        "render_format": req.render_format,
        "content": content,
        "generated_at": now,
        "generation_inputs_hash": inputs_hash,
        "created_at": now,
        "created_by": actor_id,
    }

    result = await store.append_event(
        investigation_id, document_id,
        actor_id, actor_type, actor_source,
        "doc.narrative.regenerated",
        {"block": block},
        expected_revision=req.expected_revision,
    )

    if not result.get("ok"):
        if result.get("conflict"):
            return JSONResponse(
                {
                    "error": "revision_conflict",
                    "current_revision": result["current_revision"],
                    "changed_block_ids": result["changed_block_ids"],
                },
                status_code=409,
            )
        raise HTTPException(500, "Failed to store narrative")

    return JSONResponse({
        "block_id": block["id"],
        "revision": result["revision"],
        "source_assertion_ids": approved_ids,
        "source_revision": source_revision,
        "generation_inputs_hash": inputs_hash,
    })


# ---------------------------------------------------------------------------
# Connector: local file ingest
# ---------------------------------------------------------------------------

@router.post("/{document_id}/ingest/file", status_code=201)
async def ingest_file(
    investigation_id: str,
    document_id: str,
    request: Request,
    file: UploadFile = File(...),
    label: str = Form(default=""),
    content_type_override: str = Form(default=""),
    encoding: str = Form(default="utf-8"),
    newline_mode: str = Form(default="unknown"),
    stream: str = Form(default="stdout"),
):
    """
    Ingest a local file upload as command + output blocks.

    Stores the file as an immutable artifact (SHA-256 addressed) and creates:
      - command block (tool="file_ingest")
      - output block (artifact_ref + index if text-ish and under threshold)

    The resulting output can be cited with evidence spans, reviewed, and
    included in narratives using the same M2/M3 flows.
    """
    doc_store = _get_doc_store(request)
    artifact_store = _get_artifact_store(request)
    actor_id, actor_type, actor_source = _extract_actor(request)

    raw = await file.read()
    if not raw:
        raise HTTPException(400, "Empty file upload")
    if len(raw) > MAX_UPLOAD_SIZE:
        raise HTTPException(
            413,
            f"File too large: {len(raw):,} bytes (max {MAX_UPLOAD_SIZE:,})"
        )

    filename = file.filename or "upload"
    effective_ct = content_type_override or file.content_type or "application/octet-stream"

    doc = await doc_store.get_document(investigation_id, document_id)
    if doc is None:
        raise HTTPException(404, f"Document not found: {document_id}")

    try:
        result = await _process_file_ingest(
            doc_store=doc_store,
            artifact_store=artifact_store,
            investigation_id=investigation_id,
            document_id=document_id,
            actor_id=actor_id,
            actor_type=actor_type,
            actor_source=actor_source,
            raw=raw,
            filename=filename,
            content_type=effective_ct,
            encoding=encoding,
            newline_mode=newline_mode,
            stream=stream,
            label=label,
        )
    except ValueError as exc:
        raise HTTPException(500, str(exc))

    return JSONResponse(result, status_code=201)


# ---------------------------------------------------------------------------
# Connector: command ingest (HTTP surface for IngestCommandOutputTool)
# ---------------------------------------------------------------------------

class IngestCommandRequest(BaseModel):
    command: str = Field(..., min_length=1, description="Shell command to execute and capture")
    target: str = Field(default="localhost", description="Backend target (localhost or named SSH host)")
    label: str = Field(default="")
    stream: str = Field(default="stdout", pattern="^(stdout|stderr|combined)$")
    max_bytes: int = Field(default=10 * 1024 * 1024, ge=1, le=100 * 1024 * 1024)


@router.post("/{document_id}/ingest/command", status_code=201)
async def ingest_command(
    investigation_id: str,
    document_id: str,
    req: IngestCommandRequest,
    request: Request,
):
    """
    Execute a shell command via a backend and ingest stdout as an artifact.

    Creates command + output blocks identical in shape to the file ingest
    endpoint, so the same assertion/review/narrative flows apply.
    The command runs through the configured backend router (local or SSH).
    """
    doc_store = _get_doc_store(request)
    artifact_store = _get_artifact_store(request)
    backend_router = _get_backend_router(request)
    actor_id, actor_type, actor_source = _extract_actor(request)

    doc = await doc_store.get_document(investigation_id, document_id)
    if doc is None:
        raise HTTPException(404, f"Document not found: {document_id}")

    try:
        result = await backend_router.run_shell(req.command, req.target)
    except Exception as exc:
        raise HTTPException(502, f"Backend error: {exc}")

    # Select the requested stream
    if req.stream == "stderr":
        raw_str = result.get("stderr", "")
    elif req.stream == "combined":
        raw_str = result.get("stdout", "") + result.get("stderr", "")
    else:
        raw_str = result.get("stdout", "")

    raw = raw_str.encode("utf-8", errors="replace")

    # Clamp to max_bytes and record truncation
    backend_truncated = bool((result.get("truncated") or {}).get(req.stream if req.stream != "combined" else "stdout"))
    truncated = backend_truncated or len(raw) > req.max_bytes
    raw = raw[: req.max_bytes]

    if not raw:
        raise HTTPException(400, "Command produced no output")

    # Derive a safe filename for metadata (command first word + stream)
    cmd_slug = req.command.split()[0].replace("/", "_")[:40]
    filename = f"{cmd_slug}.{req.stream}.txt"

    try:
        ingest_result = await process_bytes_ingest(
            doc_store=doc_store,
            artifact_store=artifact_store,
            investigation_id=investigation_id,
            document_id=document_id,
            actor_id=actor_id,
            actor_type=actor_type,
            actor_source=actor_source,
            raw=raw,
            filename=filename,
            content_type="text/plain",
            encoding="utf-8",
            newline_mode="unknown",
            stream=req.stream,
            label=req.label,
            tool="command_ingest",
            command_str=req.command,
            executor=actor_type if actor_type in ("human", "agent") else "human",
            run_context={
                "workspace": req.target,
                "identity": actor_id,
                "policy_scope": "local_read" if req.target in ("localhost", "local", "127.0.0.1") else "remote_read",
                "label": req.label,
            },
            truncated=truncated,
        )
    except ValueError as exc:
        raise HTTPException(500, str(exc))

    return JSONResponse(
        {
            **ingest_result,
            "exit_code": result.get("exit_code"),
            "duration_ms": result.get("duration_ms"),
            "timed_out": result.get("timed_out", False),
        },
        status_code=201,
    )


# ---------------------------------------------------------------------------
# Evidence resolver
# ---------------------------------------------------------------------------

@router.get("/{document_id}/evidence/{assertion_id}")
async def resolve_evidence(
    investigation_id: str,
    document_id: str,
    assertion_id: str,
    request: Request,
    context_before: int = 3,
    context_after: int = 3,
):
    """
    Full UI-ready evidence resolver.

    For each evidence item:
    - Validates byte span (400 if out-of-range)
    - Resolves authoritative line range from index (builds index on read if missing)
    - Returns excerpt, highlight bounds, and before/after context lines

    Response per evidence item:
      artifact_ref, content_encoding, newline_mode,
      line_start, line_end, byte_start, byte_end, byte_length,
      excerpt, excerpt_hash, excerpt_matches_stored,
      highlight: {line_start, line_end},
      context: {before, highlighted, after, context_line_start, context_line_end},
      index_version, note
    """
    store = _get_doc_store(request)
    artifact_store = _get_artifact_store(request)

    state = await store.get_state(investigation_id, document_id)
    if state is None:
        raise HTTPException(404, f"Document not found: {document_id}")

    blocks = state.get("blocks", {})
    assertion = blocks.get(assertion_id)
    if assertion is None or assertion.get("type") != "assertion":
        raise HTTPException(404, f"Assertion not found: {assertion_id}")

    resolved: list[dict[str, Any]] = []
    for ev in assertion.get("evidence", []):
        art_ref = ev.get("artifact_ref", "")
        byte_start = ev.get("byte_start", 0)
        byte_end = ev.get("byte_end", 0)

        # Load artifact — hard error, not a soft skip
        try:
            raw = artifact_store.get(_make_artifact_ref(artifact_store, art_ref))
        except FileNotFoundError:
            raise HTTPException(404, f"Artifact not found: {art_ref}")

        total_bytes = len(raw)

        # Validate span — 400 for bad spans
        span_err = validate_span(byte_start, byte_end, total_bytes)
        if span_err:
            raise HTTPException(400, f"Invalid evidence span: {span_err}")

        # Resolve output block metadata for encoding/newline info
        output_block = blocks.get(ev.get("output_id", ""), {})
        content_encoding = output_block.get("content_encoding", "utf-8")
        newline_mode = output_block.get("newline_mode", "lf")
        index_version = output_block.get("index_version", 1)

        # Ensure index exists (build on read if missing)
        idx = await _ensure_artifact_index(
            store, artifact_store, art_ref, content_encoding, newline_mode
        )

        # Authoritative line range from index
        line_start = ev.get("line_start", 0)
        line_end = ev.get("line_end", 0)
        if idx:
            lm = {int(k): tuple(v) for k, v in idx["line_map"].items()}
            span_info = resolve_span(byte_start, byte_end, lm, total_bytes)
            if span_info:
                line_start, line_end, _ = span_info
            if idx.get("index_version"):
                index_version = idx["index_version"]
        else:
            lm = {}

        # Excerpt
        exc = excerpt_bytes(raw, byte_start, byte_end)
        exc_hash = hashlib.sha256(exc).hexdigest()

        # Context lines
        ctx = get_context_lines(
            lm, raw, line_start, line_end,
            encoding=content_encoding,
            before=max(0, context_before),
            after=max(0, context_after),
        )

        resolved.append({
            "output_id": ev.get("output_id", ""),
            "artifact_ref": art_ref,
            "content_encoding": content_encoding,
            "newline_mode": newline_mode,
            "line_start": line_start,
            "line_end": line_end,
            "byte_start": byte_start,
            "byte_end": byte_end,
            "byte_length": len(exc),
            "excerpt": exc.decode(content_encoding, errors="replace"),
            "excerpt_hash": exc_hash,
            "excerpt_matches_stored": exc_hash == ev.get("excerpt_hash", exc_hash),
            "highlight": {"line_start": line_start, "line_end": line_end},
            "context": ctx,
            "index_version": index_version,
            "note": ev.get("note", ""),
        })

    return JSONResponse({
        "assertion_id": assertion_id,
        "claim": assertion.get("claim", ""),
        "workflow_state": assertion.get("workflow_state", ""),
        "evidence": resolved,
    })
