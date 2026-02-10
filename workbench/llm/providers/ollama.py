"""
Ollama provider.

Streams responses from a local Ollama instance via its ``/api/chat`` endpoint.
Supports tool calling when the Ollama model advertises it.

Dependencies: ``httpx``.
"""

from __future__ import annotations

import json
import logging
from typing import AsyncIterator

import httpx

from workbench.llm.providers.base import Provider
from workbench.llm.token_counter import TokenCounter
from workbench.llm.types import Message, RawToolDelta, StreamChunk

logger = logging.getLogger(__name__)

# Ollama context sizes vary by model.  Default to a reasonable value; users
# can override via the constructor.
_DEFAULT_MAX_CONTEXT = 8192


class OllamaProvider(Provider):
    """
    Provider for a local `Ollama <https://ollama.com>`_ instance.

    Parameters
    ----------
    url:
        Base URL of the Ollama HTTP API (e.g. ``"http://localhost:11434"``).
    model:
        Model tag, e.g. ``"llama3"`` or ``"mistral"``.
    timeout:
        HTTP request timeout in seconds.
    max_context:
        Maximum context window in tokens (model-dependent).
    max_output:
        Maximum output tokens.
    """

    def __init__(
        self,
        url: str = "http://localhost:11434",
        model: str = "llama3",
        timeout: float = 120.0,
        max_context: int = _DEFAULT_MAX_CONTEXT,
        max_output: int = 4096,
    ) -> None:
        self._url = url.rstrip("/")
        self._model = model
        self._timeout = timeout
        self._max_context = max_context
        self._max_output = max_output
        self._counter = TokenCounter(model)

    # ------------------------------------------------------------------
    # Provider interface
    # ------------------------------------------------------------------

    @property
    def name(self) -> str:
        return "ollama"

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
        return self._counter.count_messages(messages, tools)

    async def chat(
        self,
        messages: list[Message],
        tools: list[dict] | None = None,
        stream: bool = True,
        timeout: float = 30.0,
    ) -> AsyncIterator[StreamChunk]:
        body = self._build_body(messages, tools, stream)
        effective_timeout = timeout or self._timeout

        if stream:
            async for chunk in self._stream_request(body, effective_timeout):
                yield chunk
        else:
            result = await self._non_stream_request(body, effective_timeout)
            yield result

    # ------------------------------------------------------------------
    # Request building
    # ------------------------------------------------------------------

    def _build_body(
        self,
        messages: list[Message],
        tools: list[dict] | None,
        stream: bool,
    ) -> dict:
        wire_messages: list[dict] = []
        for msg in messages:
            m: dict = {"role": msg.role, "content": msg.content}

            if msg.tool_calls:
                m["tool_calls"] = [
                    {
                        "function": {
                            "name": tc.name,
                            "arguments": tc.arguments,  # Ollama expects dict, not string
                        },
                    }
                    for tc in msg.tool_calls
                ]

            wire_messages.append(m)

        body: dict = {
            "model": self._model,
            "messages": wire_messages,
            "stream": stream,
        }

        if tools:
            body["tools"] = tools

        return body

    # ------------------------------------------------------------------
    # Streaming
    # ------------------------------------------------------------------

    async def _stream_request(
        self,
        body: dict,
        timeout: float,
    ) -> AsyncIterator[StreamChunk]:
        """
        Ollama streams newline-delimited JSON objects from ``/api/chat``.
        Each line is a complete JSON object.
        """
        url = f"{self._url}/api/chat"

        async with httpx.AsyncClient(timeout=timeout) as client:
            async with client.stream(
                "POST", url, json=body
            ) as response:
                response.raise_for_status()

                buffer = ""
                async for raw_bytes in response.aiter_bytes():
                    buffer += raw_bytes.decode("utf-8", errors="replace")

                    while "\n" in buffer:
                        line, buffer = buffer.split("\n", 1)
                        line = line.strip()
                        if not line:
                            continue

                        try:
                            data = json.loads(line)
                        except json.JSONDecodeError:
                            logger.warning(
                                "Ollama: failed to parse line: %s",
                                line[:200],
                            )
                            continue

                        chunk = self._data_to_chunk(data)
                        yield chunk

                        if chunk.done:
                            return

                # Process any remaining data in the buffer.
                remaining = buffer.strip()
                if remaining:
                    try:
                        data = json.loads(remaining)
                        chunk = self._data_to_chunk(data)
                        yield chunk
                        if chunk.done:
                            return
                    except json.JSONDecodeError:
                        pass

                # Safety: always end with a done chunk.
                yield StreamChunk(done=True)

    def _data_to_chunk(self, data: dict) -> StreamChunk:
        """Convert a single Ollama JSON object to a ``StreamChunk``."""
        is_done = data.get("done", False)

        message = data.get("message", {})
        content = message.get("content", "") or ""

        # Tool calls -- Ollama returns them in the message.tool_calls field.
        tool_deltas: list[RawToolDelta] | None = None
        raw_tool_calls = message.get("tool_calls")
        if raw_tool_calls:
            tool_deltas = []
            for idx, tc in enumerate(raw_tool_calls):
                func = tc.get("function", {})
                tool_deltas.append(
                    RawToolDelta(
                        call_index=idx,
                        id=f"ollama_call_{idx}",
                        name_delta=func.get("name", ""),
                        args_delta=json.dumps(func.get("arguments", {})),
                        done=True,
                    )
                )

        return StreamChunk(
            delta=content,
            tool_deltas=tool_deltas,
            done=is_done,
        )

    # ------------------------------------------------------------------
    # Non-streaming
    # ------------------------------------------------------------------

    async def _non_stream_request(
        self,
        body: dict,
        timeout: float,
    ) -> StreamChunk:
        url = f"{self._url}/api/chat"

        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.post(url, json=body)
            resp.raise_for_status()
            data = resp.json()

        return self._data_to_chunk(data | {"done": True})
