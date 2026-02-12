"""Tests for ToolRegistry."""

import pytest

from workbench.tools.base import ToolRisk
from workbench.tools.registry import ToolRegistry
from tests.mock_tools import EchoTool, WriteTool, DestructiveTool, ShellTool


class TestToolRegistry:
    """Test suite for ToolRegistry."""

    def test_register_and_get(self):
        reg = ToolRegistry()
        tool = EchoTool()
        reg.register(tool)
        assert reg.get("echo") is tool

    def test_get_returns_none_for_unknown(self):
        reg = ToolRegistry()
        assert reg.get("nonexistent") is None

    def test_require_returns_tool(self):
        reg = ToolRegistry()
        tool = EchoTool()
        reg.register(tool)
        assert reg.require("echo") is tool

    def test_require_raises_keyerror_for_unknown(self):
        reg = ToolRegistry()
        with pytest.raises(KeyError, match="nonexistent"):
            reg.require("nonexistent")

    def test_duplicate_registration_raises_valueerror(self):
        reg = ToolRegistry()
        reg.register(EchoTool())
        with pytest.raises(ValueError, match="already registered"):
            reg.register(EchoTool())

    def test_duplicate_registration_with_overwrite(self):
        reg = ToolRegistry()
        tool1 = EchoTool()
        tool2 = EchoTool()
        reg.register(tool1)
        reg.register(tool2, overwrite=True)
        assert reg.get("echo") is tool2

    def test_list_returns_all_sorted_by_name(self):
        reg = ToolRegistry()
        reg.register(ShellTool())
        reg.register(EchoTool())
        reg.register(WriteTool())
        reg.register(DestructiveTool())
        tools = reg.list()
        names = [t.name for t in tools]
        assert names == sorted(names)
        assert len(names) == 4

    def test_list_with_max_risk_read_only(self):
        reg = ToolRegistry()
        reg.register(EchoTool())
        reg.register(WriteTool())
        reg.register(DestructiveTool())
        reg.register(ShellTool())
        tools = reg.list(max_risk=ToolRisk.READ_ONLY)
        assert len(tools) == 1
        assert tools[0].name == "echo"

    def test_list_with_max_risk_write(self):
        reg = ToolRegistry()
        reg.register(EchoTool())
        reg.register(WriteTool())
        reg.register(DestructiveTool())
        reg.register(ShellTool())
        tools = reg.list(max_risk=ToolRisk.WRITE)
        names = [t.name for t in tools]
        assert "echo" in names
        assert "write_file" in names
        assert "delete_resource" not in names
        assert "shell" not in names

    def test_list_with_max_risk_destructive(self):
        reg = ToolRegistry()
        reg.register(EchoTool())
        reg.register(WriteTool())
        reg.register(DestructiveTool())
        reg.register(ShellTool())
        tools = reg.list(max_risk=ToolRisk.DESTRUCTIVE)
        names = [t.name for t in tools]
        assert len(names) == 3
        assert "shell" not in names

    def test_list_with_max_risk_shell_includes_all(self):
        reg = ToolRegistry()
        reg.register(EchoTool())
        reg.register(WriteTool())
        reg.register(DestructiveTool())
        reg.register(ShellTool())
        tools = reg.list(max_risk=ToolRisk.SHELL)
        assert len(tools) == 4

    def test_to_openai_schema(self):
        reg = ToolRegistry()
        reg.register(EchoTool())
        reg.register(WriteTool())
        schema = reg.to_openai_schema()
        assert isinstance(schema, list)
        assert len(schema) == 2
        for entry in schema:
            assert entry["type"] == "function"
            assert "function" in entry
            fn = entry["function"]
            assert "name" in fn
            assert "description" in fn
            assert "parameters" in fn
            assert fn["parameters"]["type"] == "object"

    def test_to_openai_schema_sorted(self):
        reg = ToolRegistry()
        reg.register(WriteTool())
        reg.register(EchoTool())
        schema = reg.to_openai_schema()
        names = [s["function"]["name"] for s in schema]
        assert names == sorted(names)

    def test_load_plugins_disabled_returns_zero(self):
        reg = ToolRegistry()
        count = reg.load_plugins(enabled=False)
        assert count == 0

    def test_load_plugins_disabled_no_tools_added(self):
        reg = ToolRegistry()
        reg.load_plugins(enabled=False)
        assert len(reg.list()) == 0

    def test_load_plugins_disabled_with_backend_returns_zero(self):
        reg = ToolRegistry()
        count = reg.load_plugins(enabled=False, backend=object())
        assert count == 0

    def test_empty_registry_list(self):
        reg = ToolRegistry()
        assert reg.list() == []

    def test_empty_registry_to_openai_schema(self):
        reg = ToolRegistry()
        assert reg.to_openai_schema() == []
