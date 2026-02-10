"""
Bridge tools connecting the Execution Backend interface to the Tool Registry.

These are the canonical tools from the build plan:
- resolve_target
- list_diagnostics
- run_diagnostic
- summarize_artifact

Target is always explicit per tool call (never implicit).
"""

from __future__ import annotations

import json
from dataclasses import asdict

from workbench.backends.base import BackendError, ExecutionBackend
from workbench.session.artifacts import ArtifactStore
from workbench.tools.base import Tool, ToolRisk, PrivacyScope
from workbench.types import ToolResult, ErrorCode


class ResolveTargetTool(Tool):
    """Resolve a target identifier to structured information."""

    def __init__(self, backend: ExecutionBackend) -> None:
        self._backend = backend

    @property
    def name(self) -> str:
        return "resolve_target"

    @property
    def description(self) -> str:
        return "Resolve a target identifier (hostname, service name, etc.) to structured information about it."

    @property
    def parameters(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "target": {
                    "type": "string",
                    "description": "The target identifier to resolve.",
                },
            },
            "required": ["target"],
        }

    @property
    def risk_level(self) -> ToolRisk:
        return ToolRisk.READ_ONLY

    async def execute(self, **kwargs) -> ToolResult:
        target = kwargs["target"]
        try:
            info = await self._backend.resolve_target(target)
            return ToolResult(
                success=True,
                content=json.dumps(info, indent=2),
                data=info,
            )
        except BackendError as e:
            return ToolResult(
                success=False,
                content=str(e),
                error=str(e),
                error_code=ErrorCode.BACKEND_ERROR,
            )


class ListDiagnosticsTool(Tool):
    """List available diagnostics for a target."""

    def __init__(self, backend: ExecutionBackend) -> None:
        self._backend = backend

    @property
    def name(self) -> str:
        return "list_diagnostics"

    @property
    def description(self) -> str:
        return "List all available diagnostic actions for a given target."

    @property
    def parameters(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "target": {
                    "type": "string",
                    "description": "The target to list diagnostics for.",
                },
            },
            "required": ["target"],
        }

    @property
    def risk_level(self) -> ToolRisk:
        return ToolRisk.READ_ONLY

    async def execute(self, **kwargs) -> ToolResult:
        target = kwargs["target"]
        try:
            diags = await self._backend.list_diagnostics(target)
            data = [asdict(d) for d in diags]
            lines = [f"- {d.name}: {d.description}" for d in diags]
            return ToolResult(
                success=True,
                content="\n".join(lines) if lines else "No diagnostics available.",
                data=data,
            )
        except BackendError as e:
            return ToolResult(
                success=False,
                content=str(e),
                error=str(e),
                error_code=ErrorCode.BACKEND_ERROR,
            )


class RunDiagnosticTool(Tool):
    """Run a diagnostic action against a target."""

    def __init__(self, backend: ExecutionBackend) -> None:
        self._backend = backend

    @property
    def name(self) -> str:
        return "run_diagnostic"

    @property
    def description(self) -> str:
        return "Run a specific diagnostic action against a target. Target is always required."

    @property
    def parameters(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "description": "The diagnostic action to run (e.g. ping, traceroute).",
                },
                "target": {
                    "type": "string",
                    "description": "The target to run the diagnostic against.",
                },
            },
            "required": ["action", "target"],
            "additionalProperties": True,
        }

    @property
    def risk_level(self) -> ToolRisk:
        return ToolRisk.WRITE

    async def execute(self, **kwargs) -> ToolResult:
        action = kwargs.pop("action")
        target = kwargs.pop("target")
        try:
            result = await self._backend.run_diagnostic(action, target, **kwargs)
            return ToolResult(
                success=True,
                content=json.dumps(result, indent=2),
                data=result,
            )
        except BackendError as e:
            return ToolResult(
                success=False,
                content=str(e),
                error=str(e),
                error_code=ErrorCode.BACKEND_ERROR,
            )


class SummarizeArtifactTool(Tool):
    """Retrieve and summarize a stored artifact."""

    def __init__(self, artifact_store: ArtifactStore) -> None:
        self._store = artifact_store

    @property
    def name(self) -> str:
        return "summarize_artifact"

    @property
    def description(self) -> str:
        return "Retrieve a stored artifact by its SHA-256 hash and return a text summary of its contents."

    @property
    def parameters(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "sha256": {
                    "type": "string",
                    "description": "SHA-256 hash of the artifact to summarize.",
                },
            },
            "required": ["sha256"],
        }

    @property
    def risk_level(self) -> ToolRisk:
        return ToolRisk.READ_ONLY

    async def execute(self, **kwargs) -> ToolResult:
        sha = kwargs["sha256"]
        if not self._store.exists(sha):
            return ToolResult(
                success=False,
                content=f"Artifact not found: {sha}",
                error=f"Artifact not found: {sha}",
                error_code=ErrorCode.BACKEND_ERROR,
            )
        path = self._store._artifact_path(sha)
        try:
            data = path.read_bytes()
            text = data.decode("utf-8", errors="replace")[:4000]
            size = len(data)
            return ToolResult(
                success=True,
                content=f"Artifact {sha[:12]}... ({size} bytes):\n{text}",
                data={"sha256": sha, "size_bytes": size, "preview": text[:500]},
            )
        except Exception as e:
            return ToolResult(
                success=False,
                content=f"Error reading artifact: {e}",
                error=str(e),
                error_code=ErrorCode.BACKEND_ERROR,
            )
