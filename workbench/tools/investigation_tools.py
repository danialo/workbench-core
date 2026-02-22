"""
LLM-callable investigation management tools (M6).

CreateAssertionTool    — write an assertion with validated evidence spans
SubmitForReviewTool    — move assertions from draft -> submitted (idempotent)
RegenerateNarrativeTool — trigger narrative regen after human approvals

These tools drive the full investigation loop:
  ingest (M5) -> assert -> submit -> human approve -> regen narrative

Human approval remains the only gate. No tool can self-approve.
"""

from __future__ import annotations

import hashlib
import logging
import uuid
from datetime import datetime, timezone
from typing import Any

from workbench.documents.ingest import ensure_artifact_index, make_artifact_ref
from workbench.documents.indexer import excerpt_bytes, resolve_span, validate_span
from workbench.documents.store import DocumentStore
from workbench.documents.templates import build_narrative, generation_inputs_hash
from workbench.tools.base import Tool, ToolRisk
from workbench.types import ToolResult

logger = logging.getLogger(__name__)

_ACTOR_ID = "agent:ise"
_ACTOR_TYPE = "agent"
_ACTOR_SOURCE = "tool"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _block_id() -> str:
    return str(uuid.uuid4())


# ---------------------------------------------------------------------------
# CreateAssertionTool
# ---------------------------------------------------------------------------

class CreateAssertionTool(Tool):
    """
    Create an assertion block with a claim and validated evidence spans.

    For each evidence span:
    - byte_start/byte_end are validated against the artifact's actual byte length
    - line_start/line_end are derived from the index (authoritative)
    - excerpt_hash is computed for tamper detection

    If ANY span is invalid, the tool call fails with no partial write.
    Spans must reference artifacts that exist in the document's output blocks.
    """

    def __init__(self, doc_store: DocumentStore, artifact_store: Any) -> None:
        self._doc_store = doc_store
        self._artifact_store = artifact_store

    @property
    def name(self) -> str:
        return "create_assertion"

    @property
    def description(self) -> str:
        return (
            "Create an assertion in a document with a claim and evidence spans. "
            "Each evidence span must reference an artifact_ref from a prior ingest "
            "output block with valid byte_start/byte_end offsets. "
            "Parameters: investigation_id, document_id, claim (string), "
            "evidence (list of {artifact_ref, byte_start, byte_end, output_id (optional)}), "
            "workflow_state ('draft'|'submitted', default 'draft'). "
            "Returns: assertion_id and normalized evidence with derived line spans. "
            "Fails if any span is invalid — no partial writes."
        )

    @property
    def parameters(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "investigation_id": {"type": "string"},
                "document_id": {"type": "string"},
                "claim": {
                    "type": "string",
                    "description": "The assertion claim text",
                },
                "evidence": {
                    "type": "array",
                    "description": "List of evidence spans from ingested output blocks",
                    "items": {
                        "type": "object",
                        "properties": {
                            "artifact_ref": {
                                "type": "string",
                                "description": "SHA-256 from the output block's artifact_ref",
                            },
                            "byte_start": {"type": "integer", "minimum": 0},
                            "byte_end": {"type": "integer", "minimum": 0},
                            "output_id": {
                                "type": "string",
                                "description": "output block ID (optional, used to validate artifact_ref)",
                            },
                            "note": {"type": "string", "default": ""},
                        },
                        "required": ["artifact_ref", "byte_start", "byte_end"],
                    },
                },
                "workflow_state": {
                    "type": "string",
                    "enum": ["draft", "submitted"],
                    "default": "draft",
                },
            },
            "required": ["investigation_id", "document_id", "claim"],
            "additionalProperties": False,
        }

    @property
    def risk_level(self) -> ToolRisk:
        return ToolRisk.WRITE

    async def execute(self, **kwargs: Any) -> ToolResult:
        investigation_id: str = kwargs.get("investigation_id", "")
        document_id: str = kwargs.get("document_id", "")
        claim: str = kwargs.get("claim", "").strip()
        evidence_raw: list = kwargs.get("evidence") or []
        workflow_state: str = kwargs.get("workflow_state", "draft")

        if not investigation_id or not document_id or not claim:
            return ToolResult(
                success=False,
                content="investigation_id, document_id, and claim are required",
            )
        if workflow_state not in ("draft", "submitted"):
            return ToolResult(
                success=False,
                content=f"workflow_state must be 'draft' or 'submitted', got {workflow_state!r}",
            )
        if workflow_state == "submitted" and not evidence_raw:
            return ToolResult(
                success=False,
                content="Evidence is required for submitted assertions",
            )

        # Load document state for output block lookup
        state = await self._doc_store.get_state(investigation_id, document_id)
        if state is None:
            return ToolResult(
                success=False,
                content=f"Document not found: {document_id}",
            )
        blocks = state.get("blocks", {})

        # Validate and normalize all evidence spans up front (fail-fast, no partial write)
        normalized_evidence: list[dict[str, Any]] = []
        for i, ev in enumerate(evidence_raw):
            art_ref: str = ev.get("artifact_ref", "")
            byte_start: int = int(ev.get("byte_start", 0))
            byte_end: int = int(ev.get("byte_end", 0))
            output_id: str = ev.get("output_id", "")
            note: str = ev.get("note", "")

            if not art_ref:
                return ToolResult(
                    success=False,
                    content=f"evidence[{i}]: artifact_ref is required",
                )

            # Validate output_id → artifact_ref consistency if output_id given
            content_encoding = "utf-8"
            newline_mode = "lf"
            if output_id:
                output_block = blocks.get(output_id)
                if output_block is None:
                    return ToolResult(
                        success=False,
                        content=f"evidence[{i}]: output_id '{output_id}' not found in document",
                    )
                if output_block.get("type") != "output":
                    return ToolResult(
                        success=False,
                        content=f"evidence[{i}]: '{output_id}' is not an output block",
                    )
                expected_ref = output_block.get("artifact_ref", "")
                if art_ref != expected_ref:
                    return ToolResult(
                        success=False,
                        content=(
                            f"evidence[{i}]: artifact_ref '{art_ref}' does not match "
                            f"output block artifact_ref '{expected_ref}'"
                        ),
                    )
                content_encoding = output_block.get("content_encoding", "utf-8")
                newline_mode = output_block.get("newline_mode", "lf")

            # Load artifact bytes for span validation
            try:
                raw_art = self._artifact_store.get(
                    make_artifact_ref(self._artifact_store, art_ref)
                )
            except FileNotFoundError:
                return ToolResult(
                    success=False,
                    content=f"evidence[{i}]: artifact '{art_ref}' not found in store",
                )

            total_bytes = len(raw_art)
            span_err = validate_span(byte_start, byte_end, total_bytes)
            if span_err:
                return ToolResult(
                    success=False,
                    content=f"evidence[{i}]: {span_err}",
                )

            # Derive authoritative line range from index
            line_start = 0
            line_end = 0
            idx = await ensure_artifact_index(
                self._doc_store, self._artifact_store, art_ref,
                content_encoding, newline_mode,
            )
            if idx:
                lm = {int(k): tuple(v) for k, v in idx["line_map"].items()}
                span_info = resolve_span(byte_start, byte_end, lm, total_bytes)
                if span_info:
                    line_start, line_end, _ = span_info

            excerpt = excerpt_bytes(raw_art, byte_start, byte_end)
            excerpt_hash = hashlib.sha256(excerpt).hexdigest()

            normalized_evidence.append({
                "output_id": output_id,
                "artifact_ref": art_ref,
                "line_start": line_start,
                "line_end": line_end,
                "byte_start": byte_start,
                "byte_end": byte_end,
                "excerpt_hash": excerpt_hash,
                "note": note,
            })

        # All spans valid — append assertion event
        now = _now()
        assertion_id = _block_id()
        block: dict[str, Any] = {
            "id": assertion_id,
            "type": "assertion",
            "claim": claim,
            "workflow_state": workflow_state,
            "authored_by": _ACTOR_ID,
            "authored_at": now,
            "evidence": normalized_evidence,
            "created_at": now,
            "created_by": _ACTOR_ID,
        }

        result = await self._doc_store.append_event(
            investigation_id, document_id,
            _ACTOR_ID, _ACTOR_TYPE, _ACTOR_SOURCE,
            "doc.assertion.created",
            {"block": block},
        )
        if not result["ok"]:
            return ToolResult(
                success=False,
                content="Failed to append assertion event to document store",
            )

        return ToolResult(
            success=True,
            content=(
                f"Created assertion {assertion_id} ({workflow_state}) "
                f"with {len(normalized_evidence)} evidence span(s)."
            ),
            data={
                "assertion_id": assertion_id,
                "workflow_state": workflow_state,
                "evidence": normalized_evidence,
                "revision": result["revision"],
            },
        )


# ---------------------------------------------------------------------------
# SubmitForReviewTool
# ---------------------------------------------------------------------------

class SubmitForReviewTool(Tool):
    """
    Move assertions from draft -> submitted state for human review.

    Idempotent: already-submitted assertions are skipped (no new event).
    Cannot be used to approve or reject — that is the human's gate.
    """

    def __init__(self, doc_store: DocumentStore) -> None:
        self._doc_store = doc_store

    @property
    def name(self) -> str:
        return "submit_for_review"

    @property
    def description(self) -> str:
        return (
            "Submit one or more assertions for human review. "
            "Moves assertions from 'draft' to 'submitted' state. "
            "Idempotent: already-submitted assertions are silently skipped. "
            "Cannot approve or reject — only humans can do that. "
            "Parameters: investigation_id, document_id, "
            "assertion_ids (list of assertion block IDs), "
            "note (optional string explaining what to look for). "
            "Returns: updated states per assertion and pending_review_count."
        )

    @property
    def parameters(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "investigation_id": {"type": "string"},
                "document_id": {"type": "string"},
                "assertion_ids": {
                    "type": "array",
                    "items": {"type": "string"},
                    "minItems": 1,
                    "description": "List of assertion block IDs to submit",
                },
                "note": {
                    "type": "string",
                    "description": "Optional note for the reviewer explaining what to look for",
                    "default": "",
                },
            },
            "required": ["investigation_id", "document_id", "assertion_ids"],
            "additionalProperties": False,
        }

    @property
    def risk_level(self) -> ToolRisk:
        return ToolRisk.WRITE

    async def execute(self, **kwargs: Any) -> ToolResult:
        investigation_id: str = kwargs.get("investigation_id", "")
        document_id: str = kwargs.get("document_id", "")
        assertion_ids: list[str] = kwargs.get("assertion_ids") or []
        note: str = kwargs.get("note", "")

        if not investigation_id or not document_id or not assertion_ids:
            return ToolResult(
                success=False,
                content="investigation_id, document_id, and assertion_ids are required",
            )

        # Load document state
        state = await self._doc_store.get_state(investigation_id, document_id)
        if state is None:
            return ToolResult(
                success=False,
                content=f"Document not found: {document_id}",
            )
        doc = await self._doc_store.get_document(investigation_id, document_id)
        if doc is None:
            return ToolResult(
                success=False,
                content=f"Document not found: {document_id}",
            )

        blocks = state.get("blocks", {})
        assertion_states = state.get("assertion_states", {})
        current_revision: int = doc["current_revision"]

        updated: dict[str, str] = {}
        skipped: list[str] = []
        errors: list[str] = []

        for aid in assertion_ids:
            block = blocks.get(aid)
            if block is None:
                errors.append(f"'{aid}' not found in document")
                continue
            if block.get("type") != "assertion":
                errors.append(f"'{aid}' is not an assertion block")
                continue

            effective_state = assertion_states.get(aid, block.get("workflow_state", "draft"))

            if effective_state in ("approved", "rejected"):
                errors.append(
                    f"'{aid}' is already {effective_state} — "
                    "approved/rejected assertions cannot be resubmitted"
                )
                continue

            if effective_state == "submitted":
                # Idempotent: skip without error
                skipped.append(aid)
                updated[aid] = "submitted"
                continue

            # Patch draft -> submitted
            patch_payload: dict[str, Any] = {
                "assertion_id": aid,
                "patch": {"workflow_state": "submitted"},
            }
            if note:
                patch_payload["review_note"] = note

            result = await self._doc_store.append_event(
                investigation_id, document_id,
                _ACTOR_ID, _ACTOR_TYPE, _ACTOR_SOURCE,
                "doc.assertion.patched",
                patch_payload,
                expected_revision=current_revision,
            )

            if not result.get("ok"):
                if result.get("conflict"):
                    # Reload and retry once
                    doc2 = await self._doc_store.get_document(investigation_id, document_id)
                    current_revision = doc2["current_revision"] if doc2 else current_revision
                    result = await self._doc_store.append_event(
                        investigation_id, document_id,
                        _ACTOR_ID, _ACTOR_TYPE, _ACTOR_SOURCE,
                        "doc.assertion.patched",
                        patch_payload,
                        expected_revision=current_revision,
                    )

                if not result.get("ok"):
                    errors.append(f"Failed to patch '{aid}'")
                    continue

            current_revision = result["revision"]
            updated[aid] = "submitted"

        if errors and not updated and not skipped:
            return ToolResult(
                success=False,
                content="All assertions failed: " + "; ".join(errors),
            )

        # Count pending review from current state (after patches)
        fresh_state = await self._doc_store.get_state(investigation_id, document_id)
        fresh_assertion_states = fresh_state.get("assertion_states", {}) if fresh_state else {}
        pending_review_count = sum(
            1 for ws in fresh_assertion_states.values() if ws == "submitted"
        )

        parts = [f"Submitted {len(updated) - len(skipped)} assertion(s)."]
        if skipped:
            parts.append(f"{len(skipped)} already submitted (skipped).")
        if errors:
            parts.append(f"Errors: {'; '.join(errors)}.")
        parts.append(f"Pending review queue: {pending_review_count}.")

        return ToolResult(
            success=True,
            content=" ".join(parts),
            data={
                "updated_states": updated,
                "skipped": skipped,
                "errors": errors,
                "pending_review_count": pending_review_count,
            },
        )


# ---------------------------------------------------------------------------
# RegenerateNarrativeTool
# ---------------------------------------------------------------------------

class RegenerateNarrativeTool(Tool):
    """
    Trigger narrative regeneration from approved assertions.

    Blocked if no approved assertions exist. Uses deterministic templates.
    The agent cannot call this until a human has approved at least one assertion.
    """

    def __init__(self, doc_store: DocumentStore) -> None:
        self._doc_store = doc_store

    @property
    def name(self) -> str:
        return "regenerate_narrative"

    @property
    def description(self) -> str:
        return (
            "Generate or regenerate the investigation narrative from approved assertions. "
            "Blocked if no approved assertions exist — human approval is required first. "
            "Parameters: investigation_id, document_id, "
            "audience ('internal'|'customer', default 'internal'), "
            "template_id (optional), expected_revision (int, current doc revision). "
            "Returns: narrative_id, source_revision, source_assertion_ids."
        )

    @property
    def parameters(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "investigation_id": {"type": "string"},
                "document_id": {"type": "string"},
                "audience": {
                    "type": "string",
                    "enum": ["internal", "customer"],
                    "default": "internal",
                },
                "template_id": {"type": "string", "default": ""},
                "expected_revision": {
                    "type": "integer",
                    "description": "Current document revision (for optimistic locking)",
                },
            },
            "required": ["investigation_id", "document_id", "expected_revision"],
            "additionalProperties": False,
        }

    @property
    def risk_level(self) -> ToolRisk:
        return ToolRisk.WRITE

    async def execute(self, **kwargs: Any) -> ToolResult:
        investigation_id: str = kwargs.get("investigation_id", "")
        document_id: str = kwargs.get("document_id", "")
        audience: str = kwargs.get("audience", "internal")
        template_id: str = kwargs.get("template_id", "")
        expected_revision: int | None = kwargs.get("expected_revision")

        if not investigation_id or not document_id:
            return ToolResult(
                success=False,
                content="investigation_id and document_id are required",
            )
        if audience not in ("internal", "customer"):
            return ToolResult(
                success=False,
                content=f"audience must be 'internal' or 'customer', got {audience!r}",
            )
        if expected_revision is None:
            return ToolResult(
                success=False,
                content="expected_revision is required for optimistic locking",
            )

        state = await self._doc_store.get_state(investigation_id, document_id)
        if state is None:
            return ToolResult(
                success=False,
                content=f"Document not found: {document_id}",
            )

        doc = await self._doc_store.get_document(investigation_id, document_id)
        if doc is None:
            return ToolResult(
                success=False,
                content=f"Document not found: {document_id}",
            )

        source_revision: int = doc["current_revision"]
        assertion_states = state.get("assertion_states", {})
        approved_ids = [aid for aid, ws in assertion_states.items() if ws == "approved"]

        if not approved_ids:
            return ToolResult(
                success=False,
                content=(
                    "Cannot regenerate narrative: no approved assertions. "
                    "Submit assertions and wait for human approval first."
                ),
            )

        blocks = state.get("blocks", {})
        approved_assertions = [blocks[aid] for aid in approved_ids if aid in blocks]
        rejected_ids = [aid for aid, ws in assertion_states.items() if ws == "rejected"]
        rejected_assertions = [blocks[aid] for aid in rejected_ids if aid in blocks]

        now = _now()

        inputs_hash = generation_inputs_hash(
            audience=audience,
            template_id=template_id,
            source_assertion_ids=approved_ids,
            approved_assertions=approved_assertions,
        )

        content = build_narrative(
            audience=audience,
            template_id=template_id,
            investigation_id=investigation_id,
            document_id=document_id,
            source_revision=source_revision,
            generated_at=now,
            approved_assertions=approved_assertions,
            rejected_assertions=rejected_assertions,
        )

        narrative_id = _block_id()
        block: dict[str, Any] = {
            "id": narrative_id,
            "type": "narrative",
            "source_assertion_ids": approved_ids,
            "source_revision": source_revision,
            "audience": audience,
            "template_id": template_id,
            "render_format": "markdown",
            "content": content,
            "generated_at": now,
            "generation_inputs_hash": inputs_hash,
            "created_at": now,
            "created_by": _ACTOR_ID,
        }

        result = await self._doc_store.append_event(
            investigation_id, document_id,
            _ACTOR_ID, _ACTOR_TYPE, _ACTOR_SOURCE,
            "doc.narrative.regenerated",
            {"block": block},
            expected_revision=expected_revision,
        )

        if not result.get("ok"):
            if result.get("conflict"):
                return ToolResult(
                    success=False,
                    content=(
                        f"Revision conflict: document changed since revision {expected_revision}. "
                        f"Current revision is {result.get('current_revision')}. "
                        "Reload the document and retry with the current revision."
                    ),
                    data={"conflict": True, "current_revision": result.get("current_revision")},
                )
            return ToolResult(
                success=False,
                content="Failed to store narrative",
            )

        return ToolResult(
            success=True,
            content=(
                f"Generated {audience} narrative from {len(approved_ids)} approved assertion(s). "
                f"narrative_id={narrative_id}, source_revision={source_revision}."
            ),
            data={
                "narrative_id": narrative_id,
                "source_revision": source_revision,
                "source_assertion_ids": approved_ids,
                "audience": audience,
                "generation_inputs_hash": inputs_hash,
                "revision": result["revision"],
            },
        )
