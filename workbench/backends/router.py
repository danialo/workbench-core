"""Backend router — dispatches tool calls to the correct backend by target name."""

from __future__ import annotations

from workbench.backends.base import BackendError, DiagnosticInfo, ExecutionBackend

_LOCAL_ALIASES = frozenset({"localhost", "local", "127.0.0.1"})


class BackendRouter(ExecutionBackend):
    """Routes tool calls to the correct backend based on target name.

    Register named backends for specific targets (e.g. "prod-01") and
    set a default backend for localhost/unrecognized targets. All
    ExecutionBackend methods delegate to the resolved backend.
    """

    def __init__(self) -> None:
        self._backends: dict[str, ExecutionBackend] = {}
        self._default: ExecutionBackend | None = None

    def register(self, target: str, backend: ExecutionBackend) -> None:
        """Register a backend for a target name."""
        self._backends[target] = backend

    def set_default(self, backend: ExecutionBackend) -> None:
        """Set the fallback backend (used for localhost and unregistered targets)."""
        self._default = backend

    @property
    def targets(self) -> list[str]:
        """List all registered target names."""
        return list(self._backends.keys())

    def _resolve(self, target: str) -> ExecutionBackend:
        """Find the backend for a given target."""
        # Exact match first
        if target in self._backends:
            return self._backends[target]
        # Local aliases fall through to default
        if target in _LOCAL_ALIASES and self._default is not None:
            return self._default
        # Default fallback
        if self._default is not None:
            return self._default
        raise BackendError(
            f"No backend registered for target: {target}",
            code="no_backend",
        )

    async def resolve_target(self, target: str, **kwargs) -> dict:
        return await self._resolve(target).resolve_target(target, **kwargs)

    async def list_diagnostics(self, target: str, **kwargs) -> list[DiagnosticInfo]:
        return await self._resolve(target).list_diagnostics(target, **kwargs)

    async def run_diagnostic(self, action: str, target: str, **kwargs) -> dict:
        return await self._resolve(target).run_diagnostic(action, target, **kwargs)

    async def run_shell(self, command: str, target: str, **kwargs) -> dict:
        return await self._resolve(target).run_shell(command, target, **kwargs)
