"""Abstract base class for LLM providers."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import AsyncIterator

from workbench.llm.types import Message, StreamChunk


class Provider(ABC):
    """
    A provider encapsulates access to a single LLM endpoint.

    Implementations must support:
      - Streaming chat completions (``chat``).
      - Token counting (``count_tokens``).
      - Reporting context-window limits.
    """

    @abstractmethod
    async def chat(
        self,
        messages: list[Message],
        tools: list[dict] | None = None,
        stream: bool = True,
        timeout: float = 30.0,
    ) -> AsyncIterator[StreamChunk]:
        """
        Start a chat completion.

        Yields ``StreamChunk`` objects.  The last chunk has ``done=True``.
        """
        ...
        # Make the method an async generator so sub-classes can ``yield``.
        # This line is unreachable but satisfies the type checker.
        if False:  # pragma: no cover
            yield StreamChunk()  # type: ignore[misc]

    @abstractmethod
    def count_tokens(
        self,
        messages: list[Message],
        tools: list[dict] | None = None,
    ) -> int:
        """Estimate the total token count for the given conversation."""
        ...

    @property
    @abstractmethod
    def max_context_tokens(self) -> int:
        """Maximum number of tokens the model can accept as input."""
        ...

    @property
    def max_output_tokens(self) -> int:
        """Maximum number of tokens the model can generate."""
        return 4096

    @property
    @abstractmethod
    def name(self) -> str:
        """Human-readable provider name (e.g. ``"openai-compat"``).."""
        ...
