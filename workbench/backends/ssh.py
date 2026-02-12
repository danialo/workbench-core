"""SSH execution backend using asyncssh."""

from __future__ import annotations

import asyncio
import time

import asyncssh

from workbench.backends.base import BackendError, DiagnosticInfo, ExecutionBackend

# Cap output per stream to prevent memory issues.
_MAX_OUTPUT_BYTES = 100 * 1024  # 100 KB


class SSHBackend(ExecutionBackend):
    """Execution backend that runs commands on a remote host over SSH."""

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
        self._conn: asyncssh.SSHClientConnection | None = None

    def _check_connected(self) -> None:
        """Raise if not connected."""
        if not self._connected or self._conn is None:
            raise BackendError(
                "SSH backend not connected — call connect() first",
                code="not_connected",
            )

    async def connect(self) -> None:
        """Establish SSH connection."""
        import getpass

        kwargs: dict = {
            "host": self.host,
            "port": self.port,
            "username": self.username or getpass.getuser(),
            "known_hosts": None,  # Accept any host key (vendor-agnostic)
        }
        if self.key_path:
            kwargs["client_keys"] = [self.key_path]
        if self.password:
            kwargs["password"] = self.password

        try:
            self._conn = await asyncio.wait_for(
                asyncssh.connect(**kwargs),
                timeout=self.timeout,
            )
            self._connected = True
        except asyncio.TimeoutError:
            raise BackendError(
                f"SSH connection to {self.host}:{self.port} timed out after {self.timeout}s",
                code="timeout",
            )
        except asyncssh.PermissionDenied:
            raise BackendError(
                f"SSH authentication failed for {self.username}@{self.host}:{self.port}",
                code="auth_failed",
            )
        except (asyncssh.Error, OSError) as e:
            raise BackendError(
                f"SSH connection failed to {self.host}:{self.port}: {e}",
                code="connection_failed",
            )

    async def disconnect(self) -> None:
        """Close SSH connection."""
        if self._conn is not None:
            self._conn.close()
            self._conn = None
        self._connected = False

    async def resolve_target(self, target: str, **kwargs) -> dict:
        self._check_connected()
        result = await self.run_shell(
            'hostname; uname -s; uname -r; uname -m; python3 --version 2>/dev/null || echo "N/A"',
            target,
        )
        lines = result["stdout"].strip().splitlines()
        return {
            "type": "host",
            "hostname": lines[0] if len(lines) > 0 else "unknown",
            "platform": lines[1] if len(lines) > 1 else "unknown",
            "platform_release": lines[2] if len(lines) > 2 else "unknown",
            "architecture": lines[3] if len(lines) > 3 else "unknown",
            "python": lines[4].replace("Python ", "") if len(lines) > 4 and lines[4] != "N/A" else "N/A",
            "connection": f"ssh://{self.username}@{self.host}:{self.port}",
        }

    async def list_diagnostics(self, target: str, **kwargs) -> list[DiagnosticInfo]:
        self._check_connected()
        return [
            DiagnosticInfo("shell", "Execute an arbitrary shell command", "host"),
            DiagnosticInfo("ps", "List running processes", "host"),
            DiagnosticInfo("df", "Show disk usage", "host"),
            DiagnosticInfo("uptime", "Show system uptime and load", "host"),
            DiagnosticInfo("free", "Show memory usage", "host"),
            DiagnosticInfo("uname", "Show system information", "host"),
            DiagnosticInfo("who", "Show logged-in users", "host"),
        ]

    async def run_diagnostic(self, action: str, target: str, **kwargs) -> dict:
        self._check_connected()
        commands = {
            "ps": "ps aux --sort=-%mem | head -20",
            "df": "df -h",
            "uptime": "uptime",
            "free": "free -h",
            "uname": "uname -a",
            "who": "who",
        }
        cmd = commands.get(action)
        if cmd is None:
            raise BackendError(
                f"Unknown diagnostic action: {action}",
                code="unknown_diagnostic",
            )
        return await self.run_shell(cmd, target, **kwargs)

    async def run_shell(self, command: str, target: str, **kwargs) -> dict:
        self._check_connected()
        timeout: int | float = kwargs.get("timeout", 30)

        t0 = time.monotonic()
        try:
            completed = await asyncio.wait_for(
                self._conn.run(command, check=False),
                timeout=timeout,
            )
        except asyncio.TimeoutError:
            duration_ms = round((time.monotonic() - t0) * 1000)
            return {
                "exit_code": -1,
                "stdout": "",
                "stderr": f"Command timed out after {timeout}s",
                "duration_ms": duration_ms,
                "timed_out": True,
            }
        except asyncssh.Error as e:
            raise BackendError(
                f"SSH command execution failed: {e}",
                code="execution_failed",
            )

        duration_ms = round((time.monotonic() - t0) * 1000)

        stdout_raw = (completed.stdout or "").encode("utf-8")
        stderr_raw = (completed.stderr or "").encode("utf-8")

        stdout = stdout_raw[:_MAX_OUTPUT_BYTES].decode("utf-8", errors="replace")
        stderr = stderr_raw[:_MAX_OUTPUT_BYTES].decode("utf-8", errors="replace")

        truncated = {}
        if len(stdout_raw) > _MAX_OUTPUT_BYTES:
            truncated["stdout"] = True
        if len(stderr_raw) > _MAX_OUTPUT_BYTES:
            truncated["stderr"] = True

        result: dict = {
            "exit_code": completed.exit_status if completed.exit_status is not None else -1,
            "stdout": stdout,
            "stderr": stderr,
            "duration_ms": duration_ms,
        }
        if truncated:
            result["truncated"] = truncated
        return result
