"""
LLM-callable evidence connector tools (M5).

IngestCommandOutputTool  — run a command, capture stdout as an artifact
IngestRemoteFileTool     — fetch a file from a target host as an artifact

Both write command + output blocks into the document graph via
workbench.documents.ingest.process_bytes_ingest, so the resulting outputs
are evidence-addressable (assertion spans, review, narrative) identically
to M4 file uploads.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from workbench.documents.ingest import (
    MAX_COMMAND_BYTES,
    process_bytes_ingest,
)
from workbench.documents.store import DocumentStore
from workbench.tools.base import Tool, ToolRisk
from workbench.types import ToolResult

logger = logging.getLogger(__name__)

_LOCAL_TARGETS = frozenset({"localhost", "local", "127.0.0.1"})


class IngestCommandOutputTool(Tool):
    """
    Execute a shell command via a backend and ingest its stdout as an artifact.

    The output is stored in the document graph as command + output blocks,
    indexed if text-eligible, and can then be cited in assertions with byte/line
    spans through the same evidence flow as M4 file uploads.
    """

    def __init__(
        self,
        doc_store: DocumentStore,
        artifact_store: Any,
        backend_router: Any,
    ) -> None:
        self._doc_store = doc_store
        self._artifact_store = artifact_store
        self._backend_router = backend_router

    @property
    def name(self) -> str:
        return "ingest_command_output"

    @property
    def description(self) -> str:
        return (
            "Run a shell command on a target host and ingest the output as evidence "
            "into a document. Creates command + output blocks that can then be cited "
            "with byte/line spans in assertions. "
            "Parameters: investigation_id, document_id, command (shell command to run), "
            "target (hostname or 'localhost'), label (optional), "
            "stream ('stdout'|'stderr'|'combined', default 'stdout'), "
            "max_bytes (int, default 10MB). "
            "Returns: command_id, output_id, artifact_ref, byte_length, line_count, indexed."
        )

    @property
    def parameters(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "investigation_id": {
                    "type": "string",
                    "description": "Investigation ID the document belongs to",
                },
                "document_id": {
                    "type": "string",
                    "description": "Document ID to append blocks to",
                },
                "command": {
                    "type": "string",
                    "description": "Shell command to execute and capture",
                },
                "target": {
                    "type": "string",
                    "description": "Backend target: 'localhost' or a named SSH host",
                    "default": "localhost",
                },
                "label": {
                    "type": "string",
                    "description": "Human-readable label for this evidence block",
                    "default": "",
                },
                "stream": {
                    "type": "string",
                    "enum": ["stdout", "stderr", "combined"],
                    "description": "Which output stream to capture",
                    "default": "stdout",
                },
                "max_bytes": {
                    "type": "integer",
                    "description": "Byte cap on captured output (default 10 MB)",
                    "default": MAX_COMMAND_BYTES,
                },
            },
            "required": ["investigation_id", "document_id", "command"],
            "additionalProperties": False,
        }

    @property
    def risk_level(self) -> ToolRisk:
        return ToolRisk.READ_ONLY

    async def execute(self, **kwargs: Any) -> ToolResult:
        investigation_id: str = kwargs.get("investigation_id", "")
        document_id: str = kwargs.get("document_id", "")
        command: str = kwargs.get("command", "")
        target: str = kwargs.get("target", "localhost")
        label: str = kwargs.get("label", "")
        stream: str = kwargs.get("stream", "stdout")
        max_bytes: int = int(kwargs.get("max_bytes", MAX_COMMAND_BYTES))

        if not investigation_id or not document_id or not command:
            return ToolResult(
                success=False,
                content="investigation_id, document_id, and command are required",
            )

        if stream not in ("stdout", "stderr", "combined"):
            return ToolResult(success=False, content=f"Invalid stream: {stream!r}")

        # Verify document exists
        doc = await self._doc_store.get_document(investigation_id, document_id)
        if doc is None:
            return ToolResult(
                success=False,
                content=f"Document not found: {document_id}",
            )

        # Run command via backend
        try:
            result = await self._backend_router.run_shell(command, target)
        except Exception as exc:
            return ToolResult(
                success=False,
                content=f"Backend error running command on '{target}': {exc}",
                error=str(exc),
                error_code="backend_error",
            )

        # Select stream
        if stream == "stderr":
            raw_str = result.get("stderr", "")
        elif stream == "combined":
            raw_str = result.get("stdout", "") + result.get("stderr", "")
        else:
            raw_str = result.get("stdout", "")

        raw = raw_str.encode("utf-8", errors="replace")

        # Detect backend-level truncation then clamp to max_bytes
        backend_truncated = bool(
            (result.get("truncated") or {}).get(
                stream if stream != "combined" else "stdout"
            )
        )
        truncated = backend_truncated or len(raw) > max_bytes
        raw = raw[:max_bytes]

        if not raw:
            return ToolResult(
                success=False,
                content=f"Command '{command}' produced no output on stream '{stream}'",
            )

        # Derive filename for metadata
        cmd_slug = command.split()[0].replace("/", "_")[:40]
        filename = f"{cmd_slug}.{stream}.txt"

        policy_scope = "local_read" if target in _LOCAL_TARGETS else "remote_read"

        try:
            ingest_result = await process_bytes_ingest(
                doc_store=self._doc_store,
                artifact_store=self._artifact_store,
                investigation_id=investigation_id,
                document_id=document_id,
                actor_id="agent:ise",
                actor_type="agent",
                actor_source="tool",
                raw=raw,
                filename=filename,
                content_type="text/plain",
                encoding="utf-8",
                newline_mode="unknown",
                stream=stream,
                label=label,
                tool="command_ingest",
                command_str=command,
                executor="agent",
                run_context={
                    "workspace": target,
                    "identity": "agent:ise",
                    "policy_scope": policy_scope,
                    "label": label,
                },
                truncated=truncated,
            )
        except ValueError as exc:
            return ToolResult(
                success=False,
                content=f"Ingest failed: {exc}",
                error=str(exc),
            )

        summary = (
            f"Ingested {ingest_result['byte_length']:,} bytes "
            f"({ingest_result['line_count']} lines, "
            f"{'indexed' if ingest_result['indexed'] else 'not indexed'}"
            f"{', truncated' if truncated else ''}) "
            f"from `{command}` on {target}. "
            f"output_id={ingest_result['output_id']}, "
            f"artifact_ref={ingest_result['artifact_ref'][:16]}…"
        )

        return ToolResult(
            success=True,
            content=summary,
            data={
                **ingest_result,
                "exit_code": result.get("exit_code"),
                "duration_ms": result.get("duration_ms"),
                "timed_out": result.get("timed_out", False),
                "target": target,
                "command": command,
            },
        )


class IngestRemoteFileTool(Tool):
    """
    Fetch a file from a target host and ingest it as an artifact.

    For localhost targets, reads the file directly from disk (no byte cap).
    For SSH targets, fetches via `cat` (text-safe; binary files may be
    garbled — v1 limitation). The file is indexed if text-eligible.
    """

    def __init__(
        self,
        doc_store: DocumentStore,
        artifact_store: Any,
        backend_router: Any,
    ) -> None:
        self._doc_store = doc_store
        self._artifact_store = artifact_store
        self._backend_router = backend_router

    @property
    def name(self) -> str:
        return "ingest_remote_file"

    @property
    def description(self) -> str:
        return (
            "Fetch a file from a target host and ingest it as evidence into a document. "
            "For localhost, reads directly from disk. For SSH targets, fetches the file "
            "via cat (text files only in v1 — binary files will be rejected). "
            "Creates command + output blocks citeable in assertions. "
            "Parameters: investigation_id, document_id, path (file path on host), "
            "target (required: 'localhost' or SSH host name), "
            "label (optional), content_type (optional MIME override), "
            "max_bytes (int, default 10MB). "
            "Returns: command_id, output_id, artifact_ref, byte_length, line_count, indexed."
        )

    @property
    def parameters(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "investigation_id": {
                    "type": "string",
                    "description": "Investigation ID the document belongs to",
                },
                "document_id": {
                    "type": "string",
                    "description": "Document ID to append blocks to",
                },
                "path": {
                    "type": "string",
                    "description": "Absolute file path on the target host",
                },
                "target": {
                    "type": "string",
                    "description": "'localhost' or a named SSH host",
                },
                "label": {
                    "type": "string",
                    "description": "Human-readable label for this evidence block",
                    "default": "",
                },
                "content_type": {
                    "type": "string",
                    "description": "Optional MIME type override (e.g. 'text/plain')",
                    "default": "",
                },
                "max_bytes": {
                    "type": "integer",
                    "description": "Byte cap (default 10 MB)",
                    "default": MAX_COMMAND_BYTES,
                },
            },
            "required": ["investigation_id", "document_id", "path", "target"],
            "additionalProperties": False,
        }

    @property
    def risk_level(self) -> ToolRisk:
        return ToolRisk.READ_ONLY

    async def execute(self, **kwargs: Any) -> ToolResult:
        investigation_id: str = kwargs.get("investigation_id", "")
        document_id: str = kwargs.get("document_id", "")
        path: str = kwargs.get("path", "")
        target: str = kwargs.get("target", "")
        label: str = kwargs.get("label", "")
        content_type_override: str = kwargs.get("content_type", "")
        max_bytes: int = int(kwargs.get("max_bytes", MAX_COMMAND_BYTES))

        if not investigation_id or not document_id or not path or not target:
            return ToolResult(
                success=False,
                content="investigation_id, document_id, path, and target are required",
            )

        # Verify document exists
        doc = await self._doc_store.get_document(investigation_id, document_id)
        if doc is None:
            return ToolResult(
                success=False,
                content=f"Document not found: {document_id}",
            )

        filename = Path(path).name  # basename for metadata (no traversal risk)
        truncated = False

        if target in _LOCAL_TARGETS:
            # Local: read directly from disk — no byte cap from run_shell
            try:
                resolved = Path(path).expanduser().resolve()
                raw = resolved.read_bytes()
            except FileNotFoundError:
                return ToolResult(
                    success=False,
                    content=f"File not found on localhost: {path}",
                )
            except PermissionError:
                return ToolResult(
                    success=False,
                    content=f"Permission denied reading: {path}",
                )
            except OSError as exc:
                return ToolResult(
                    success=False,
                    content=f"Error reading file: {exc}",
                )

            truncated = len(raw) > max_bytes
            raw = raw[:max_bytes]

        else:
            # SSH: fetch via cat — text-safe only (v1)
            # Attempt a quick binary check: if file -b returns binary/octet, reject
            try:
                type_result = await self._backend_router.run_shell(
                    f"file -b --mime-type {path}", target
                )
                remote_mime = type_result.get("stdout", "").strip().lower()
            except Exception:
                remote_mime = ""

            if remote_mime and not remote_mime.startswith("text/") and remote_mime not in (
                "application/json", "application/xml", "application/x-yaml",
                "application/x-sh", "application/x-python", "inode/x-empty",
            ):
                return ToolResult(
                    success=False,
                    content=(
                        f"Remote file appears to be binary ({remote_mime}). "
                        "Binary file ingest over SSH is not supported in v1. "
                        "Use a local path or convert to text before ingesting."
                    ),
                )

            try:
                result = await self._backend_router.run_shell(f"cat {path}", target)
            except Exception as exc:
                return ToolResult(
                    success=False,
                    content=f"Backend error fetching file from '{target}': {exc}",
                    error=str(exc),
                    error_code="backend_error",
                )

            if result.get("exit_code", 0) != 0:
                stderr = result.get("stderr", "").strip()
                return ToolResult(
                    success=False,
                    content=f"cat {path} on {target} failed (exit {result.get('exit_code')}): {stderr}",
                )

            raw_str = result.get("stdout", "")
            raw = raw_str.encode("utf-8", errors="replace")
            backend_truncated = bool((result.get("truncated") or {}).get("stdout"))
            truncated = backend_truncated or len(raw) > max_bytes
            raw = raw[:max_bytes]

        if not raw:
            return ToolResult(
                success=False,
                content=f"File is empty or produced no output: {path}",
            )

        # Determine content type
        effective_ct = content_type_override or "text/plain"

        policy_scope = "local_read" if target in _LOCAL_TARGETS else "remote_read"

        try:
            ingest_result = await process_bytes_ingest(
                doc_store=self._doc_store,
                artifact_store=self._artifact_store,
                investigation_id=investigation_id,
                document_id=document_id,
                actor_id="agent:ise",
                actor_type="agent",
                actor_source="tool",
                raw=raw,
                filename=filename,
                content_type=effective_ct,
                encoding="utf-8",
                newline_mode="unknown",
                stream="stdout",
                label=label,
                tool="remote_file_ingest",
                command_str=f"cat {path}",
                executor="agent",
                run_context={
                    "workspace": target,
                    "identity": "agent:ise",
                    "policy_scope": policy_scope,
                    "label": label,
                },
                truncated=truncated,
            )
        except ValueError as exc:
            return ToolResult(
                success=False,
                content=f"Ingest failed: {exc}",
                error=str(exc),
            )

        summary = (
            f"Ingested {ingest_result['byte_length']:,} bytes "
            f"({ingest_result['line_count']} lines, "
            f"{'indexed' if ingest_result['indexed'] else 'not indexed'}"
            f"{', truncated' if truncated else ''}) "
            f"from {target}:{path}. "
            f"output_id={ingest_result['output_id']}, "
            f"artifact_ref={ingest_result['artifact_ref'][:16]}…"
        )

        return ToolResult(
            success=True,
            content=summary,
            data={
                **ingest_result,
                "path": path,
                "target": target,
            },
        )
