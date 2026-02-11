"""SSH execution backend (stub — not yet connected)."""

from __future__ import annotations

from workbench.backends.base import BackendError, DiagnosticInfo, ExecutionBackend

_NOT_CONNECTED = BackendError(
    "SSH backend not connected — configure host credentials",
    code="not_connected",
)


class SSHBackend(ExecutionBackend):
    """
    Skeleton backend for future SSH connectivity.

    Wire in asyncssh or paramiko once remote targets are available.
    All methods raise BackendError until connect() succeeds.
    """

    def __init__(
        self,
        host: str,
        port: int = 22,
        username: str = "root",
        key_path: str | None = None,
        password: str | None = None,
        timeout: int = 10,
    ) -> None:
        self.host = host
        self.port = port
        self.username = username
        self.key_path = key_path
        self.password = password
        self.timeout = timeout
        self._connected = False

    async def connect(self) -> None:
        """Establish SSH session. Not yet implemented."""
        raise BackendError(
            "SSH connect not implemented — install asyncssh and wire up",
            code="not_implemented",
        )

    async def disconnect(self) -> None:
        """Close SSH session. Not yet implemented."""
        self._connected = False

    async def resolve_target(self, target: str, **kwargs) -> dict:
        raise _NOT_CONNECTED

    async def list_diagnostics(self, target: str, **kwargs) -> list[DiagnosticInfo]:
        raise _NOT_CONNECTED

    async def run_diagnostic(self, action: str, target: str, **kwargs) -> dict:
        raise _NOT_CONNECTED

    async def run_shell(self, command: str, target: str, **kwargs) -> dict:
        raise _NOT_CONNECTED
