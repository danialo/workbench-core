"""
Claude Code CLI provider — uses the local ``claude`` CLI as an LLM backend.

This lets workbench-core piggyback on a Claude Code (Max/Pro) subscription
instead of needing a separate Anthropic API key.  The CLI is invoked in
headless mode with ``--output-format stream-json`` and its built-in tools
disabled so the workbench orchestrator handles tool execution.

Design notes
------------
- Spawns ``claude -p <prompt> --output-format stream-json --tools ""``
- Parses newline-delimited JSON events from stdout
- Maps ``text_delta`` events → ``StreamChunk(delta=...)``
- Maps tool-call events → ``StreamChunk(tool_deltas=...)``
- The workbench tool registry is passed as ``--mcp-config`` (future)

TODO
----
- [ ] Basic streaming text: parse stream-json, yield StreamChunks
- [ ] Tool call passthrough: map workbench tools → claude MCP format
- [ ] Session continuity: --resume flag for multi-turn conversations
- [ ] Error handling: CLI not installed, auth expired, rate limits
- [ ] Token counting: extract usage from result event
- [ ] Agent SDK migration: replace CLI subprocess with claude-agent-sdk
      when the Python SDK stabilises (pip install claude-agent-sdk)

References
----------
- ``claude -p --help`` for CLI flags
- ``claude --output-format stream-json`` event format
- Agent SDK: https://docs.anthropic.com/en/docs/claude-code/sdk
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import AsyncIterator

from workbench.llm.providers.base import Provider
from workbench.llm.types import Message, StreamChunk

logger = logging.getLogger(__name__)


class ClaudeCodeProvider(Provider):
    """
    LLM provider that delegates to the local ``claude`` CLI.

    Uses the user's Claude Code subscription (Max/Pro) — no API key needed.
    """

    def __init__(
        self,
        claude_binary: str = "claude",
        max_context: int = 200_000,
        max_output: int = 16_384,
        timeout: float = 300.0,
    ) -> None:
        self._binary = claude_binary
        self._max_context = max_context
        self._max_output = max_output
        self._timeout = timeout

    @property
    def name(self) -> str:
        return "claude-code"

    @property
    def max_context_tokens(self) -> int:
        return self._max_context

    @property
    def max_output_tokens(self) -> int:
        return self._max_output

    def count_tokens(
        self,
        messages: list[Message],
        tools: list[dict] | None = None,
    ) -> int:
        # Rough estimate — claude CLI doesn't expose a token counter
        text = json.dumps([m.model_dump() for m in messages])
        return len(text) // 4

    async def chat(
        self,
        messages: list[Message],
        tools: list[dict] | None = None,
        stream: bool = True,
        timeout: float = 30.0,
    ) -> AsyncIterator[StreamChunk]:
        """
        Spawn ``claude -p`` and stream results back.

        STUB — not yet implemented.
        """
        raise NotImplementedError(
            "ClaudeCodeProvider is a stub. See TODO list in module docstring."
        )
        # Unreachable yield to satisfy async generator type
        yield StreamChunk()  # type: ignore[misc]
