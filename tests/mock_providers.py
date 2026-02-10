"""
Mock LLM providers for testing.

Provides canned responses so tests can exercise the router and assembler
without hitting real APIs.
"""

from __future__ import annotations

from typing import AsyncIterator

from workbench.llm.providers.base import Provider
from workbench.llm.token_counter import TokenCounter
from workbench.llm.types import Message, RawToolDelta, StreamChunk


class MockProvider(Provider):
    """
    A provider that yields pre-configured ``StreamChunk`` objects.

    Usage::

        chunks = [
            StreamChunk(delta="Hello "),
            StreamChunk(delta="world!"),
            StreamChunk(done=True),
        ]
        provider = MockProvider(chunks=chunks)

    Parameters
    ----------
    chunks:
        The exact sequence of ``StreamChunk`` objects to yield.
    model_name:
        Model identifier returned by ``name``.
    max_ctx:
        Value for ``max_context_tokens``.
    max_out:
        Value for ``max_output_tokens``.
    """

    def __init__(
        self,
        chunks: list[StreamChunk] | None = None,
        model_name: str = "mock-model",
        max_ctx: int = 4096,
        max_out: int = 1024,
    ) -> None:
        self._chunks = chunks or [StreamChunk(done=True)]
        self._model_name = model_name
        self._max_ctx = max_ctx
        self._max_out = max_out
        self._counter = TokenCounter(None)
        self.call_count = 0
        self.last_messages: list[Message] | None = None
        self.last_tools: list[dict] | None = None

    @property
    def name(self) -> str:
        return self._model_name

    @property
    def max_context_tokens(self) -> int:
        return self._max_ctx

    @property
    def max_output_tokens(self) -> int:
        return self._max_out

    def count_tokens(
        self,
        messages: list[Message],
        tools: list[dict] | None = None,
    ) -> int:
        return self._counter.count_messages(messages, tools)

    async def chat(
        self,
        messages: list[Message],
        tools: list[dict] | None = None,
        stream: bool = True,
        timeout: float = 30.0,
    ) -> AsyncIterator[StreamChunk]:
        self.call_count += 1
        self.last_messages = messages
        self.last_tools = tools
        for chunk in self._chunks:
            yield chunk


def make_text_provider(text: str, model_name: str = "mock-text") -> MockProvider:
    """
    Convenience: create a ``MockProvider`` that streams a simple text response
    one word at a time.
    """
    words = text.split(" ")
    chunks: list[StreamChunk] = []
    for i, word in enumerate(words):
        suffix = " " if i < len(words) - 1 else ""
        chunks.append(StreamChunk(delta=word + suffix))
    chunks.append(StreamChunk(done=True))
    return MockProvider(chunks=chunks, model_name=model_name)


def make_tool_call_provider(
    tool_name: str,
    tool_args: dict,
    call_id: str = "call_abc123",
    model_name: str = "mock-tool",
    content_prefix: str = "",
) -> MockProvider:
    """
    Convenience: create a ``MockProvider`` that streams a tool call via
    ``RawToolDelta`` objects.

    The tool name and arguments are split across multiple deltas to exercise
    the assembler.
    """
    import json

    args_json = json.dumps(tool_args)

    chunks: list[StreamChunk] = []

    # Optional text content before the tool call.
    if content_prefix:
        chunks.append(StreamChunk(delta=content_prefix))

    # Stream the tool name in two parts.
    half = len(tool_name) // 2
    name_part1 = tool_name[:half]
    name_part2 = tool_name[half:]

    chunks.append(
        StreamChunk(
            tool_deltas=[
                RawToolDelta(
                    call_index=0,
                    id=call_id,
                    name_delta=name_part1,
                )
            ]
        )
    )
    chunks.append(
        StreamChunk(
            tool_deltas=[
                RawToolDelta(
                    call_index=0,
                    name_delta=name_part2,
                )
            ]
        )
    )

    # Stream the arguments in thirds.
    third = max(1, len(args_json) // 3)
    parts = [
        args_json[:third],
        args_json[third : 2 * third],
        args_json[2 * third :],
    ]
    for part in parts:
        if part:
            chunks.append(
                StreamChunk(
                    tool_deltas=[
                        RawToolDelta(call_index=0, args_delta=part)
                    ]
                )
            )

    # Final delta with done=True.
    chunks.append(
        StreamChunk(
            tool_deltas=[RawToolDelta(call_index=0, done=True)],
            done=True,
        )
    )

    return MockProvider(chunks=chunks, model_name=model_name)


def make_multi_tool_call_provider(
    calls: list[tuple[str, dict, str]],
    model_name: str = "mock-multi-tool",
) -> MockProvider:
    """
    Create a provider that streams multiple concurrent tool calls.

    *calls* is a list of ``(tool_name, tool_args, call_id)`` tuples.
    """
    import json

    chunks: list[StreamChunk] = []

    # Emit all names first (interleaved).
    for idx, (tool_name, _, call_id) in enumerate(calls):
        chunks.append(
            StreamChunk(
                tool_deltas=[
                    RawToolDelta(
                        call_index=idx,
                        id=call_id,
                        name_delta=tool_name,
                    )
                ]
            )
        )

    # Emit all arguments.
    for idx, (_, tool_args, _) in enumerate(calls):
        args_json = json.dumps(tool_args)
        chunks.append(
            StreamChunk(
                tool_deltas=[
                    RawToolDelta(
                        call_index=idx,
                        args_delta=args_json,
                    )
                ]
            )
        )

    # Emit done for all.
    for idx in range(len(calls)):
        chunks.append(
            StreamChunk(
                tool_deltas=[RawToolDelta(call_index=idx, done=True)]
            )
        )

    chunks.append(StreamChunk(done=True))
    return MockProvider(chunks=chunks, model_name=model_name)


def make_malformed_tool_call_provider(
    model_name: str = "mock-malformed",
) -> MockProvider:
    """
    Create a provider that emits a tool call with invalid JSON arguments.

    The assembler should record an error and emit no ``ToolCall``.
    """
    chunks: list[StreamChunk] = [
        StreamChunk(
            tool_deltas=[
                RawToolDelta(
                    call_index=0,
                    id="call_bad",
                    name_delta="broken_tool",
                )
            ]
        ),
        StreamChunk(
            tool_deltas=[
                RawToolDelta(
                    call_index=0,
                    args_delta='{"key": INVALID_JSON',
                )
            ]
        ),
        StreamChunk(
            tool_deltas=[RawToolDelta(call_index=0, done=True)],
            done=True,
        ),
    ]
    return MockProvider(chunks=chunks, model_name=model_name)
