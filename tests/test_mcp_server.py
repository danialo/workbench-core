"""Tests for the MCP server module."""

import pytest
from mcp import types as mcp_types

from workbench.mcp.server import create_mcp_server
from workbench.tools.base import Tool, ToolRisk
from workbench.tools.registry import ToolRegistry
from workbench.tools.policy import PolicyEngine
from workbench.types import ToolResult


# ---------------------------------------------------------------------------
# Fixtures — minimal tools for testing
# ---------------------------------------------------------------------------

class EchoTool(Tool):
    name = "echo"
    description = "Echoes the input text"
    parameters = {
        "type": "object",
        "properties": {"text": {"type": "string"}},
        "required": ["text"],
    }

    @property
    def risk_level(self):
        return ToolRisk.READ_ONLY

    async def execute(self, *, text: str = "", **kw) -> ToolResult:
        return ToolResult(success=True, content=text)


class ShellTool(Tool):
    name = "run_shell"
    description = "Runs a shell command"
    parameters = {
        "type": "object",
        "properties": {"command": {"type": "string"}},
        "required": ["command"],
    }

    @property
    def risk_level(self):
        return ToolRisk.SHELL

    async def execute(self, *, command: str = "", **kw) -> ToolResult:
        return ToolResult(success=True, content=f"ran: {command}")


class ErrorTool(Tool):
    name = "fail"
    description = "Always fails"
    parameters = {"type": "object", "properties": {}}

    async def execute(self, **kw) -> ToolResult:
        raise RuntimeError("boom")


class DataTool(Tool):
    name = "data_tool"
    description = "Returns structured data"
    parameters = {"type": "object", "properties": {}}

    async def execute(self, **kw) -> ToolResult:
        return ToolResult(success=True, content="", data={"key": "value"})


@pytest.fixture
def registry():
    r = ToolRegistry()
    r.register(EchoTool())
    r.register(ShellTool())
    r.register(ErrorTool())
    r.register(DataTool())
    return r


@pytest.fixture
def permissive_policy(tmp_path):
    return PolicyEngine(
        max_risk=ToolRisk.SHELL,
        confirm_destructive=False,
        confirm_shell=False,
        blocked_patterns=[],
        audit_log_path=str(tmp_path / "audit.jsonl"),
    )


@pytest.fixture
def restrictive_policy(tmp_path):
    return PolicyEngine(
        max_risk=ToolRisk.READ_ONLY,
        confirm_destructive=True,
        confirm_shell=True,
        blocked_patterns=[r"rm\s+-rf"],
        audit_log_path=str(tmp_path / "audit.jsonl"),
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

async def _list_tools(server):
    """Call the list_tools handler directly."""
    handler = server.request_handlers[mcp_types.ListToolsRequest]
    result = await handler(None)
    return result.root.tools


async def _call_tool(server, name, arguments=None):
    """Call the call_tool handler directly, returns list of content blocks."""
    handler = server.request_handlers[mcp_types.CallToolRequest]
    req = mcp_types.CallToolRequest(
        method="tools/call",
        params=mcp_types.CallToolRequestParams(name=name, arguments=arguments or {}),
    )
    result = await handler(req)
    return result.root.content


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestListTools:
    async def test_lists_all_tools(self, registry):
        server = create_mcp_server(registry)
        tools = await _list_tools(server)
        assert len(tools) == 4
        names = {t.name for t in tools}
        assert names == {"echo", "run_shell", "fail", "data_tool"}

    async def test_tool_filter(self, registry):
        server = create_mcp_server(registry, tool_filter=["echo", "data_tool"])
        tools = await _list_tools(server)
        names = {t.name for t in tools}
        assert names == {"echo", "data_tool"}

    async def test_schema_mapping(self, registry):
        server = create_mcp_server(registry)
        tools = await _list_tools(server)
        echo = [t for t in tools if t.name == "echo"][0]
        assert echo.description == "Echoes the input text"
        assert "text" in echo.inputSchema["properties"]


class TestCallTool:
    async def test_basic_execution(self, registry):
        server = create_mcp_server(registry)
        content = await _call_tool(server, "echo", {"text": "hello"})
        texts = [c.text for c in content]
        assert any("hello" in t for t in texts)

    async def test_unknown_tool(self, registry):
        server = create_mcp_server(registry)
        content = await _call_tool(server, "nonexistent", {})
        texts = [c.text for c in content]
        assert any("Unknown tool" in t for t in texts)

    async def test_tool_error_returns_text(self, registry):
        server = create_mcp_server(registry)
        content = await _call_tool(server, "fail", {})
        texts = [c.text for c in content]
        assert any("Error" in t or "boom" in t for t in texts)

    async def test_data_result(self, registry):
        server = create_mcp_server(registry)
        content = await _call_tool(server, "data_tool", {})
        combined = " ".join(c.text for c in content)
        assert "key" in combined and "value" in combined


class TestPolicyEnforcement:
    async def test_policy_blocks_high_risk(self, registry, restrictive_policy):
        server = create_mcp_server(registry, restrictive_policy)
        content = await _call_tool(server, "run_shell", {"command": "ls"})
        texts = [c.text for c in content]
        assert any("Policy blocked" in t for t in texts)

    async def test_policy_allows_low_risk(self, registry, restrictive_policy):
        server = create_mcp_server(registry, restrictive_policy)
        content = await _call_tool(server, "echo", {"text": "safe"})
        texts = [c.text for c in content]
        assert any("safe" in t for t in texts)

    async def test_permissive_allows_shell(self, registry, permissive_policy):
        server = create_mcp_server(registry, permissive_policy)
        content = await _call_tool(server, "run_shell", {"command": "echo ok"})
        texts = [c.text for c in content]
        assert any("ran:" in t for t in texts)

    async def test_no_policy_allows_all(self, registry):
        server = create_mcp_server(registry, policy=None)
        content = await _call_tool(server, "run_shell", {"command": "ls"})
        texts = [c.text for c in content]
        assert any("ran:" in t for t in texts)
