"""Local shell execution backend."""

from __future__ import annotations

import asyncio
import platform
import time

from workbench.backends.base import BackendError, DiagnosticInfo, ExecutionBackend

# Cap output per stream to prevent memory issues.
_MAX_OUTPUT_BYTES = 100 * 1024  # 100 KB


class LocalBackend(ExecutionBackend):
    """Execution backend that runs commands on the local machine."""

    async def resolve_target(self, target: str, **kwargs) -> dict:
        if target not in ("localhost", "local", "127.0.0.1"):
            raise BackendError(
                f"LocalBackend only supports localhost, got: {target}",
                code="invalid_target",
            )
        return {
            "type": "host",
            "hostname": platform.node(),
            "platform": platform.system(),
            "platform_release": platform.release(),
            "architecture": platform.machine(),
            "python": platform.python_version(),
        }

    async def list_diagnostics(self, target: str, **kwargs) -> list[DiagnosticInfo]:
        if target not in ("localhost", "local", "127.0.0.1"):
            raise BackendError(
                f"LocalBackend only supports localhost, got: {target}",
                code="invalid_target",
            )
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
        timeout: int | float = kwargs.get("timeout", 30)
        cwd: str | None = kwargs.get("cwd")

        t0 = time.monotonic()
        try:
            proc = await asyncio.create_subprocess_shell(
                command,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=cwd,
            )
            try:
                stdout_raw, stderr_raw = await asyncio.wait_for(
                    proc.communicate(), timeout=timeout
                )
            except asyncio.TimeoutError:
                try:
                    proc.terminate()
                    await asyncio.wait_for(proc.wait(), timeout=5)
                except (ProcessLookupError, asyncio.TimeoutError):
                    pass
                duration_ms = round((time.monotonic() - t0) * 1000)
                return {
                    "exit_code": -1,
                    "stdout": "",
                    "stderr": f"Command timed out after {timeout}s",
                    "duration_ms": duration_ms,
                    "timed_out": True,
                }
        except ProcessLookupError:
            duration_ms = round((time.monotonic() - t0) * 1000)
            return {
                "exit_code": -1,
                "stdout": "",
                "stderr": "Process exited before output could be collected",
                "duration_ms": duration_ms,
            }

        duration_ms = round((time.monotonic() - t0) * 1000)

        stdout = stdout_raw[:_MAX_OUTPUT_BYTES].decode("utf-8", errors="replace")
        stderr = stderr_raw[:_MAX_OUTPUT_BYTES].decode("utf-8", errors="replace")

        truncated = {}
        if len(stdout_raw) > _MAX_OUTPUT_BYTES:
            truncated["stdout"] = True
        if len(stderr_raw) > _MAX_OUTPUT_BYTES:
            truncated["stderr"] = True

        result: dict = {
            "exit_code": proc.returncode,
            "stdout": stdout,
            "stderr": stderr,
            "duration_ms": duration_ms,
        }
        if truncated:
            result["truncated"] = truncated
        return result
