"""
OpenAI-compatible chat-completion provider.

Works with any endpoint that speaks the OpenAI ``/v1/chat/completions`` wire
protocol -- OpenAI itself, Azure OpenAI, vLLM, LM Studio, LocalAI, etc.

Dependencies: ``httpx`` (async HTTP client).  No ``openai`` SDK needed.
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


class OpenAICompatProvider(Provider):
    """
    Stream-capable provider for any OpenAI-API-compatible endpoint.

    Parameters
    ----------
    url:
        Base URL of the API, e.g. ``"https://api.openai.com/v1"`` or
        ``"http://localhost:8080/v1"``.
    model:
        Model identifier sent in the ``model`` field.
    api_key:
        Bearer token.  Pass ``""`` for unauthenticated local endpoints.
    timeout:
        HTTP request timeout in seconds.
    max_retries:
        Number of automatic retries on transient HTTP errors (5xx, 429).
    max_context:
        Maximum context window size in tokens.  Defaults to 128 000.
    max_output:
        Maximum output tokens.  Defaults to 4096.
    """

    def __init__(
        self,
        url: str = "https://api.openai.com/v1",
        model: str = "gpt-4",
        api_key: str = "",
        timeout: float = 120.0,
        max_retries: int = 2,
        max_context: int = 128_000,
        max_output: int = 4096,
    ) -> None:
        self._url = url.rstrip("/")
        self._model = model
        self._api_key = api_key
        self._timeout = timeout
        self._max_retries = max_retries
        self._max_context = max_context
        self._max_output = max_output
        self._counter = TokenCounter(model)

    # ------------------------------------------------------------------
    # Provider interface
    # ------------------------------------------------------------------

    @property
    def name(self) -> str:
        return "openai-compat"

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
        headers = self._build_headers()
        effective_timeout = timeout or self._timeout

        if stream:
            async for chunk in self._stream_request(body, headers, effective_timeout):
                yield chunk
        else:
            result = await self._sync_request(body, headers, effective_timeout)
            yield result

    # ------------------------------------------------------------------
    # Request building
    # ------------------------------------------------------------------

    def _build_headers(self) -> dict[str, str]:
        headers: dict[str, str] = {
            "Content-Type": "application/json",
            "Accept": "text/event-stream",
        }
        if self._api_key:
            headers["Authorization"] = f"Bearer {self._api_key}"
        return headers

    def _build_body(
        self,
        messages: list[Message],
        tools: list[dict] | None,
        stream: bool,
    ) -> dict:
        wire_messages = []
        for msg in messages:
            m: dict = {"role": msg.role, "content": msg.content}
            if msg.tool_calls:
                m["tool_calls"] = [
                    {
                        "id": tc.id,
                        "type": "function",
                        "function": {
                            "name": tc.name,
                            "arguments": json.dumps(tc.arguments),
                        },
                    }
                    for tc in msg.tool_calls
                ]
            if msg.tool_call_id:
                m["tool_call_id"] = msg.tool_call_id
            wire_messages.append(m)

        body: dict = {
            "model": self._model,
            "messages": wire_messages,
            "stream": stream,
        }
        if tools:
            body["tools"] = tools
            body["tool_choice"] = "auto"
        logger.info(
            "REQUEST: model=%s tools=%d messages=%d api_key=%s...",
            self._model,
            len(tools) if tools else 0,
            len(wire_messages),
            self._api_key[:12] if self._api_key else "(none)",
        )
        return body

    # ------------------------------------------------------------------
    # Streaming request
    # ------------------------------------------------------------------

    async def _stream_request(
        self,
        body: dict,
        headers: dict[str, str],
        timeout: float,
    ) -> AsyncIterator[StreamChunk]:
        url = f"{self._url}/chat/completions"

        last_error: Exception | None = None
        for attempt in range(1 + self._max_retries):
            try:
                async with httpx.AsyncClient(timeout=timeout) as client:
                    async with client.stream(
                        "POST", url, json=body, headers=headers
                    ) as response:
                        if response.status_code == 429 or response.status_code >= 500:
                            # Retryable -- read body so the connection is released.
                            await response.aread()
                            last_error = httpx.HTTPStatusError(
                                f"HTTP {response.status_code}",
                                request=response.request,
                                response=response,
                            )
                            continue

                        response.raise_for_status()

                        async for chunk in self._parse_sse_stream(response):
                            yield chunk
                        return  # success
            except httpx.TransportError as exc:
                last_error = exc
                if attempt < self._max_retries:
                    continue
                raise

        if last_error is not None:
            raise last_error

    async def _parse_sse_stream(
        self, response: httpx.Response
    ) -> AsyncIterator[StreamChunk]:
        """
        Parse Server-Sent Events from the response byte stream.

        Each SSE event has the form::

            data: {json}\\n\\n

        The sentinel ``data: [DONE]`` terminates the stream.
        """
        buffer = ""
        async for raw_bytes in response.aiter_bytes():
            buffer += raw_bytes.decode("utf-8", errors="replace")

            while "\n" in buffer:
                line, buffer = buffer.split("\n", 1)
                line = line.rstrip("\r")

                if not line:
                    # Empty line -- SSE event boundary.
                    continue

                if line.startswith("data:"):
                    data_str = line[len("data:"):].strip()

                    if data_str == "[DONE]":
                        yield StreamChunk(done=True)
                        return

                    try:
                        data = json.loads(data_str)
                    except json.JSONDecodeError:
                        logger.warning("Failed to parse SSE data: %s", data_str[:200])
                        continue

                    chunk = self._sse_data_to_chunk(data)
                    if chunk is not None:
                        yield chunk

        # If the stream ends without [DONE], emit a final chunk.
        yield StreamChunk(done=True)

    def _sse_data_to_chunk(self, data: dict) -> StreamChunk | None:
        """Convert a parsed SSE ``data`` payload into a ``StreamChunk``."""
        choices = data.get("choices")
        if not choices:
            return None

        choice = choices[0]
        delta = choice.get("delta", {})
        finish_reason = choice.get("finish_reason")

        text_delta = delta.get("content") or ""

        # Tool-call deltas
        tool_deltas: list[RawToolDelta] | None = None
        raw_tcs = delta.get("tool_calls")
        if raw_tcs:
            tool_deltas = []
            for raw_tc in raw_tcs:
                idx = raw_tc.get("index", 0)
                tc_id = raw_tc.get("id")
                func = raw_tc.get("function", {})
                name_delta = func.get("name", "") or ""
                args_delta = func.get("arguments", "") or ""
                tool_deltas.append(
                    RawToolDelta(
                        call_index=idx,
                        id=tc_id,
                        name_delta=name_delta,
                        args_delta=args_delta,
                        done=False,
                    )
                )

        done = finish_reason is not None
        if done and tool_deltas:
            # Mark the last delta as done for each active tool call.
            # OpenAI signals finish_reason="tool_calls" at the end.
            seen_indices: set[int] = set()
            for td in tool_deltas:
                seen_indices.add(td.call_index)
            # We don't know which indices are still open -- the assembler
            # handles that.  Mark all deltas in this chunk as done.
            for td in tool_deltas:
                td.done = True

        return StreamChunk(
            delta=text_delta,
            tool_deltas=tool_deltas,
            done=done,
        )

    # ------------------------------------------------------------------
    # Non-streaming request
    # ------------------------------------------------------------------

    async def _sync_request(
        self,
        body: dict,
        headers: dict[str, str],
        timeout: float,
    ) -> StreamChunk:
        url = f"{self._url}/chat/completions"

        last_error: Exception | None = None
        for attempt in range(1 + self._max_retries):
            try:
                async with httpx.AsyncClient(timeout=timeout) as client:
                    resp = await client.post(url, json=body, headers=headers)

                    if resp.status_code == 429 or resp.status_code >= 500:
                        last_error = httpx.HTTPStatusError(
                            f"HTTP {resp.status_code}",
                            request=resp.request,
                            response=resp,
                        )
                        continue

                    resp.raise_for_status()
                    data = resp.json()
            except httpx.TransportError as exc:
                last_error = exc
                if attempt < self._max_retries:
                    continue
                raise
            else:
                return self._parse_non_stream(data)

        if last_error is not None:
            raise last_error
        # Should never reach here.
        raise RuntimeError("unreachable")  # pragma: no cover

    def _parse_non_stream(self, data: dict) -> StreamChunk:
        """Convert a non-streaming response into a single ``StreamChunk``."""
        choices = data.get("choices", [])
        if not choices:
            return StreamChunk(done=True)

        message = choices[0].get("message", {})
        content = message.get("content") or ""

        # Build finished tool call deltas (with done=True).
        tool_deltas: list[RawToolDelta] | None = None
        raw_tcs = message.get("tool_calls")
        if raw_tcs:
            tool_deltas = []
            for idx, raw_tc in enumerate(raw_tcs):
                func = raw_tc.get("function", {})
                tool_deltas.append(
                    RawToolDelta(
                        call_index=idx,
                        id=raw_tc.get("id"),
                        name_delta=func.get("name", ""),
                        args_delta=func.get("arguments", ""),
                        done=True,
                    )
                )

        return StreamChunk(
            delta=content,
            tool_deltas=tool_deltas,
            done=True,
        )
