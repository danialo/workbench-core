"""Tests for LocalBackend, SSHBackend, and RunShellTool."""

from __future__ import annotations

import asyncio
import platform

import pytest

from workbench.backends.base import BackendError
from workbench.backends.local import LocalBackend
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
# SSHBackend
# ---------------------------------------------------------------------------

class TestSSHBackend:

    @pytest.fixture
    def backend(self):
        return SSHBackend(host="example.com", username="deploy")

    @pytest.mark.asyncio
    async def test_resolve_target_raises(self, backend):
        with pytest.raises(BackendError, match="not connected"):
            await backend.resolve_target("example.com")

    @pytest.mark.asyncio
    async def test_list_diagnostics_raises(self, backend):
        with pytest.raises(BackendError, match="not connected"):
            await backend.list_diagnostics("example.com")

    @pytest.mark.asyncio
    async def test_run_diagnostic_raises(self, backend):
        with pytest.raises(BackendError, match="not connected"):
            await backend.run_diagnostic("ps", "example.com")

    @pytest.mark.asyncio
    async def test_run_shell_raises(self, backend):
        with pytest.raises(BackendError, match="not connected"):
            await backend.run_shell("ls", "example.com")

    @pytest.mark.asyncio
    async def test_connect_raises(self, backend):
        with pytest.raises(BackendError, match="not implemented"):
            await backend.connect()

    @pytest.mark.asyncio
    async def test_disconnect_clears_flag(self, backend):
        await backend.disconnect()
        assert backend._connected is False

    def test_constructor_stores_params(self):
        ssh = SSHBackend(
            host="10.0.0.1",
            port=2222,
            username="admin",
            key_path="/home/user/.ssh/id_rsa",
            timeout=15,
        )
        assert ssh.host == "10.0.0.1"
        assert ssh.port == 2222
        assert ssh.username == "admin"
        assert ssh.key_path == "/home/user/.ssh/id_rsa"
        assert ssh.timeout == 15


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
