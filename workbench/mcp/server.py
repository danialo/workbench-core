"""MCP server — expose workbench tools over the Model Context Protocol.

Usage:
    wb mcp serve          # stdio transport (for Claude Desktop, Cursor, etc.)
    wb mcp serve --sse    # SSE transport (for remote clients)

The server exposes every tool in the workbench ToolRegistry as an MCP tool.
Policy enforcement (risk gating, blocked patterns) is applied on every call.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from mcp.server.lowlevel import Server
from mcp.server.stdio import stdio_server
from mcp.types import (
    CallToolResult,
    TextContent,
    Tool as MCPTool,
)

from workbench.tools.base import Tool as WBTool
from workbench.tools.registry import ToolRegistry
from workbench.tools.policy import PolicyEngine

logger = logging.getLogger(__name__)


def create_mcp_server(
    registry: ToolRegistry,
    policy: PolicyEngine | None = None,
    *,
    name: str = "workbench",
    tool_filter: list[str] | None = None,
) -> Server:
    """Build an MCP server that wraps workbench tools.

    Parameters
    ----------
    registry : ToolRegistry
        The workbench tool registry (already populated with tools).
    policy : PolicyEngine | None
        If provided, each tool call is gated by policy checks.
    name : str
        Server name advertised to MCP clients.
    tool_filter : list[str] | None
        If provided, only expose these tool names (whitelist).

    Returns
    -------
    Server
        Ready-to-run low-level MCP server instance.
    """
    server = Server(name)

    # Build the tool list
    tools = registry.list()
    if tool_filter:
        allowed = set(tool_filter)
        tools = [t for t in tools if t.name in allowed]

    # Index for fast lookup during call_tool
    tool_index: dict[str, WBTool] = {t.name: t for t in tools}

    @server.list_tools()
    async def handle_list_tools() -> list[MCPTool]:
        return [
            MCPTool(
                name=t.name,
                description=t.description,
                inputSchema=t.parameters or {"type": "object", "properties": {}},
            )
            for t in tools
        ]

    @server.call_tool()
    async def handle_call_tool(name: str, arguments: dict[str, Any] | None) -> list[TextContent]:
        arguments = arguments or {}

        # Lookup
        wb_tool = tool_index.get(name)
        if wb_tool is None:
            return [TextContent(type="text", text=f"Unknown tool: {name}")]

        # Policy check
        if policy is not None:
            decision = policy.check(wb_tool, arguments)
            if not decision.allowed:
                return [TextContent(
                    type="text",
                    text=f"Policy blocked: {decision.reason}",
                )]
            if decision.requires_confirmation:
                # MCP has no built-in confirmation — proceed with a log warning.
                # The MCP client (Claude Desktop, etc.) handles approval at their level.
                logger.info(
                    "Tool %s requires confirmation (risk=%s) — proceeding in MCP context",
                    wb_tool.name,
                    wb_tool.risk_level.name,
                )

        # Execute
        try:
            result = await wb_tool.execute(**arguments)
        except Exception as e:
            logger.exception("Tool %s execution error", wb_tool.name)
            return [TextContent(type="text", text=f"Error: {e}")]

        # Build response
        parts: list[TextContent] = []

        if result.content:
            parts.append(TextContent(type="text", text=result.content))

        if result.data is not None:
            parts.append(TextContent(
                type="text",
                text=json.dumps(result.data, indent=2, default=str),
            ))

        if not result.success and result.error:
            parts.append(TextContent(type="text", text=f"Error: {result.error}"))

        if not parts:
            parts.append(TextContent(type="text", text="(no output)"))

        return parts

    logger.info("MCP server '%s' ready with %d tools", name, len(tools))
    return server


async def run_stdio(server: Server) -> None:
    """Run the MCP server over stdin/stdout."""
    async with stdio_server() as (read_stream, write_stream):
        await server.run(
            read_stream,
            write_stream,
            server.create_initialization_options(),
        )
