"""Tests for LocalBackend, SSHBackend, BackendRouter, and RunShellTool."""

from __future__ import annotations

import asyncio
import platform
import shutil
import subprocess

import pytest

from workbench.backends.base import BackendError, ExecutionBackend, DiagnosticInfo
from workbench.backends.local import LocalBackend
from workbench.backends.router import BackendRouter
from workbench.backends.ssh import SSHBackend
from workbench.backends.bridge import RunShellTool
from workbench.tools.base import ToolRisk, PrivacyScope


# ---------------------------------------------------------------------------
# LocalBackend
# ---------------------------------------------------------------------------

class TestLocalBackend:

    @pytest.fixture
    def backend(self):
        return LocalBackend()

    # -- resolve_target --

    @pytest.mark.asyncio
    async def test_resolve_target_localhost(self, backend):
        info = await backend.resolve_target("localhost")
        assert info["type"] == "host"
        assert info["hostname"] == platform.node()
        assert info["platform"] == platform.system()

    @pytest.mark.asyncio
    async def test_resolve_target_aliases(self, backend):
        for alias in ("local", "127.0.0.1"):
            info = await backend.resolve_target(alias)
            assert info["type"] == "host"

    @pytest.mark.asyncio
    async def test_resolve_target_invalid(self, backend):
        with pytest.raises(BackendError, match="only supports localhost"):
            await backend.resolve_target("remote-host")

    # -- list_diagnostics --

    @pytest.mark.asyncio
    async def test_list_diagnostics(self, backend):
        diags = await backend.list_diagnostics("localhost")
        names = [d.name for d in diags]
        assert "shell" in names
        assert "ps" in names
        assert "df" in names
        assert "uptime" in names

    @pytest.mark.asyncio
    async def test_list_diagnostics_invalid_target(self, backend):
        with pytest.raises(BackendError):
            await backend.list_diagnostics("not-localhost")

    # -- run_diagnostic --

    @pytest.mark.asyncio
    async def test_run_diagnostic_uptime(self, backend):
        result = await backend.run_diagnostic("uptime", "localhost")
        assert result["exit_code"] == 0
        assert result["stdout"]  # uptime always produces output

    @pytest.mark.asyncio
    async def test_run_diagnostic_unknown(self, backend):
        with pytest.raises(BackendError, match="Unknown diagnostic"):
            await backend.run_diagnostic("nonexistent", "localhost")

    # -- run_shell --

    @pytest.mark.asyncio
    async def test_run_shell_basic(self, backend):
        result = await backend.run_shell("echo hello", "localhost")
        assert result["exit_code"] == 0
        assert "hello" in result["stdout"]
        assert isinstance(result["duration_ms"], int)

    @pytest.mark.asyncio
    async def test_run_shell_stderr(self, backend):
        result = await backend.run_shell("echo err >&2", "localhost")
        assert "err" in result["stderr"]

    @pytest.mark.asyncio
    async def test_run_shell_nonzero_exit(self, backend):
        result = await backend.run_shell("exit 42", "localhost")
        assert result["exit_code"] == 42

    @pytest.mark.asyncio
    async def test_run_shell_cwd(self, backend):
        result = await backend.run_shell("pwd", "localhost", cwd="/tmp")
        assert result["stdout"].strip() == "/tmp"

    @pytest.mark.asyncio
    async def test_run_shell_timeout(self, backend):
        result = await backend.run_shell("sleep 60", "localhost", timeout=0.5)
        assert result["exit_code"] == -1
        assert result["timed_out"] is True
        assert "timed out" in result["stderr"]

    @pytest.mark.asyncio
    async def test_run_shell_output_has_duration(self, backend):
        result = await backend.run_shell("true", "localhost")
        assert "duration_ms" in result
        assert result["duration_ms"] >= 0


# ---------------------------------------------------------------------------
# SSHBackend — unit tests (no real SSH connection needed)
# ---------------------------------------------------------------------------

class TestSSHBackendUnit:

    @pytest.fixture
    def backend(self):
        return SSHBackend(host="example.com", username="deploy")

    def test_constructor_stores_params(self):
        ssh = SSHBackend(
            host="10.0.0.1",
            port=2222,
            username="admin",
            key_path="/home/user/.ssh/id_rsa",
            password="secret",
            timeout=15,
        )
        assert ssh.host == "10.0.0.1"
        assert ssh.port == 2222
        assert ssh.username == "admin"
        assert ssh.key_path == "/home/user/.ssh/id_rsa"
        assert ssh.password == "secret"
        assert ssh.timeout == 15
        assert ssh._connected is False
        assert ssh._conn is None

    def test_check_connected_raises_when_disconnected(self, backend):
        with pytest.raises(BackendError, match="not connected"):
            backend._check_connected()

    @pytest.mark.asyncio
    async def test_resolve_target_raises_when_disconnected(self, backend):
        with pytest.raises(BackendError, match="not connected"):
            await backend.resolve_target("example.com")

    @pytest.mark.asyncio
    async def test_list_diagnostics_raises_when_disconnected(self, backend):
        with pytest.raises(BackendError, match="not connected"):
            await backend.list_diagnostics("example.com")

    @pytest.mark.asyncio
    async def test_run_diagnostic_raises_when_disconnected(self, backend):
        with pytest.raises(BackendError, match="not connected"):
            await backend.run_diagnostic("ps", "example.com")

    @pytest.mark.asyncio
    async def test_run_shell_raises_when_disconnected(self, backend):
        with pytest.raises(BackendError, match="not connected"):
            await backend.run_shell("ls", "example.com")

    @pytest.mark.asyncio
    async def test_connect_fails_for_unreachable_host(self):
        ssh = SSHBackend(host="192.0.2.1", timeout=1)  # RFC 5737 TEST-NET
        with pytest.raises(BackendError):
            await ssh.connect()
        assert ssh._connected is False

    @pytest.mark.asyncio
    async def test_disconnect_clears_state(self, backend):
        await backend.disconnect()
        assert backend._connected is False
        assert backend._conn is None

    @pytest.mark.asyncio
    async def test_disconnect_idempotent(self, backend):
        await backend.disconnect()
        await backend.disconnect()  # should not raise
        assert backend._connected is False


# ---------------------------------------------------------------------------
# BackendRouter
# ---------------------------------------------------------------------------

class _FakeBackend(ExecutionBackend):
    """Minimal backend for router tests."""

    def __init__(self, name: str):
        self.name = name

    async def resolve_target(self, target: str, **kwargs) -> dict:
        return {"backend": self.name, "target": target}

    async def list_diagnostics(self, target: str, **kwargs) -> list[DiagnosticInfo]:
        return [DiagnosticInfo("test", f"from {self.name}", "host")]

    async def run_diagnostic(self, action: str, target: str, **kwargs) -> dict:
        return {"backend": self.name, "action": action}

    async def run_shell(self, command: str, target: str, **kwargs) -> dict:
        return {"backend": self.name, "command": command, "exit_code": 0, "stdout": "", "stderr": "", "duration_ms": 0}


class TestBackendRouter:

    @pytest.fixture
    def router(self):
        r = BackendRouter()
        r.set_default(_FakeBackend("local"))
        return r

    def test_register_and_targets(self, router):
        router.register("prod-01", _FakeBackend("ssh-prod"))
        assert "prod-01" in router.targets

    def test_resolve_registered_target(self, router):
        prod = _FakeBackend("ssh-prod")
        router.register("prod-01", prod)
        assert router._resolve("prod-01") is prod

    def test_resolve_localhost_uses_default(self, router):
        result = router._resolve("localhost")
        assert result is not None

    def test_resolve_local_aliases(self, router):
        for alias in ("localhost", "local", "127.0.0.1"):
            backend = router._resolve(alias)
            assert backend is not None

    def test_resolve_unknown_uses_default(self, router):
        backend = router._resolve("unknown-host")
        assert backend is not None

    def test_resolve_no_default_raises(self):
        router = BackendRouter()
        with pytest.raises(BackendError, match="No backend"):
            router._resolve("anything")

    @pytest.mark.asyncio
    async def test_routes_resolve_target(self, router):
        router.register("prod-01", _FakeBackend("ssh-prod"))
        info = await router.resolve_target("prod-01")
        assert info["backend"] == "ssh-prod"

    @pytest.mark.asyncio
    async def test_routes_run_shell(self, router):
        router.register("prod-01", _FakeBackend("ssh-prod"))
        result = await router.run_shell("ls", "prod-01")
        assert result["backend"] == "ssh-prod"

    @pytest.mark.asyncio
    async def test_routes_run_shell_localhost(self, router):
        result = await router.run_shell("ls", "localhost")
        assert result["backend"] == "local"

    @pytest.mark.asyncio
    async def test_routes_list_diagnostics(self, router):
        router.register("staging", _FakeBackend("ssh-staging"))
        diags = await router.list_diagnostics("staging")
        assert diags[0].description == "from ssh-staging"

    @pytest.mark.asyncio
    async def test_routes_run_diagnostic(self, router):
        router.register("staging", _FakeBackend("ssh-staging"))
        result = await router.run_diagnostic("uptime", "staging")
        assert result["backend"] == "ssh-staging"

    @pytest.mark.asyncio
    async def test_multiple_backends(self, router):
        router.register("prod", _FakeBackend("prod"))
        router.register("staging", _FakeBackend("staging"))
        r1 = await router.resolve_target("prod")
        r2 = await router.resolve_target("staging")
        r3 = await router.resolve_target("localhost")
        assert r1["backend"] == "prod"
        assert r2["backend"] == "staging"
        assert r3["backend"] == "local"


# ---------------------------------------------------------------------------
# SSHBackend — integration tests (require local SSH access)
# ---------------------------------------------------------------------------

def _ssh_available() -> bool:
    """Check if we can SSH to localhost (sshd running + keys configured)."""
    try:
        result = subprocess.run(
            ["ssh", "-o", "BatchMode=yes", "-o", "StrictHostKeyChecking=no",
             "-o", "ConnectTimeout=2", "localhost", "echo", "ok"],
            capture_output=True, text=True, timeout=5,
        )
        return result.returncode == 0 and "ok" in result.stdout
    except Exception:
        return False


_skip_no_ssh = pytest.mark.skipif(
    not _ssh_available(),
    reason="No local SSH access (sshd not running or keys not configured)",
)


@_skip_no_ssh
class TestSSHBackendIntegration:

    @pytest.fixture
    async def backend(self):
        import getpass
        ssh = SSHBackend(host="localhost", username=getpass.getuser(), timeout=5)
        await ssh.connect()
        yield ssh
        await ssh.disconnect()

    @pytest.mark.asyncio
    async def test_connect_and_disconnect(self, backend):
        assert backend._connected is True
        assert backend._conn is not None

    @pytest.mark.asyncio
    async def test_run_shell_echo(self, backend):
        result = await backend.run_shell("echo hello-ssh", "localhost")
        assert result["exit_code"] == 0
        assert "hello-ssh" in result["stdout"]
        assert isinstance(result["duration_ms"], int)

    @pytest.mark.asyncio
    async def test_run_shell_stderr(self, backend):
        result = await backend.run_shell("echo err >&2", "localhost")
        assert "err" in result["stderr"]

    @pytest.mark.asyncio
    async def test_run_shell_nonzero_exit(self, backend):
        result = await backend.run_shell("exit 42", "localhost")
        assert result["exit_code"] == 42

    @pytest.mark.asyncio
    async def test_run_shell_timeout(self, backend):
        result = await backend.run_shell("sleep 60", "localhost", timeout=0.5)
        assert result["exit_code"] == -1
        assert result["timed_out"] is True

    @pytest.mark.asyncio
    async def test_resolve_target(self, backend):
        info = await backend.resolve_target("localhost")
        assert info["type"] == "host"
        assert info["hostname"]  # should be non-empty
        assert "connection" in info

    @pytest.mark.asyncio
    async def test_list_diagnostics(self, backend):
        diags = await backend.list_diagnostics("localhost")
        names = [d.name for d in diags]
        assert "uptime" in names
        assert "df" in names

    @pytest.mark.asyncio
    async def test_run_diagnostic_uptime(self, backend):
        result = await backend.run_diagnostic("uptime", "localhost")
        assert result["exit_code"] == 0
        assert result["stdout"]

    @pytest.mark.asyncio
    async def test_run_diagnostic_unknown(self, backend):
        with pytest.raises(BackendError, match="Unknown diagnostic"):
            await backend.run_diagnostic("nonexistent", "localhost")


# ---------------------------------------------------------------------------
# RunShellTool (bridge)
# ---------------------------------------------------------------------------

class TestRunShellTool:

    @pytest.fixture
    def tool(self):
        return RunShellTool(LocalBackend())

    def test_name(self, tool):
        assert tool.name == "run_shell"

    def test_risk_level(self, tool):
        assert tool.risk_level == ToolRisk.SHELL

    def test_privacy_scope(self, tool):
        assert tool.privacy_scope == PrivacyScope.SENSITIVE

    def test_schema_has_command_required(self, tool):
        schema = tool.to_openai_schema()
        params = schema["function"]["parameters"]
        assert "command" in params["properties"]
        assert "command" in params["required"]

    def test_schema_target_optional(self, tool):
        schema = tool.to_openai_schema()
        params = schema["function"]["parameters"]
        assert "target" in params["properties"]
        assert "target" not in params["required"]

    @pytest.mark.asyncio
    async def test_execute_success(self, tool):
        result = await tool.execute(command="echo test123")
        assert result.success is True
        assert "test123" in result.content
        assert result.data["exit_code"] == 0

    @pytest.mark.asyncio
    async def test_execute_failure(self, tool):
        result = await tool.execute(command="exit 1")
        assert result.success is False
        assert "exit code: 1" in result.content

    @pytest.mark.asyncio
    async def test_execute_with_stderr(self, tool):
        result = await tool.execute(command="echo oops >&2 && exit 1")
        assert result.success is False
        assert "oops" in result.content
        assert "[stderr]" in result.content

    @pytest.mark.asyncio
    async def test_execute_default_target(self, tool):
        # Should work without explicit target (defaults to localhost)
        result = await tool.execute(command="true")
        assert result.success is True

    @pytest.mark.asyncio
    async def test_execute_with_timeout(self, tool):
        result = await tool.execute(command="sleep 60", timeout=0.5)
        assert result.success is False
        assert result.data["timed_out"] is True

    @pytest.mark.asyncio
    async def test_execute_backend_error(self):
        """RunShellTool wraps BackendError into a failed ToolResult."""
        tool = RunShellTool(SSHBackend(host="unreachable"))
        result = await tool.execute(command="ls")
        assert result.success is False
        assert result.error_code == "backend_error"
        assert "not connected" in result.content


# ---------------------------------------------------------------------------
# RunShellTool with BackendRouter
# ---------------------------------------------------------------------------

class TestRunShellToolWithRouter:

    @pytest.fixture
    def tool(self):
        router = BackendRouter()
        router.set_default(LocalBackend())
        return RunShellTool(router)

    @pytest.mark.asyncio
    async def test_routes_to_local_by_default(self, tool):
        result = await tool.execute(command="echo routed")
        assert result.success is True
        assert "routed" in result.content

    @pytest.mark.asyncio
    async def test_routes_to_registered_backend(self):
        router = BackendRouter()
        router.set_default(LocalBackend())
        router.register("fake-host", _FakeBackend("fake"))
        tool = RunShellTool(router)
        result = await tool.execute(command="ls", target="fake-host")
        assert result.success is True

    @pytest.mark.asyncio
    async def test_unregistered_target_falls_to_default(self, tool):
        # Unknown target falls through to default (LocalBackend)
        # LocalBackend's run_shell executes locally regardless of target name
        result = await tool.execute(command="echo test", target="unknown-host")
        assert result.success is True
        assert "test" in result.content
