"""Tests for the MCP client module.

Covers: StderrRingBuffer, EpochGate, MCPClientTool, MCPClientManager,
schema drift, concurrency invariants, and config parsing.
"""

import asyncio
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from workbench.config import MCPServerConfig, load_config
from workbench.mcp.client import (
    EpochGate,
    MCPClientManager,
    MCPClientTool,
    StderrRingBuffer,
)
from workbench.tools.base import ToolRisk
from workbench.tools.registry import ToolRegistry
from workbench.types import ToolResult


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _mock_mcp_tool(name: str, description: str = "", schema: dict | None = None):
    """Build a mock MCP tool object (like mcp.types.Tool)."""
    t = MagicMock()
    t.name = name
    t.description = description or f"Tool {name}"
    t.inputSchema = schema or {"type": "object", "properties": {}}
    return t


def _mock_session(tools=None):
    """Build a mock ClientSession with configurable list_tools response."""
    session = AsyncMock()
    session.initialize = AsyncMock()
    session.send_ping = AsyncMock()

    if tools is None:
        tools = []

    # Build mock ListToolsResult
    mock_result = MagicMock()
    mock_tools = []
    for name, desc, schema in tools:
        mock_tools.append(_mock_mcp_tool(name, desc, schema))
    mock_result.tools = mock_tools
    session.list_tools = AsyncMock(return_value=mock_result)

    # Default call_tool returns success
    call_result = MagicMock()
    call_result.content = [MagicMock(type="text", text="ok")]
    call_result.content[0].text = "ok"
    call_result.isError = False
    session.call_tool = AsyncMock(return_value=call_result)

    return session


def _make_manager_with_gate(
    server_name: str = "testserver",
    gate: EpochGate | None = None,
    session: AsyncMock | None = None,
    cfg: MCPServerConfig | None = None,
) -> MCPClientManager:
    """Build an MCPClientManager with pre-injected state for tool execution tests."""
    mgr = MCPClientManager()
    if cfg is None:
        cfg = MCPServerConfig(
            name=server_name,
            timeout=30.0,
            acquire_timeout=2.0,
            call_concurrency=1,
        )
    mgr._server_configs[server_name] = cfg
    if gate is not None:
        mgr._gates[server_name] = gate
    if session is not None:
        mgr._sessions[server_name] = session
    return mgr


# ---------------------------------------------------------------------------
# StderrRingBuffer tests
# ---------------------------------------------------------------------------

class TestStderrRingBuffer:

    def test_ring_buffer_caps_at_maxlen(self):
        """Add 300 lines to a buffer of size 200, verify only last 200 remain."""
        buf = StderrRingBuffer(maxlen=200)
        for i in range(300):
            buf.append(f"line-{i}")
        all_lines = buf.tail()
        assert len(all_lines) == 200
        assert all_lines[0] == "line-100"
        assert all_lines[-1] == "line-299"

    def test_ring_buffer_truncates_long_lines(self):
        """Line exceeding line_max_chars gets truncated."""
        buf = StderrRingBuffer(maxlen=10, line_max_chars=50)
        long_line = "x" * 100
        buf.append(long_line)
        stored = buf.tail(1)[0]
        assert len(stored) < 100
        assert stored.endswith("...(truncated)")
        # First 50 chars preserved
        assert stored.startswith("x" * 50)

    def test_ring_buffer_tail(self):
        """tail(5) returns last 5 lines."""
        buf = StderrRingBuffer(maxlen=200)
        for i in range(20):
            buf.append(f"line-{i}")
        last5 = buf.tail(5)
        assert len(last5) == 5
        assert last5 == [f"line-{i}" for i in range(15, 20)]

    def test_ring_buffer_last_at(self):
        """last_at starts None, updates on append."""
        buf = StderrRingBuffer(maxlen=10)
        assert buf.last_at is None
        buf.append("hello")
        assert isinstance(buf.last_at, datetime)
        assert buf.last_at.tzinfo == timezone.utc


# ---------------------------------------------------------------------------
# EpochGate tests
# ---------------------------------------------------------------------------

class TestEpochGate:

    async def test_gate_acquire_release(self):
        """Basic acquire/release cycle."""
        sem = asyncio.Semaphore(1)
        gate = EpochGate("epoch-1", sem, max_permits=1)
        assert await gate.acquire(timeout=1.0) is True
        gate.release()
        # Can acquire again after release
        assert await gate.acquire(timeout=1.0) is True
        gate.release()

    async def test_gate_close_wakes_waiters(self):
        """Start a task waiting to acquire, close the gate, verify waiter gets False."""
        sem = asyncio.Semaphore(1)
        gate = EpochGate("epoch-1", sem, max_permits=1)

        # Exhaust the semaphore
        assert await gate.acquire(timeout=1.0) is True

        result_holder = []

        async def waiter():
            got = await gate.acquire(timeout=10.0)
            result_holder.append(got)

        task = asyncio.create_task(waiter())
        # Let the waiter start blocking
        await asyncio.sleep(0.05)

        gate.close()
        await asyncio.wait_for(task, timeout=2.0)

        assert result_holder == [False]

    async def test_gate_acquire_after_close_returns_false(self):
        """Close first, then try acquire — returns False immediately."""
        sem = asyncio.Semaphore(1)
        gate = EpochGate("epoch-1", sem, max_permits=1)
        gate.close()
        assert await gate.acquire(timeout=1.0) is False

    async def test_gate_timeout(self):
        """Acquire with very short timeout on saturated semaphore returns False."""
        sem = asyncio.Semaphore(1)
        gate = EpochGate("epoch-1", sem, max_permits=1)
        # Exhaust permit
        assert await gate.acquire(timeout=1.0) is True
        # Second acquire should time out
        assert await gate.acquire(timeout=0.01) is False


# ---------------------------------------------------------------------------
# MCPClientTool tests
# ---------------------------------------------------------------------------

class TestMCPClientTool:

    def test_tool_name_namespacing(self):
        """Name is server__toolname."""
        mcp_tool = _mock_mcp_tool("search")
        mgr = MCPClientManager()
        tool = MCPClientTool("myserver", mcp_tool, ToolRisk.READ_ONLY, mgr)
        assert tool.name == "myserver__search"

    def test_tool_properties(self):
        """description, parameters, risk_level from constructor."""
        schema = {"type": "object", "properties": {"q": {"type": "string"}}}
        mcp_tool = _mock_mcp_tool("lookup", description="Find stuff", schema=schema)
        mgr = MCPClientManager()
        tool = MCPClientTool("srv", mcp_tool, ToolRisk.WRITE, mgr)
        assert tool.description == "Find stuff"
        assert tool.risk_level == ToolRisk.WRITE
        assert "q" in tool.parameters.get("properties", {})

    async def test_execute_success(self):
        """Mock session returns TextContent, verify ToolResult."""
        session = _mock_session()
        sem = asyncio.Semaphore(1)
        gate = EpochGate("e1", sem, max_permits=1)
        mgr = _make_manager_with_gate("srv", gate=gate, session=session)

        mcp_tool = _mock_mcp_tool("echo")
        tool = MCPClientTool("srv", mcp_tool, ToolRisk.READ_ONLY, mgr)

        result = await tool.execute(text="hello")
        assert isinstance(result, ToolResult)
        assert result.success is True
        assert result.content == "ok"
        session.call_tool.assert_awaited_once_with("echo", {"text": "hello"})

    async def test_execute_disconnected(self):
        """Gate is None — returns error_code='disconnected'."""
        mgr = _make_manager_with_gate("srv", gate=None, session=None)
        mcp_tool = _mock_mcp_tool("echo")
        tool = MCPClientTool("srv", mcp_tool, ToolRisk.READ_ONLY, mgr)

        result = await tool.execute()
        assert result.success is False
        assert result.error_code == "disconnected"

    async def test_execute_busy(self):
        """Gate acquire times out — returns error_code='busy' with retry_after_ms."""
        sem = asyncio.Semaphore(1)
        gate = EpochGate("e1", sem, max_permits=1)
        # Exhaust the permit so next acquire will time out
        await gate.acquire(timeout=1.0)

        cfg = MCPServerConfig(name="srv", acquire_timeout=0.01, call_concurrency=1)
        mgr = _make_manager_with_gate("srv", gate=gate, session=_mock_session(), cfg=cfg)

        mcp_tool = _mock_mcp_tool("echo")
        tool = MCPClientTool("srv", mcp_tool, ToolRisk.READ_ONLY, mgr)

        result = await tool.execute()
        assert result.success is False
        assert result.error_code == "busy"
        assert result.metadata.get("retry_after_ms") == 500

    async def test_execute_timeout(self):
        """session.call_tool raises TimeoutError — returns error_code='timeout'."""
        session = _mock_session()
        session.call_tool = AsyncMock(side_effect=asyncio.TimeoutError())
        sem = asyncio.Semaphore(1)
        gate = EpochGate("e1", sem, max_permits=1)
        mgr = _make_manager_with_gate("srv", gate=gate, session=session)

        mcp_tool = _mock_mcp_tool("slow")
        tool = MCPClientTool("srv", mcp_tool, ToolRisk.READ_ONLY, mgr)

        result = await tool.execute()
        assert result.success is False
        assert result.error_code == "timeout"

    async def test_execute_remote_error(self):
        """CallToolResult has isError=True — returns error_code='remote_error'."""
        session = _mock_session()
        error_result = MagicMock()
        error_content = MagicMock()
        error_content.text = "tool failed"
        error_result.content = [error_content]
        error_result.isError = True
        session.call_tool = AsyncMock(return_value=error_result)

        sem = asyncio.Semaphore(1)
        gate = EpochGate("e1", sem, max_permits=1)
        mgr = _make_manager_with_gate("srv", gate=gate, session=session)

        mcp_tool = _mock_mcp_tool("bad")
        tool = MCPClientTool("srv", mcp_tool, ToolRisk.READ_ONLY, mgr)

        result = await tool.execute()
        assert result.success is False
        assert result.error_code == "remote_error"
        assert "tool failed" in result.content

    async def test_execute_exception(self):
        """session.call_tool raises generic Exception — returns error_code='exception'."""
        session = _mock_session()
        session.call_tool = AsyncMock(side_effect=RuntimeError("kaboom"))
        sem = asyncio.Semaphore(1)
        gate = EpochGate("e1", sem, max_permits=1)
        mgr = _make_manager_with_gate("srv", gate=gate, session=session)

        mcp_tool = _mock_mcp_tool("broken")
        tool = MCPClientTool("srv", mcp_tool, ToolRisk.READ_ONLY, mgr)

        result = await tool.execute()
        assert result.success is False
        assert result.error_code == "exception"
        assert "kaboom" in result.error


# ---------------------------------------------------------------------------
# MCPClientManager tests
# ---------------------------------------------------------------------------

class TestMCPClientManager:

    async def test_start_registers_tools(self):
        """Mock _server_loop to simulate tool registration, verify in registry."""
        registry = ToolRegistry()
        mgr = MCPClientManager()

        cfg = MCPServerConfig(name="test", command="echo", risk_level="READ_ONLY")

        async def fake_server_loop(config):
            # Simulate what _register_tools does
            session = _mock_session(tools=[
                ("search", "Search things", None),
                ("fetch", "Fetch things", None),
            ])
            result = await session.list_tools()
            risk = ToolRisk[config.risk_level]
            for t in result.tools:
                wb_tool = MCPClientTool(config.name, t, risk, mgr)
                registry.register(wb_tool, overwrite=True)
                mgr._tool_ownership[wb_tool.name] = config.name
            mgr._known_tools_by_server[config.name] = {
                f"{config.name}__{t.name}" for t in result.tools
            }
            mgr._status[config.name] = "connected"

        with patch.object(mgr, "_server_loop", side_effect=fake_server_loop):
            await mgr.start(registry, [cfg])

        # Wait for task to finish
        await asyncio.sleep(0.6)

        assert registry.get("test__search") is not None
        assert registry.get("test__fetch") is not None

    async def test_stop_unregisters_tools(self):
        """After stop, tools are removed from registry."""
        registry = ToolRegistry()
        mgr = MCPClientManager()
        mgr._registry = registry

        # Pre-register tools as if a server loop had done it
        mcp_tool = _mock_mcp_tool("search")
        wb_tool = MCPClientTool("srv", mcp_tool, ToolRisk.READ_ONLY, mgr)
        registry.register(wb_tool)
        mgr._tool_ownership["srv__search"] = "srv"
        mgr._known_tools_by_server["srv"] = {"srv__search"}
        mgr._server_configs["srv"] = MCPServerConfig(name="srv")

        await mgr.stop()

        assert registry.get("srv__search") is None

    async def test_tools_changed_callback(self):
        """Register a callback, verify it's called with correct payload."""
        mgr = MCPClientManager()
        mgr._registry = ToolRegistry()

        payloads = []
        mgr.on_tools_changed(lambda p: payloads.append(p))

        mgr._emit_tools_changed(
            server="srv",
            status="connected",
            epoch="ep-1",
            tools_added=["srv__search"],
            tools_removed=[],
            tools_total=1,
        )

        assert len(payloads) == 1
        p = payloads[0]
        assert p["server"] == "srv"
        assert p["status"] == "connected"
        assert p["epoch"] == "ep-1"
        assert p["tools_added"] == ["srv__search"]
        assert p["tools_removed"] == []
        assert p["tools_total"] == 1

    async def test_server_status(self):
        """Verify observability snapshot structure."""
        mgr = MCPClientManager()
        cfg = MCPServerConfig(name="srv")
        mgr._server_configs["srv"] = cfg
        mgr._status["srv"] = "connected"
        mgr._epochs["srv"] = "epoch-42"
        mgr._stderr_buffers["srv"] = StderrRingBuffer(maxlen=10)
        mgr._known_tools_by_server["srv"] = {"srv__a", "srv__b"}

        status = mgr.server_status()
        assert "srv" in status
        s = status["srv"]
        assert s["status"] == "connected"
        assert s["tools_count"] == 2
        assert s["epoch"] == "epoch-42"
        assert isinstance(s["stderr_tail"], list)
        assert s["last_stderr_at"] is None  # No stderr yet

    async def test_reconfigure_raises(self):
        """reconfigure() raises NotImplementedError."""
        mgr = MCPClientManager()
        with pytest.raises(NotImplementedError, match="Hot reconfigure"):
            await mgr.reconfigure(ToolRegistry(), [])


# ---------------------------------------------------------------------------
# Schema drift tests
# ---------------------------------------------------------------------------

class TestSchemaDrift:

    async def test_schema_drift_removal(self):
        """First list_tools returns A,B. Second returns A only. B is unregistered."""
        registry = ToolRegistry()
        mgr = MCPClientManager()
        mgr._registry = registry

        cfg = MCPServerConfig(name="srv", risk_level="READ_ONLY", timeout=30.0)
        mgr._server_configs["srv"] = cfg

        # --- First registration: tools A and B ---
        session1 = _mock_session(tools=[
            ("tool_a", "Tool A", None),
            ("tool_b", "Tool B", None),
        ])
        gate1 = EpochGate("e1", asyncio.Semaphore(1), 1)
        await mgr._register_tools(cfg, session1, "e1", gate1)

        assert registry.get("srv__tool_a") is not None
        assert registry.get("srv__tool_b") is not None
        assert mgr._known_tools_by_server["srv"] == {"srv__tool_a", "srv__tool_b"}

        # --- Second registration: only tool A ---
        session2 = _mock_session(tools=[
            ("tool_a", "Tool A", None),
        ])
        gate2 = EpochGate("e2", asyncio.Semaphore(1), 1)
        await mgr._register_tools(cfg, session2, "e2", gate2)

        assert registry.get("srv__tool_a") is not None
        assert registry.get("srv__tool_b") is None  # Removed by drift detection
        assert mgr._known_tools_by_server["srv"] == {"srv__tool_a"}


# ---------------------------------------------------------------------------
# Concurrency tests
# ---------------------------------------------------------------------------

class TestConcurrency:

    async def test_semaphore_waiters_released_on_disconnect(self):
        """One call holding permit, second waiting. Close gate. Second returns error."""
        sem = asyncio.Semaphore(1)
        gate = EpochGate("e1", sem, max_permits=1)

        session = _mock_session()
        # Make call_tool block until we release it
        block_event = asyncio.Event()
        call_count = 0

        async def blocking_call_tool(name, args):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                await block_event.wait()
            result = MagicMock()
            result.content = [MagicMock(text="ok")]
            result.isError = False
            return result

        session.call_tool = AsyncMock(side_effect=blocking_call_tool)

        cfg = MCPServerConfig(name="srv", acquire_timeout=5.0, call_concurrency=1)
        mgr = _make_manager_with_gate("srv", gate=gate, session=session, cfg=cfg)

        mcp_tool = _mock_mcp_tool("work")
        tool = MCPClientTool("srv", mcp_tool, ToolRisk.READ_ONLY, mgr)

        results = []

        async def call1():
            r = await tool.execute()
            results.append(("call1", r))

        async def call2():
            # Small delay so call1 grabs the permit first
            await asyncio.sleep(0.05)
            r = await tool.execute()
            results.append(("call2", r))

        t1 = asyncio.create_task(call1())
        t2 = asyncio.create_task(call2())

        # Let call1 acquire and call2 start waiting
        await asyncio.sleep(0.1)

        # Close gate to release waiters
        gate.close()
        block_event.set()  # Let call1 finish too

        await asyncio.wait_for(asyncio.gather(t1, t2), timeout=3.0)

        # call2 should have gotten disconnected or busy (gate closed while waiting)
        call2_results = [r for tag, r in results if tag == "call2"]
        assert len(call2_results) == 1
        assert call2_results[0].success is False
        assert call2_results[0].error_code in ("disconnected", "busy")

    async def test_busy_serialization_invariant(self):
        """Two concurrent calls with concurrency=1. call1 blocks, call2 must wait."""
        sem = asyncio.Semaphore(1)
        gate = EpochGate("e1", sem, max_permits=1)

        session = _mock_session()
        order = []
        call1_entered = asyncio.Event()
        call1_release = asyncio.Event()

        async def ordered_call_tool(name, args):
            call_id = args.get("id", "unknown")
            if call_id == "first":
                order.append("call1_start")
                call1_entered.set()
                await call1_release.wait()
                order.append("call1_end")
            else:
                order.append("call2_start")
                order.append("call2_end")
            result = MagicMock()
            result.content = [MagicMock(text=f"done-{call_id}")]
            result.isError = False
            return result

        session.call_tool = AsyncMock(side_effect=ordered_call_tool)

        cfg = MCPServerConfig(
            name="srv",
            acquire_timeout=5.0,
            call_concurrency=1,
            timeout=30.0,
        )
        mgr = _make_manager_with_gate("srv", gate=gate, session=session, cfg=cfg)

        mcp_tool = _mock_mcp_tool("work")
        tool = MCPClientTool("srv", mcp_tool, ToolRisk.READ_ONLY, mgr)

        async def do_call1():
            return await tool.execute(id="first")

        async def do_call2():
            # Wait for call1 to enter
            await call1_entered.wait()
            return await tool.execute(id="second")

        t1 = asyncio.create_task(do_call1())
        t2 = asyncio.create_task(do_call2())

        # Wait for call1 to be inside call_tool
        await call1_entered.wait()
        await asyncio.sleep(0.05)

        # call2 should be blocked on acquire — hasn't started yet
        assert "call2_start" not in order

        # Release call1
        call1_release.set()

        r1, r2 = await asyncio.wait_for(asyncio.gather(t1, t2), timeout=5.0)

        assert r1.success is True
        assert r2.success is True
        # call1 must complete before call2 starts
        assert order.index("call1_end") < order.index("call2_start")


# ---------------------------------------------------------------------------
# Config tests
# ---------------------------------------------------------------------------

class TestConfig:

    def test_mcp_server_config_defaults(self):
        """Verify default values."""
        cfg = MCPServerConfig()
        assert cfg.name == ""
        assert cfg.transport == "stdio"
        assert cfg.command == ""
        assert cfg.args == []
        assert cfg.env == {}
        assert cfg.url == ""
        assert cfg.headers == {}
        assert cfg.risk_level == "READ_ONLY"
        assert cfg.timeout == 30.0
        assert cfg.call_concurrency == 1
        assert cfg.acquire_timeout == 2.0
        assert cfg.kill_grace_seconds == 1.0
        assert cfg.kill_force_seconds == 1.0
        assert cfg.stderr_lines_max == 200
        assert cfg.stderr_log_level == "DEBUG"
        assert cfg.stderr_rate_limit_per_sec == 50
        assert cfg.stderr_line_max_chars == 2000
        assert cfg.ping_interval_seconds == 15.0
        assert cfg.ping_timeout_seconds == 5.0
        assert cfg.stable_reset_seconds == 60.0
        assert cfg.backoff_initial == 1.0
        assert cfg.backoff_max == 60.0

    def test_config_loads_mcp_clients(self, tmp_path):
        """Create a YAML with mcp_clients.servers, load_config, verify MCPServerConfig parsed."""
        yaml_content = """\
mcp_clients:
  servers:
    - name: test-server
      transport: stdio
      command: node
      args: ["server.js"]
      risk_level: WRITE
      timeout: 45.0
      call_concurrency: 3
    - name: sse-server
      transport: sse
      url: https://example.com/mcp
      headers:
        Authorization: "Bearer token123"
      risk_level: READ_ONLY
"""
        config_file = tmp_path / "workbench.yaml"
        config_file.write_text(yaml_content)

        cfg = load_config(config_path=str(config_file))

        assert len(cfg.mcp_clients.servers) == 2

        s1 = cfg.mcp_clients.servers[0]
        assert s1.name == "test-server"
        assert s1.transport == "stdio"
        assert s1.command == "node"
        assert s1.args == ["server.js"]
        assert s1.risk_level == "WRITE"
        assert s1.timeout == 45.0
        assert s1.call_concurrency == 3

        s2 = cfg.mcp_clients.servers[1]
        assert s2.name == "sse-server"
        assert s2.transport == "sse"
        assert s2.url == "https://example.com/mcp"
        assert s2.headers == {"Authorization": "Bearer token123"}
        assert s2.risk_level == "READ_ONLY"
