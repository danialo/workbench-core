"""Tests for PolicyEngine."""

import asyncio
import json
import os
import tempfile

import pytest

from workbench.tools.base import ToolRisk, PrivacyScope
from workbench.tools.policy import PolicyEngine
from workbench.types import ToolResult, PolicyDecision
from tests.mock_tools import EchoTool, WriteTool, DestructiveTool, ShellTool


@pytest.fixture
def tmp_audit_path(tmp_path):
    return str(tmp_path / "audit.jsonl")


class TestPolicyCheck:
    """Tests for PolicyEngine.check()."""

    def test_risk_gating_blocks_above_max_risk(self, tmp_audit_path):
        engine = PolicyEngine(
            max_risk=ToolRisk.READ_ONLY,
            audit_log_path=tmp_audit_path,
        )
        tool = WriteTool()
        decision = engine.check(tool, {})
        assert decision.allowed is False
        assert "risk_too_high" in decision.reason
        assert "WRITE" in decision.reason
        assert "READ_ONLY" in decision.reason

    def test_risk_gating_allows_at_max_risk(self, tmp_audit_path):
        engine = PolicyEngine(
            max_risk=ToolRisk.WRITE,
            audit_log_path=tmp_audit_path,
        )
        tool = WriteTool()
        decision = engine.check(tool, {})
        assert decision.allowed is True

    def test_risk_gating_allows_below_max_risk(self, tmp_audit_path):
        engine = PolicyEngine(
            max_risk=ToolRisk.SHELL,
            audit_log_path=tmp_audit_path,
        )
        tool = EchoTool()
        decision = engine.check(tool, {})
        assert decision.allowed is True
        assert decision.reason == "ok"
        assert decision.requires_confirmation is False

    def test_destructive_requires_confirmation(self, tmp_audit_path):
        engine = PolicyEngine(
            max_risk=ToolRisk.DESTRUCTIVE,
            confirm_destructive=True,
            audit_log_path=tmp_audit_path,
        )
        tool = DestructiveTool()
        decision = engine.check(tool, {})
        assert decision.allowed is True
        assert decision.requires_confirmation is True

    def test_destructive_no_confirmation_when_disabled(self, tmp_audit_path):
        engine = PolicyEngine(
            max_risk=ToolRisk.DESTRUCTIVE,
            confirm_destructive=False,
            audit_log_path=tmp_audit_path,
        )
        tool = DestructiveTool()
        decision = engine.check(tool, {})
        assert decision.allowed is True
        assert decision.requires_confirmation is False

    def test_shell_requires_confirmation(self, tmp_audit_path):
        engine = PolicyEngine(
            max_risk=ToolRisk.SHELL,
            confirm_shell=True,
            audit_log_path=tmp_audit_path,
        )
        tool = ShellTool()
        decision = engine.check(tool, {"command": "ls"})
        assert decision.allowed is True
        assert decision.requires_confirmation is True

    def test_write_confirmation_when_enabled(self, tmp_audit_path):
        engine = PolicyEngine(
            max_risk=ToolRisk.WRITE,
            confirm_write=True,
            audit_log_path=tmp_audit_path,
        )
        tool = WriteTool()
        decision = engine.check(tool, {"path": "/tmp/x", "content": "y"})
        assert decision.allowed is True
        assert decision.requires_confirmation is True

    def test_blocked_pattern_blocks(self, tmp_audit_path):
        engine = PolicyEngine(
            max_risk=ToolRisk.SHELL,
            blocked_patterns=[r"rm\s+-rf"],
            audit_log_path=tmp_audit_path,
        )
        tool = ShellTool()
        decision = engine.check(tool, {"command": "rm -rf /"})
        assert decision.allowed is False
        assert decision.reason == "blocked_pattern"

    def test_blocked_pattern_allows_non_matching(self, tmp_audit_path):
        engine = PolicyEngine(
            max_risk=ToolRisk.SHELL,
            confirm_shell=False,
            blocked_patterns=[r"rm\s+-rf"],
            audit_log_path=tmp_audit_path,
        )
        tool = ShellTool()
        decision = engine.check(tool, {"command": "ls -la"})
        assert decision.allowed is True

    def test_multiple_blocked_patterns(self, tmp_audit_path):
        engine = PolicyEngine(
            max_risk=ToolRisk.SHELL,
            blocked_patterns=[r"rm\s+-rf", r"sudo"],
            audit_log_path=tmp_audit_path,
        )
        tool = ShellTool()
        decision = engine.check(tool, {"command": "sudo apt install"})
        assert decision.allowed is False


class TestRedaction:
    """Tests for redaction functionality."""

    def test_redact_secret_fields(self, tmp_audit_path):
        engine = PolicyEngine(
            max_risk=ToolRisk.SHELL,
            audit_log_path=tmp_audit_path,
        )
        tool = ShellTool()
        redacted = engine.redact_args_for_audit(tool, {"command": "secret-cmd", "timeout": 30})
        assert redacted["command"] == "***REDACTED***"
        assert redacted["timeout"] == 30

    def test_redaction_patterns_on_args(self, tmp_audit_path):
        engine = PolicyEngine(
            max_risk=ToolRisk.READ_ONLY,
            redaction_patterns=[r"sk-[A-Za-z0-9]+"],
            audit_log_path=tmp_audit_path,
        )
        tool = EchoTool()
        redacted = engine.redact_args_for_audit(
            tool, {"message": "my key is sk-abc123xyz"}
        )
        assert "sk-abc123xyz" not in redacted["message"]
        assert "***REDACTED***" in redacted["message"]

    def test_redaction_patterns_on_output(self, tmp_audit_path):
        engine = PolicyEngine(
            max_risk=ToolRisk.READ_ONLY,
            redaction_patterns=[r"\b\d{3}-\d{2}-\d{4}\b"],  # SSN pattern
            audit_log_path=tmp_audit_path,
        )
        output = engine.redact_output_for_audit("SSN: 123-45-6789 is private")
        assert "123-45-6789" not in output
        assert "***REDACTED***" in output

    def test_no_redaction_patterns_leaves_text_unchanged(self, tmp_audit_path):
        engine = PolicyEngine(
            max_risk=ToolRisk.READ_ONLY,
            audit_log_path=tmp_audit_path,
        )
        result = engine.redact_output_for_audit("no secrets here")
        assert result == "no secrets here"


class TestAuditLog:
    """Tests for async audit logging by privacy scope."""

    async def test_audit_log_public_scope(self, tmp_audit_path):
        engine = PolicyEngine(
            max_risk=ToolRisk.READ_ONLY,
            audit_log_path=tmp_audit_path,
        )
        tool = EchoTool()
        result = ToolResult(success=True, content="hello back")
        await engine.audit_log(
            session_id="sess-1",
            event_id="evt-1",
            tool=tool,
            args={"message": "hello"},
            result=result,
            duration_ms=42,
            tool_call_id="tc-1",
        )
        with open(tmp_audit_path, "r") as f:
            record = json.loads(f.readline())
        assert record["tool_name"] == "echo"
        assert record["privacy"] == "public"
        assert record["success"] is True
        assert record["args"] == {"message": "hello"}
        assert record["output"] == "hello back"
        assert record["duration_ms"] == 42

    async def test_audit_log_sensitive_scope(self, tmp_audit_path):
        engine = PolicyEngine(
            max_risk=ToolRisk.DESTRUCTIVE,
            audit_log_path=tmp_audit_path,
        )
        tool = DestructiveTool()
        result = ToolResult(success=True, content="Deleted resource-42")
        await engine.audit_log(
            session_id="sess-2",
            event_id="evt-2",
            tool=tool,
            args={"resource_id": "resource-42"},
            result=result,
            duration_ms=100,
            tool_call_id="tc-2",
        )
        with open(tmp_audit_path, "r") as f:
            record = json.loads(f.readline())
        assert record["tool_name"] == "delete_resource"
        assert record["privacy"] == "sensitive"
        # Sensitive: args should be fully redacted, output partially visible
        assert record["args"] == "***REDACTED***"
        assert "Deleted" in record["output"]

    async def test_audit_log_secret_scope(self, tmp_audit_path):
        engine = PolicyEngine(
            max_risk=ToolRisk.SHELL,
            audit_log_path=tmp_audit_path,
        )
        tool = ShellTool()
        result = ToolResult(success=True, content="command output here")
        await engine.audit_log(
            session_id="sess-3",
            event_id="evt-3",
            tool=tool,
            args={"command": "secret-cmd"},
            result=result,
            duration_ms=200,
            tool_call_id="tc-3",
        )
        with open(tmp_audit_path, "r") as f:
            record = json.loads(f.readline())
        assert record["tool_name"] == "shell"
        assert record["privacy"] == "secret"
        # Secret: both args and output fully redacted
        assert record["args"] == "***REDACTED***"
        assert record["output"] == "***REDACTED***"

    async def test_audit_log_with_redaction_patterns(self, tmp_audit_path):
        engine = PolicyEngine(
            max_risk=ToolRisk.READ_ONLY,
            redaction_patterns=[r"sk-[A-Za-z0-9]+"],
            audit_log_path=tmp_audit_path,
        )
        tool = EchoTool()
        result = ToolResult(success=True, content="Key: sk-abc123")
        await engine.audit_log(
            session_id="sess-4",
            event_id="evt-4",
            tool=tool,
            args={"message": "use key sk-xyz789"},
            result=result,
            duration_ms=10,
            tool_call_id="tc-4",
        )
        with open(tmp_audit_path, "r") as f:
            record = json.loads(f.readline())
        assert "sk-abc123" not in record["output"]
        assert "sk-xyz789" not in json.dumps(record["args"])
        assert "***REDACTED***" in record["output"]

    async def test_audit_log_truncates_output(self, tmp_audit_path):
        engine = PolicyEngine(
            max_risk=ToolRisk.READ_ONLY,
            audit_log_path=tmp_audit_path,
        )
        tool = EchoTool()
        long_content = "x" * 5000
        result = ToolResult(success=True, content=long_content)
        await engine.audit_log(
            session_id="sess-5",
            event_id="evt-5",
            tool=tool,
            args={"message": "test"},
            result=result,
            duration_ms=5,
            tool_call_id="tc-5",
        )
        with open(tmp_audit_path, "r") as f:
            record = json.loads(f.readline())
        # PUBLIC scope truncates to 2000 chars
        assert len(record["output"]) <= 2000

    async def test_audit_log_records_error_code(self, tmp_audit_path):
        engine = PolicyEngine(
            max_risk=ToolRisk.READ_ONLY,
            audit_log_path=tmp_audit_path,
        )
        tool = EchoTool()
        result = ToolResult(
            success=False,
            content="boom",
            error="something broke",
            error_code="tool_exception",
        )
        await engine.audit_log(
            session_id="sess-6",
            event_id="evt-6",
            tool=tool,
            args={"message": "fail"},
            result=result,
            duration_ms=1,
            tool_call_id="tc-6",
        )
        with open(tmp_audit_path, "r") as f:
            record = json.loads(f.readline())
        assert record["success"] is False
        assert record["error_code"] == "tool_exception"
