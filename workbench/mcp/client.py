"""MCP client — connect to remote MCP servers and expose their tools locally.

Each configured MCP server gets a persistent background task that manages
connection, reconnection with backoff, health checks, and tool registration.
Remote tools are wrapped as local Tool subclasses and registered in the
workbench ToolRegistry with namespaced names (server__tool).

Spec reference: docs/MCP_CLIENT_SPEC_v2.2.md
"""

from __future__ import annotations

import asyncio
import logging
import os
import random
import time
from collections import deque
from datetime import datetime, timezone
from typing import Any, Callable
from uuid import uuid4

from mcp import ClientSession
from mcp.client.stdio import stdio_client, StdioServerParameters
from mcp.client.sse import sse_client

from workbench.config import MCPServerConfig
from workbench.tools.base import Tool, ToolRisk, normalize_schema
from workbench.tools.registry import ToolRegistry
from workbench.types import ToolResult

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# StderrRingBuffer
# ---------------------------------------------------------------------------

class StderrRingBuffer:
    """Fixed-size ring buffer for capturing MCP server stderr output."""

    def __init__(
        self,
        maxlen: int = 200,
        line_max_chars: int = 2000,
        rate_limit_per_sec: int = 50,
        log_level: str = "DEBUG",
    ):
        self._buf: deque[str] = deque(maxlen=maxlen)
        self._line_max_chars = line_max_chars
        self._rate_limit = rate_limit_per_sec
        self._log_level = getattr(logging, log_level.upper(), logging.DEBUG)
        self._last_at: datetime | None = None
        # Rate limiting state
        self._window_start: float = 0.0
        self._window_count: int = 0

    def append(self, line: str) -> None:
        """Add a line to the buffer, truncating if needed."""
        if len(line) > self._line_max_chars:
            line = line[: self._line_max_chars] + "...(truncated)"
        self._buf.append(line)
        self._last_at = datetime.now(timezone.utc)

        # Rate-limited logging
        now = time.monotonic()
        if now - self._window_start >= 1.0:
            self._window_start = now
            self._window_count = 0
        self._window_count += 1
        if self._window_count <= self._rate_limit:
            logger.log(self._log_level, "stderr: %s", line.rstrip())

    def tail(self, n: int | None = None) -> list[str]:
        """Return the last N lines (or all buffered lines)."""
        if n is None:
            return list(self._buf)
        return list(self._buf)[-n:]

    @property
    def last_at(self) -> datetime | None:
        return self._last_at


# ---------------------------------------------------------------------------
# EpochGate
# ---------------------------------------------------------------------------

class EpochGate:
    """Epoch-scoped execution gate wrapping an asyncio.Semaphore.

    When a server disconnects, the gate is closed — all waiters are woken
    and new acquires fail immediately.
    """

    def __init__(self, epoch_id: str, semaphore: asyncio.Semaphore, max_permits: int):
        self.epoch_id = epoch_id
        self.closed = False
        self._sem = semaphore
        self._max_permits = max_permits

    async def acquire(self, timeout: float) -> bool:
        """Acquire the semaphore with timeout. Returns False if closed or timed out."""
        if self.closed:
            return False
        try:
            await asyncio.wait_for(self._sem.acquire(), timeout=timeout)
        except asyncio.TimeoutError:
            return False
        # Re-check after waking — gate may have been closed while waiting
        if self.closed:
            self._sem.release()
            return False
        return True

    def release(self) -> None:
        """Release the semaphore."""
        self._sem.release()

    def close(self) -> None:
        """Mark gate as closed and wake all waiters."""
        self.closed = True
        # Release N times to unblock any waiters
        for _ in range(self._max_permits):
            try:
                self._sem.release()
            except ValueError:
                pass


# ---------------------------------------------------------------------------
# MCPClientTool
# ---------------------------------------------------------------------------

class MCPClientTool(Tool):
    """Wraps a single remote MCP tool as a local workbench Tool."""

    def __init__(
        self,
        server_name: str,
        mcp_tool: Any,
        risk: ToolRisk,
        manager: MCPClientManager,
    ):
        self._server_name = server_name
        self._mcp_tool = mcp_tool
        self._risk = risk
        self._manager = manager
        # Cache the original (un-namespaced) tool name for call_tool
        self._original_name: str = mcp_tool.name

    @property
    def name(self) -> str:
        return f"{self._server_name}__{self._mcp_tool.name}"

    @property
    def description(self) -> str:
        return self._mcp_tool.description or ""

    @property
    def parameters(self) -> dict:
        schema = self._mcp_tool.inputSchema or {}
        return normalize_schema(schema)

    @property
    def risk_level(self) -> ToolRisk:
        return self._risk

    async def execute(self, **kwargs) -> ToolResult:
        gate = self._manager.get_gate(self._server_name)

        # Check gate availability
        if gate is None or gate.closed:
            return ToolResult(
                success=False,
                content="",
                error="Server disconnected",
                error_code="disconnected",
                metadata={"retry_after_ms": 2000},
            )

        # Try to acquire execution permit
        cfg = self._manager._server_configs.get(self._server_name)
        acquire_timeout = cfg.acquire_timeout if cfg else 2.0
        call_timeout = cfg.timeout if cfg else 30.0

        acquired = await gate.acquire(timeout=acquire_timeout)
        if not acquired:
            if gate.closed:
                return ToolResult(
                    success=False,
                    content="",
                    error="Server disconnected",
                    error_code="disconnected",
                    metadata={"retry_after_ms": 2000},
                )
            return ToolResult(
                success=False,
                content="",
                error="Server busy — all execution permits in use",
                error_code="busy",
                metadata={"retry_after_ms": 500},
            )

        try:
            session = self._manager.get_session(self._server_name)
            if session is None:
                return ToolResult(
                    success=False,
                    content="",
                    error="Server disconnected",
                    error_code="disconnected",
                    metadata={"retry_after_ms": 2000},
                )

            result = await asyncio.wait_for(
                session.call_tool(self._original_name, kwargs),
                timeout=call_timeout,
            )

            # Map CallToolResult to ToolResult
            text_parts: list[str] = []
            metadata: dict[str, Any] = {}
            image_list: list[dict[str, str]] = []

            for item in result.content:
                if hasattr(item, "text"):
                    text_parts.append(item.text)
                elif hasattr(item, "data") and hasattr(item, "mimeType"):
                    # ImageContent — base64 data
                    image_list.append({
                        "data": item.data,
                        "mimeType": item.mimeType,
                    })

            if image_list:
                metadata["images"] = image_list

            content = "\n".join(text_parts)

            if result.isError:
                return ToolResult(
                    success=False,
                    content=content,
                    error=content or "Remote tool error",
                    error_code="remote_error",
                    metadata=metadata,
                )

            return ToolResult(
                success=True,
                content=content,
                metadata=metadata,
            )

        except asyncio.TimeoutError:
            return ToolResult(
                success=False,
                content="",
                error=f"Tool call timed out after {call_timeout}s",
                error_code="timeout",
            )
        except Exception as exc:
            logger.exception(
                "Exception calling MCP tool %s on server %s",
                self._original_name,
                self._server_name,
            )
            return ToolResult(
                success=False,
                content="",
                error=str(exc),
                error_code="exception",
            )
        finally:
            if not gate.closed:
                gate.release()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _resolve_env(cfg: MCPServerConfig) -> dict[str, str] | None:
    """Resolve environment variables for a server config.

    Values starting with '$' are looked up in os.environ.
    Returns None if any required variable is missing.
    """
    resolved: dict[str, str] = {}
    for key, value in cfg.env.items():
        if isinstance(value, str) and value.startswith("$"):
            env_name = value[1:]
            env_val = os.environ.get(env_name)
            if env_val is None:
                logger.error(
                    "MCP server %s: required env var %s (from key %s) not found",
                    cfg.name, env_name, key,
                )
                return None
            resolved[key] = env_val
        else:
            resolved[key] = value
    return resolved


def _resolve_headers(cfg: MCPServerConfig) -> dict[str, str] | None:
    """Resolve header values that reference environment variables."""
    resolved: dict[str, str] = {}
    for key, value in cfg.headers.items():
        if isinstance(value, str) and value.startswith("$"):
            env_name = value[1:]
            env_val = os.environ.get(env_name)
            if env_val is None:
                logger.error(
                    "MCP server %s: required env var %s (for header %s) not found",
                    cfg.name, env_name, key,
                )
                return None
            resolved[key] = env_val
        else:
            resolved[key] = value
    return resolved


# ---------------------------------------------------------------------------
# MCPClientManager
# ---------------------------------------------------------------------------

class MCPClientManager:
    """Manages connections to one or more remote MCP servers.

    Each server gets a background asyncio.Task that handles connection,
    reconnection with exponential backoff, health checks, and tool
    registration/unregistration in the workbench ToolRegistry.
    """

    def __init__(self) -> None:
        self._tasks: dict[str, asyncio.Task] = {}
        self._sessions: dict[str, ClientSession] = {}
        self._gates: dict[str, EpochGate] = {}
        self._status: dict[str, str] = {}
        self._epochs: dict[str, str] = {}
        self._stderr_buffers: dict[str, StderrRingBuffer] = {}
        self._known_tools_by_server: dict[str, set[str]] = {}
        self._tool_ownership: dict[str, str] = {}  # namespaced_name -> server_name
        self._server_configs: dict[str, MCPServerConfig] = {}
        self._stop_event: asyncio.Event = asyncio.Event()
        self._callbacks: list[Callable] = []
        self._registry: ToolRegistry | None = None

    # -- Public API ---------------------------------------------------------

    async def start(
        self, registry: ToolRegistry, servers: list[MCPServerConfig]
    ) -> None:
        """Start background tasks for all configured MCP servers."""
        self._registry = registry
        self._stop_event.clear()

        for cfg in servers:
            if not cfg.name:
                logger.warning("Skipping MCP server config with empty name")
                continue
            self._server_configs[cfg.name] = cfg
            task = asyncio.create_task(
                self._server_loop(cfg), name=f"mcp-client-{cfg.name}"
            )
            self._tasks[cfg.name] = task
            logger.info("Started MCP client task for server: %s", cfg.name)

        # Brief grace period for stdio servers to register tools
        if servers:
            await asyncio.sleep(0.5)

    async def stop(self) -> None:
        """Stop all server tasks and clean up."""
        self._stop_event.set()
        shutdown_timeout = 5.0

        if self._tasks:
            tasks = list(self._tasks.values())
            done, pending = await asyncio.wait(tasks, timeout=shutdown_timeout)

            for task in pending:
                task.cancel()
                try:
                    await task
                except (asyncio.CancelledError, Exception):
                    pass

        # Unregister all owned tools
        if self._registry is not None:
            for tool_name in list(self._tool_ownership.keys()):
                self._registry.unregister(tool_name)

        # Close all gates
        for gate in self._gates.values():
            gate.close()

        # Clear state
        self._tasks.clear()
        self._sessions.clear()
        self._gates.clear()
        self._status.clear()
        self._epochs.clear()
        self._stderr_buffers.clear()
        self._known_tools_by_server.clear()
        self._tool_ownership.clear()
        self._server_configs.clear()

    def on_tools_changed(self, callback: Callable) -> None:
        """Register a callback invoked when tools are added/removed/status changes."""
        self._callbacks.append(callback)

    def _emit_tools_changed(
        self,
        server: str,
        status: str,
        epoch: str,
        tools_added: list[str],
        tools_removed: list[str],
        tools_total: int,
    ) -> None:
        """Notify all registered callbacks of a tools change event."""
        payload = {
            "server": server,
            "status": status,
            "epoch": epoch,
            "tools_added": tools_added,
            "tools_removed": tools_removed,
            "tools_total": tools_total,
        }
        for cb in self._callbacks:
            try:
                cb(payload)
            except Exception:
                logger.exception("tools_changed callback error")

    def get_session(self, server_name: str) -> ClientSession | None:
        """Return the active session for a server, or None."""
        return self._sessions.get(server_name)

    def get_gate(self, server_name: str) -> EpochGate | None:
        """Return the current epoch gate for a server, or None."""
        return self._gates.get(server_name)

    def server_status(self) -> dict[str, dict]:
        """Return observability snapshot for all servers."""
        result: dict[str, dict] = {}
        for name in self._server_configs:
            stderr_buf = self._stderr_buffers.get(name)
            known = self._known_tools_by_server.get(name, set())
            result[name] = {
                "status": self._status.get(name, "stopped"),
                "tools_count": len(known),
                "stderr_tail": stderr_buf.tail(20) if stderr_buf else [],
                "last_stderr_at": (
                    stderr_buf.last_at.isoformat() if stderr_buf and stderr_buf.last_at else None
                ),
                "epoch": self._epochs.get(name),
            }
        return result

    async def reconfigure(
        self, registry: ToolRegistry, servers: list[MCPServerConfig]
    ) -> None:
        """Placeholder for hot reconfigure — not supported in v2.2."""
        raise NotImplementedError(
            "Hot reconfigure not supported in v2.2. Restart required."
        )

    # -- Server loop --------------------------------------------------------

    async def _server_loop(self, cfg: MCPServerConfig) -> None:
        """Main per-server reconnect loop (spec section 11)."""

        # Step 1: Resolve secrets
        resolved_env = _resolve_env(cfg)
        if resolved_env is None:
            self._status[cfg.name] = "misconfigured"
            self._emit_tools_changed(
                cfg.name, "misconfigured", "", [], [], 0
            )
            # Retry after 30s in case env vars become available
            while not self._stop_event.is_set():
                await _interruptible_sleep(self._stop_event, 30.0)
                resolved_env = _resolve_env(cfg)
                if resolved_env is not None:
                    break
            if self._stop_event.is_set():
                return

        resolved_headers: dict[str, str] | None = None
        if cfg.transport == "sse":
            resolved_headers = _resolve_headers(cfg)
            if resolved_headers is None:
                self._status[cfg.name] = "misconfigured"
                self._emit_tools_changed(
                    cfg.name, "misconfigured", "", [], [], 0
                )
                while not self._stop_event.is_set():
                    await _interruptible_sleep(self._stop_event, 30.0)
                    resolved_headers = _resolve_headers(cfg)
                    if resolved_headers is not None:
                        break
                if self._stop_event.is_set():
                    return

        # Initialize stderr buffer
        self._stderr_buffers[cfg.name] = StderrRingBuffer(
            maxlen=cfg.stderr_lines_max,
            line_max_chars=cfg.stderr_line_max_chars,
            rate_limit_per_sec=cfg.stderr_rate_limit_per_sec,
            log_level=cfg.stderr_log_level,
        )

        # Step 2: Backoff state
        delay = cfg.backoff_initial
        stable_since: float | None = None

        # Step 3: Main loop
        while not self._stop_event.is_set():
            epoch_id = str(uuid4())
            gate = EpochGate(
                epoch_id,
                asyncio.Semaphore(cfg.call_concurrency),
                cfg.call_concurrency,
            )
            self._gates[cfg.name] = gate

            try:
                if cfg.transport == "stdio":
                    await self._run_stdio_epoch(
                        cfg, epoch_id, gate, resolved_env or {}
                    )
                elif cfg.transport == "sse":
                    await self._run_sse_epoch(
                        cfg, epoch_id, gate, resolved_headers or {}
                    )
                else:
                    logger.error(
                        "MCP server %s: unknown transport %s",
                        cfg.name, cfg.transport,
                    )
                    self._status[cfg.name] = "misconfigured"
                    self._emit_tools_changed(
                        cfg.name, "misconfigured", epoch_id, [], [], 0
                    )
                    return

                # If we get here cleanly, connection was stable
                # Reset backoff since we had a clean run
                delay = cfg.backoff_initial
                stable_since = None

            except asyncio.CancelledError:
                gate.close()
                raise

            except Exception as exc:
                logger.warning(
                    "MCP server %s disconnected: %s", cfg.name, exc
                )

                # Mark degraded — tools stay registered
                self._status[cfg.name] = "degraded"
                gate.close()
                self._sessions.pop(cfg.name, None)

                self._emit_tools_changed(
                    cfg.name,
                    "degraded",
                    epoch_id,
                    [],
                    [],
                    len(self._known_tools_by_server.get(cfg.name, set())),
                )

                if self._stop_event.is_set():
                    return

                # Check if we were stable long enough to reset backoff
                if stable_since is not None:
                    elapsed = time.monotonic() - stable_since
                    if elapsed >= cfg.stable_reset_seconds:
                        delay = cfg.backoff_initial

                # Backoff sleep with jitter
                jittered = delay * (0.5 + random.random())
                logger.info(
                    "MCP server %s: reconnecting in %.1fs", cfg.name, jittered
                )
                await _interruptible_sleep(self._stop_event, jittered)

                # Increase delay for next time (capped)
                delay = min(delay * 2, cfg.backoff_max)

    async def _run_stdio_epoch(
        self,
        cfg: MCPServerConfig,
        epoch_id: str,
        gate: EpochGate,
        env: dict[str, str],
    ) -> None:
        """Run one connection epoch for a stdio transport server."""
        # Build full env: inherit os.environ and overlay server-specific vars
        full_env = dict(os.environ)
        full_env.update(env)

        server_params = StdioServerParameters(
            command=cfg.command,
            args=cfg.args,
            env=full_env,
        )

        async with stdio_client(server_params) as (read_stream, write_stream):
            async with ClientSession(read_stream, write_stream) as session:
                # Initialize
                await asyncio.wait_for(session.initialize(), timeout=cfg.timeout)
                self._sessions[cfg.name] = session

                # Register tools
                await self._register_tools(cfg, session, epoch_id, gate)

                # Health loop — periodic ping as fallback for subprocess liveness
                while not self._stop_event.is_set():
                    await _interruptible_sleep(
                        self._stop_event, cfg.ping_interval_seconds
                    )
                    if self._stop_event.is_set():
                        break

                    try:
                        ping_timeout = min(cfg.ping_timeout_seconds, cfg.timeout)
                        await asyncio.wait_for(
                            session.send_ping(), timeout=ping_timeout
                        )
                    except Exception as exc:
                        logger.warning(
                            "MCP server %s: health check failed: %s",
                            cfg.name, exc,
                        )
                        raise  # Triggers reconnect in _server_loop

    async def _run_sse_epoch(
        self,
        cfg: MCPServerConfig,
        epoch_id: str,
        gate: EpochGate,
        headers: dict[str, str],
    ) -> None:
        """Run one connection epoch for an SSE transport server."""
        sse_kwargs: dict[str, Any] = {"url": cfg.url}
        if headers:
            sse_kwargs["headers"] = headers
        sse_kwargs["timeout"] = cfg.timeout

        async with sse_client(**sse_kwargs) as (read_stream, write_stream):
            async with ClientSession(read_stream, write_stream) as session:
                # Initialize
                await asyncio.wait_for(session.initialize(), timeout=cfg.timeout)
                self._sessions[cfg.name] = session

                # Register tools
                await self._register_tools(cfg, session, epoch_id, gate)

                # Health loop — periodic ping for SSE
                while not self._stop_event.is_set():
                    await _interruptible_sleep(
                        self._stop_event, cfg.ping_interval_seconds
                    )
                    if self._stop_event.is_set():
                        break

                    try:
                        ping_timeout = min(cfg.ping_timeout_seconds, cfg.timeout)
                        await asyncio.wait_for(
                            session.send_ping(), timeout=ping_timeout
                        )
                    except Exception as exc:
                        logger.warning(
                            "MCP server %s: SSE health check failed: %s",
                            cfg.name, exc,
                        )
                        raise

    async def _register_tools(
        self,
        cfg: MCPServerConfig,
        session: ClientSession,
        epoch_id: str,
        gate: EpochGate,
    ) -> None:
        """List tools from server, handle schema drift, register in ToolRegistry."""
        result = await asyncio.wait_for(session.list_tools(), timeout=cfg.timeout)

        # Sanitize schemas — ensure inputSchema has "type": "object"
        for tool in result.tools:
            schema = tool.inputSchema or {}
            if "type" not in schema:
                schema["type"] = "object"
            tool.inputSchema = schema

        # Parse risk from config
        risk = ToolRisk[cfg.risk_level]

        # Compute namespaced names
        new_set: set[str] = set()
        for tool in result.tools:
            namespaced = f"{cfg.name}__{tool.name}"
            new_set.add(namespaced)

        # Schema drift detection
        old_set = self._known_tools_by_server.get(cfg.name, set())
        stale = old_set - new_set
        added = new_set - old_set

        # Unregister stale tools (ownership-checked)
        tools_removed: list[str] = []
        for tool_name in stale:
            if self._tool_ownership.get(tool_name) == cfg.name:
                if self._registry is not None:
                    self._registry.unregister(tool_name)
                self._tool_ownership.pop(tool_name, None)
                tools_removed.append(tool_name)
                logger.info("Unregistered stale MCP tool: %s", tool_name)

        # Register new/updated tools
        tools_added: list[str] = []
        for mcp_tool in result.tools:
            namespaced = f"{cfg.name}__{mcp_tool.name}"
            wb_tool = MCPClientTool(
                server_name=cfg.name,
                mcp_tool=mcp_tool,
                risk=risk,
                manager=self,
            )
            if self._registry is not None:
                self._registry.register(wb_tool, overwrite=True)
            self._tool_ownership[namespaced] = cfg.name
            if namespaced in added:
                tools_added.append(namespaced)

        # Update known tools
        self._known_tools_by_server[cfg.name] = new_set

        # Mark connected
        self._status[cfg.name] = "connected"
        self._epochs[cfg.name] = epoch_id

        # Emit event
        self._emit_tools_changed(
            cfg.name,
            "connected",
            epoch_id,
            tools_added,
            tools_removed,
            len(new_set),
        )

        logger.info(
            "MCP server %s: connected (epoch=%s, tools=%d, added=%d, removed=%d)",
            cfg.name,
            epoch_id[:8],
            len(new_set),
            len(tools_added),
            len(tools_removed),
        )


# ---------------------------------------------------------------------------
# Utilities
# ---------------------------------------------------------------------------

async def _interruptible_sleep(stop_event: asyncio.Event, seconds: float) -> None:
    """Sleep for up to `seconds`, returning early if stop_event is set."""
    try:
        await asyncio.wait_for(stop_event.wait(), timeout=seconds)
    except asyncio.TimeoutError:
        pass
