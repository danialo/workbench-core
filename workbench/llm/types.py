"""Core types for the LLM subsystem."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class Message:
    """A single message in a conversation."""

    role: str  # "user", "assistant", "system", "tool"
    content: str
    tool_calls: list[ToolCall] | None = None
    tool_call_id: str | None = None
    model: str | None = None
    provider: str | None = None


@dataclass
class ToolCall:
    """A resolved tool call with parsed arguments."""

    id: str
    name: str
    arguments: dict


@dataclass
class RawToolDelta:
    """
    An incremental delta for a streaming tool call.

    Providers emit these as tool-call fragments arrive.  The ToolCallAssembler
    accumulates them and produces finished ToolCall objects.
    """

    call_index: int
    id: str | None = None
    name_delta: str = ""
    args_delta: str = ""
    done: bool = False


@dataclass
class StreamChunk:
    """
    A single chunk yielded while streaming a chat completion.

    *delta* carries new text content.
    *tool_deltas* carries incremental tool-call fragments.
    *tool_calls* carries fully-assembled tool calls (set by the router after
    the assembler has finished).
    *done* is ``True`` on the final chunk.
    """

    delta: str = ""
    tool_deltas: list[RawToolDelta] | None = None
    tool_calls: list[ToolCall] | None = None
    done: bool = False


@dataclass
class AssembledAssistant:
    """
    The complete assistant turn after consuming the full stream.

    Produced by ``LLMRouter.chat_complete`` for easy persistence.
    """

    content: str
    tool_calls: list[ToolCall]
    model: str | None = None
    provider: str | None = None
    metadata: dict = field(default_factory=dict)
