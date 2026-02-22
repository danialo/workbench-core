"""
Core evidence ingest pipeline — testable without HTTP context.

Callable from:
  - workbench/web/routes/documents.py   (file upload + command ingest HTTP routes)
  - workbench/tools/evidence_tools.py   (IngestCommandOutputTool, IngestRemoteFileTool)
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from workbench.documents.indexer import index_bytes
from workbench.documents.store import DocumentStore

logger = logging.getLogger(__name__)

# Deferred to avoid circular imports at module level
def _artifact_ref_type():
    from workbench.types import ArtifactRef
    return ArtifactRef

# ---------------------------------------------------------------------------
# Public constants (re-exported from documents.py for backward compat)
# ---------------------------------------------------------------------------

# Extensions treated as text for indexing purposes
INDEXABLE_EXTENSIONS: frozenset[str] = frozenset({
    ".log", ".txt", ".json", ".csv", ".yaml", ".yml",
    ".md", ".xml", ".conf", ".cfg", ".ini", ".toml",
    ".sh", ".py", ".rb", ".js", ".ts",
})

INDEX_SIZE_THRESHOLD = 20 * 1024 * 1024   # 20 MB — index if under this
MAX_UPLOAD_SIZE      = 100 * 1024 * 1024  # 100 MB — hard reject above this
MAX_COMMAND_BYTES    = 10 * 1024 * 1024   # 10 MB — default cap for command output


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def is_indexable(filename: str, content_type: str, size: int) -> bool:
    """Return True if the artifact should be indexed for line/byte navigation."""
    if size == 0 or size > INDEX_SIZE_THRESHOLD:
        return False
    if content_type.startswith("text/"):
        return True
    return Path(filename).suffix.lower() in INDEXABLE_EXTENSIONS


def make_artifact_ref(artifact_store: Any, sha256: str) -> Any:
    """Construct an ArtifactRef from a sha256, using the store's path layout."""
    from workbench.types import ArtifactRef
    return ArtifactRef(sha256=sha256, stored_path=str(artifact_store._artifact_path(sha256)))


async def ensure_artifact_index(
    doc_store: DocumentStore,
    artifact_store: Any,
    artifact_ref: str,
    content_encoding: str = "utf-8",
    newline_mode: str = "lf",
) -> dict | None:
    """Return the index for an artifact, building and storing it inline if absent."""
    idx = await doc_store.get_artifact_index(artifact_ref)
    if idx is not None:
        return idx
    try:
        raw = artifact_store.get(make_artifact_ref(artifact_store, artifact_ref))
    except FileNotFoundError:
        return None
    lm, rm = index_bytes(raw, content_encoding=content_encoding, newline_mode=newline_mode)
    await doc_store.store_artifact_index(artifact_ref, lm, rm)
    return await doc_store.get_artifact_index(artifact_ref)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _block_id() -> str:
    return str(uuid.uuid4())


# ---------------------------------------------------------------------------
# Core pipeline
# ---------------------------------------------------------------------------

async def process_bytes_ingest(
    *,
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
    encoding: str = "utf-8",
    newline_mode: str = "unknown",
    stream: str = "stdout",
    label: str = "",
    tool: str = "file_ingest",
    command_str: str | None = None,
    executor: str | None = None,
    run_context: dict[str, Any] | None = None,
    truncated: bool = False,
) -> dict[str, Any]:
    """
    Store bytes as a content-addressed artifact and create command + output
    blocks in the document graph.

    Parameters
    ----------
    raw          : Bytes to store. Must be non-empty (caller validates).
    filename     : Logical name for metadata and extension-based indexing.
    content_type : MIME type. "text/*" always triggers indexing (size permitting).
    tool         : Name recorded in command block.  Use:
                     "file_ingest"        — user upload (M4 HTTP endpoint)
                     "command_ingest"     — captured command stdout/stderr
                     "remote_file_ingest" — fetched remote file
    command_str  : Human-readable command string in the block.
    executor     : "human" | "agent". Falls back to actor_type or "human".
    run_context  : Stored verbatim in command block.
    truncated    : If True, marks output.truncated = True. Caller must have
                   already clamped raw to max_bytes before calling.

    Returns
    -------
    dict with: command_id, output_id, artifact_ref, index_ref, byte_length,
               line_count, revision, indexed, truncated.
    """
    from workbench.types import ArtifactPayload  # avoid circular at module level

    now = _now()
    safe_name = Path(filename).name  # strip directory traversal

    effective_executor: str = executor or (
        actor_type if actor_type in ("human", "agent") else "human"
    )
    effective_command_str: str = command_str or f"ingest file {safe_name}"
    effective_run_context: dict[str, Any] = run_context or {
        "workspace": "local",
        "identity": actor_id,
        "policy_scope": "local_read",
        "label": label,
    }

    # ---- Store artifact (content-addressed, dedup by SHA-256) ----
    payload_obj = ArtifactPayload(
        content=raw,
        original_name=safe_name,
        media_type=content_type,
        description=(
            f"Ingested: {safe_name}" + (f" [{label}]" if label else "")
        ),
    )
    artifact_ref_obj = artifact_store.store(payload_obj)
    artifact_ref: str = artifact_ref_obj.sha256

    # ---- Command block ----
    command_id = _block_id()
    command_block: dict[str, Any] = {
        "id": command_id,
        "type": "command",
        "tool": tool,
        "executor": effective_executor,
        "run_context": effective_run_context,
        "input": {
            "command": effective_command_str,
            "args": {"original_name": safe_name},
        },
        "started_at": now,
        "finished_at": now,
        "exit_code": 0,
        "labels": [label] if label else [],
        "error_summary": "",
        "created_at": now,
        "created_by": actor_id,
    }

    cmd_result = await doc_store.append_event(
        investigation_id, document_id,
        actor_id, actor_type, actor_source,
        "doc.command.created",
        {"block": command_block},
    )
    if not cmd_result["ok"]:
        raise ValueError("Failed to append command event")

    # ---- Index if text-ish and under threshold ----
    lm: dict[int, tuple[int, int]] = {}
    index_ref: str | None = None
    should_index = is_indexable(safe_name, content_type, len(raw))

    if should_index:
        try:
            lm, rm = index_bytes(
                raw,
                content_encoding=encoding,
                newline_mode=newline_mode,
            )
            index_ref = await doc_store.store_artifact_index(artifact_ref, lm, rm)
        except Exception as exc:
            logger.warning("Indexing failed for %s: %s", safe_name, exc)
            lm = {}
            index_ref = None

    # ---- Output block ----
    output_id = _block_id()
    output_block: dict[str, Any] = {
        "id": output_id,
        "type": "output",
        "source_command_id": command_id,
        "stream": stream,
        "artifact_ref": artifact_ref,
        "checksum": artifact_ref,   # SHA-256 is the checksum
        "byte_length": len(raw),
        "line_count": len(lm),
        "index_ref": index_ref,
        "index_version": 1 if index_ref else None,
        "truncated": truncated,
        "content_type": content_type,
        "content_encoding": encoding,
        "newline_mode": newline_mode if should_index else "none",
        "provenance": {
            "source": tool,
            "original_name": safe_name,
            "label": label,
        },
        "indexed_at": now if index_ref else None,
        "created_at": now,
        "created_by": actor_id,
    }

    out_result = await doc_store.append_event(
        investigation_id, document_id,
        actor_id, actor_type, actor_source,
        "doc.output.created",
        {"block": output_block},
    )
    if not out_result["ok"]:
        raise ValueError("Failed to append output event")

    return {
        "command_id": command_id,
        "output_id": output_id,
        "artifact_ref": artifact_ref,
        "index_ref": index_ref,
        "byte_length": len(raw),
        "line_count": len(lm),
        "revision": out_result["revision"],
        "indexed": bool(index_ref),
        "truncated": truncated,
    }
