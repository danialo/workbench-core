"""
LLM Router -- manages multiple providers and assembles streamed tool calls.

The router is the primary entry point for the rest of the workbench when it
needs an LLM response.  It:

  1. Streams ``StreamChunk`` objects from the active provider.
  2. Feeds tool-call deltas into a ``ToolCallAssembler``.
  3. Produces an ``AssembledAssistant`` for persistence.

If the assembler records any errors (malformed JSON from the model, etc.)
the final ``AssembledAssistant`` will have an *empty* ``tool_calls`` list
and the errors will be surfaced in ``metadata["assembler_errors"]``.
"""

from __future__ import annotations

import logging
from typing import AsyncIterator

from workbench.llm.providers.base import Provider
from workbench.llm.tool_call_assembler import ToolCallAssembler
from workbench.llm.types import (
    AssembledAssistant,
    Message,
    StreamChunk,
    ToolCall,
)

logger = logging.getLogger(__name__)


class LLMRouter:
    """
    Routes chat requests to a named provider and assembles the response.
    """

    def __init__(self) -> None:
        self._providers: dict[str, Provider] = {}
        self._active: str | None = None

    # ------------------------------------------------------------------
    # Provider management
    # ------------------------------------------------------------------

    def register_provider(self, name: str, provider: Provider) -> None:
        """Register a provider under *name*.  Overwrites any existing entry."""
        self._providers[name] = provider
        if self._active is None:
            self._active = name

    def set_active(self, name: str) -> None:
        """
        Switch the active provider.

        Raises ``KeyError`` if *name* has not been registered.
        """
        if name not in self._providers:
            raise KeyError(
                f"Unknown provider {name!r}. "
                f"Registered: {list(self._providers)}"
            )
        self._active = name

    @property
    def active_name(self) -> str | None:
        """Return the name of the currently active provider (or ``None``)."""
        return self._active

    @property
    def active_provider(self) -> Provider:
        """
        Return the active ``Provider`` instance.

        Raises ``RuntimeError`` if no provider is active.
        """
        if self._active is None or self._active not in self._providers:
            raise RuntimeError("No active LLM provider")
        return self._providers[self._active]

    @property
    def provider_names(self) -> list[str]:
        """Return the list of registered provider names."""
        return list(self._providers)

    # ------------------------------------------------------------------
    # Streaming chat
    # ------------------------------------------------------------------

    async def chat(
        self,
        messages: list[Message],
        tools: list[dict] | None = None,
        stream: bool = True,
        timeout: float = 30.0,
    ) -> AsyncIterator[StreamChunk]:
        """
        Stream ``StreamChunk`` objects from the active provider.

        Tool-call deltas are forwarded through as-is so the UI can render
        them incrementally.
        """
        provider = self.active_provider
        async for chunk in provider.chat(
            messages, tools=tools, stream=stream, timeout=timeout
        ):
            yield chunk

    async def chat_complete(
        self,
        messages: list[Message],
        tools: list[dict] | None = None,
        stream: bool = True,
        timeout: float = 30.0,
    ) -> AssembledAssistant:
        """
        Consume the full stream and return an ``AssembledAssistant``.

        This is the convenience method most callers should use.  It
        internally drives the assembler and handles error bookkeeping.
        """
        provider = self.active_provider
        assembler = ToolCallAssembler()
        content_parts: list[str] = []
        assembled_calls: list[ToolCall] = []

        async for chunk in provider.chat(
            messages, tools=tools, stream=stream, timeout=timeout
        ):
            # Accumulate text.
            if chunk.delta:
                content_parts.append(chunk.delta)

            # Feed tool deltas into the assembler.
            if chunk.tool_deltas:
                for td in chunk.tool_deltas:
                    finished = assembler.feed(td)
                    assembled_calls.extend(finished)

        # Flush any remaining incomplete buffers.
        assembled_calls.extend(assembler.flush())

        # Build metadata.
        metadata: dict = {}
        if self._active:
            metadata["provider"] = self._active

        # If the assembler recorded errors, drop all tool calls and report.
        if assembler.errors:
            logger.warning(
                "Tool-call assembly errors: %s", assembler.errors
            )
            metadata["assembler_errors"] = list(assembler.errors)
            assembled_calls = []

        return AssembledAssistant(
            content="".join(content_parts),
            tool_calls=assembled_calls,
            model=None,  # providers can inject this later
            provider=self._active,
            metadata=metadata,
        )

    # ------------------------------------------------------------------
    # Token counting
    # ------------------------------------------------------------------

    def count_tokens(
        self,
        messages: list[Message],
        tools: list[dict] | None = None,
    ) -> int:
        """Delegate token counting to the active provider."""
        return self.active_provider.count_tokens(messages, tools)
