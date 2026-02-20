"""Tests for memory bridge tools."""

import pytest
import tempfile
from pathlib import Path

from workbench.memory.sqlite_provider import SQLiteMemoryProvider
from workbench.memory.file_provider import FileMemoryProvider
from workbench.memory.tools import MemoryReadTool, MemoryWriteTool, build_memory_context
from workbench.tools.base import ToolRisk


class TestMemoryReadTool:
    """Test suite for MemoryReadTool."""

    @pytest.fixture
    async def provider(self, tmp_path):
        db = str(tmp_path / "test.db")
        p = SQLiteMemoryProvider(db)
        await p.init()
        return p

    @pytest.fixture
    def tool(self, provider):
        return MemoryReadTool(provider, "test-ws")

    def test_properties(self):
        p = SQLiteMemoryProvider(":memory:")
        tool = MemoryReadTool(p, "ws")
        assert tool.name == "memory_read"
        assert tool.risk_level == ToolRisk.READ_ONLY
        assert "action" in tool.parameters["properties"]

    async def test_list_empty(self, tool):
        result = await tool.execute(action="list")
        assert result.success
        assert "No memory entries" in result.content

    async def test_list_with_entries(self, provider, tool):
        await provider.set("test-ws", "key1", "value1")
        await provider.set("test-ws", "key2", "value2")
        result = await tool.execute(action="list")
        assert result.success
        assert "key1" in result.content
        assert "key2" in result.content

    async def test_get_existing(self, provider, tool):
        await provider.set("test-ws", "mykey", "myvalue")
        result = await tool.execute(action="get", key="mykey")
        assert result.success
        assert result.content == "myvalue"

    async def test_get_missing(self, tool):
        result = await tool.execute(action="get", key="nonexistent")
        assert result.success
        assert "No memory entry" in result.content

    async def test_get_without_key(self, tool):
        result = await tool.execute(action="get")
        assert not result.success
        assert "key" in result.error.lower()

    async def test_unknown_action(self, tool):
        result = await tool.execute(action="bad")
        assert not result.success
        assert "Unknown action" in result.content


class TestMemoryWriteTool:
    """Test suite for MemoryWriteTool."""

    @pytest.fixture
    async def provider(self, tmp_path):
        db = str(tmp_path / "test.db")
        p = SQLiteMemoryProvider(db)
        await p.init()
        return p

    @pytest.fixture
    def tool(self, provider):
        return MemoryWriteTool(provider, "test-ws")

    def test_properties(self):
        p = SQLiteMemoryProvider(":memory:")
        tool = MemoryWriteTool(p, "ws")
        assert tool.name == "memory_write"
        assert tool.risk_level == ToolRisk.WRITE
        assert "action" in tool.parameters["properties"]

    async def test_set(self, provider, tool):
        result = await tool.execute(action="set", key="k1", value="v1")
        assert result.success
        assert "Stored" in result.content
        # Verify it was actually stored
        assert await provider.get("test-ws", "k1") == "v1"

    async def test_set_without_value(self, tool):
        result = await tool.execute(action="set", key="k1")
        assert not result.success
        assert "value" in result.error.lower()

    async def test_delete_existing(self, provider, tool):
        await provider.set("test-ws", "k1", "v1")
        result = await tool.execute(action="delete", key="k1")
        assert result.success
        assert "Deleted" in result.content
        assert await provider.get("test-ws", "k1") is None

    async def test_delete_missing(self, tool):
        result = await tool.execute(action="delete", key="nope")
        assert result.success
        assert "No memory entry" in result.content

    async def test_unknown_action(self, tool):
        result = await tool.execute(action="bad", key="k")
        assert not result.success


class TestBuildMemoryContext:
    """Test suite for build_memory_context."""

    async def test_empty_context(self, tmp_path):
        db = str(tmp_path / "test.db")
        sqlite_p = SQLiteMemoryProvider(db)
        await sqlite_p.init()
        file_p = FileMemoryProvider()

        result = await build_memory_context(
            sqlite_p, file_p, "ws", str(tmp_path)
        )
        assert result == ""

    async def test_sqlite_entries(self, tmp_path):
        db = str(tmp_path / "test.db")
        sqlite_p = SQLiteMemoryProvider(db)
        await sqlite_p.init()
        await sqlite_p.set("ws", "notes", "important stuff")
        file_p = FileMemoryProvider()

        result = await build_memory_context(
            sqlite_p, file_p, "ws", str(tmp_path)
        )
        assert "## Workspace Memory" in result
        assert "### notes" in result
        assert "important stuff" in result

    async def test_file_entries(self, tmp_path):
        db = str(tmp_path / "test.db")
        sqlite_p = SQLiteMemoryProvider(db)
        await sqlite_p.init()
        file_p = FileMemoryProvider()

        # Create a CLAUDE.md in the workspace
        (tmp_path / "CLAUDE.md").write_text("# Project Instructions\nDo stuff.")

        result = await build_memory_context(
            sqlite_p, file_p, "ws", str(tmp_path)
        )
        assert "## Workspace Memory" in result
        assert "### CLAUDE.md" in result
        assert "Project Instructions" in result

    async def test_combined_entries(self, tmp_path):
        db = str(tmp_path / "test.db")
        sqlite_p = SQLiteMemoryProvider(db)
        await sqlite_p.init()
        await sqlite_p.set("ws", "context", "agent notes here")
        file_p = FileMemoryProvider()
        (tmp_path / "README.md").write_text("# My Project")

        result = await build_memory_context(
            sqlite_p, file_p, "ws", str(tmp_path)
        )
        assert "### README.md" in result
        assert "### context" in result
        assert "agent notes here" in result

    async def test_truncation(self, tmp_path):
        db = str(tmp_path / "test.db")
        sqlite_p = SQLiteMemoryProvider(db)
        await sqlite_p.init()
        file_p = FileMemoryProvider()

        # Create a large file
        (tmp_path / "CLAUDE.md").write_text("x" * 10000)

        result = await build_memory_context(
            sqlite_p, file_p, "ws", str(tmp_path)
        )
        assert "truncated" in result
        assert len(result) < 10000


class TestMemoryToolSchema:
    """Test tool schema generation for OpenAI compatibility."""

    def test_read_schema(self):
        p = SQLiteMemoryProvider(":memory:")
        tool = MemoryReadTool(p, "ws")
        schema = tool.to_openai_schema()
        assert schema["type"] == "function"
        assert schema["function"]["name"] == "memory_read"
        params = schema["function"]["parameters"]
        assert "action" in params["properties"]
        assert params["properties"]["action"]["enum"] == ["get", "list"]

    def test_write_schema(self):
        p = SQLiteMemoryProvider(":memory:")
        tool = MemoryWriteTool(p, "ws")
        schema = tool.to_openai_schema()
        assert schema["type"] == "function"
        assert schema["function"]["name"] == "memory_write"
        params = schema["function"]["parameters"]
        assert "action" in params["properties"]
        assert params["properties"]["action"]["enum"] == ["set", "delete"]
        assert "key" in params["required"]
