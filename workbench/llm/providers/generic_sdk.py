"""
Generic SDK provider that dynamically wraps vendor SDKs.

Currently supports:
  - **anthropic** -- Anthropic's Python SDK (``anthropic`` package).
  - **openai** -- OpenAI's Python SDK (``openai`` package).

The provider detects which SDK is installed and exposes a unified streaming
interface through the standard ``Provider`` ABC.
"""

from __future__ import annotations

import json
import logging
import uuid
from typing import AsyncIterator

from workbench.llm.providers.base import Provider
from workbench.llm.token_counter import TokenCounter
from workbench.llm.types import Message, RawToolDelta, StreamChunk

logger = logging.getLogger(__name__)


def _import_anthropic():
    """Attempt to import the Anthropic SDK.  Returns ``None`` on failure."""
    try:
        import anthropic  # type: ignore[import-untyped]
        return anthropic
    except ImportError:
        return None


def _import_openai():
    """Attempt to import the OpenAI SDK.  Returns ``None`` on failure."""
    try:
        import openai  # type: ignore[import-untyped]
        return openai
    except ImportError:
        return None


class GenericSDKProvider(Provider):
    """
    Provider backed by a vendor SDK (``anthropic`` or ``openai``).

    Parameters
    ----------
    sdk:
        Which SDK to use.  ``"anthropic"`` or ``"openai"``.  If ``"auto"``
        the first available SDK is selected (anthropic takes precedence).
    model:
        Model identifier.
    api_key:
        API key.  If omitted the SDK's default environment-variable lookup
        is used.
    base_url:
        Override the base URL (useful for proxies).
    max_context:
        Context-window size in tokens.
    max_output:
        Maximum output tokens.
    timeout:
        Default request timeout.
    """

    def __init__(
        self,
        sdk: str = "auto",
        model: str | None = None,
        api_key: str | None = None,
        base_url: str | None = None,
        max_context: int = 128_000,
        max_output: int = 4096,
        timeout: float = 120.0,
    ) -> None:
        self._sdk_name = self._resolve_sdk(sdk)
        self._model = model or self._default_model()
        self._api_key = api_key
        self._base_url = base_url
        self._max_context = max_context
        self._max_output = max_output
        self._timeout = timeout
        self._counter = TokenCounter(self._model)
        self._client = None  # lazily initialised

    # ------------------------------------------------------------------
    # SDK resolution
    # ------------------------------------------------------------------

    @staticmethod
    def _resolve_sdk(requested: str) -> str:
        if requested == "auto":
            if _import_anthropic() is not None:
                return "anthropic"
            if _import_openai() is not None:
                return "openai"
            raise ImportError(
                "No supported LLM SDK found. "
                "Install 'anthropic' or 'openai'."
            )
        if requested not in ("anthropic", "openai"):
            raise ValueError(f"Unsupported SDK: {requested!r}")
        return requested

    def _default_model(self) -> str:
        if self._sdk_name == "anthropic":
            return "claude-sonnet-4-20250514"
        return "gpt-4"

    # ------------------------------------------------------------------
    # Provider interface
    # ------------------------------------------------------------------

    @property
    def name(self) -> str:
        return f"sdk-{self._sdk_name}"

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
        if self._sdk_name == "anthropic":
            async for chunk in self._anthropic_chat(messages, tools, stream, timeout):
                yield chunk
        else:
            async for chunk in self._openai_chat(messages, tools, stream, timeout):
                yield chunk

    # ------------------------------------------------------------------
    # Anthropic implementation
    # ------------------------------------------------------------------

    def _get_anthropic_client(self):
        if self._client is not None:
            return self._client
        anthropic = _import_anthropic()
        if anthropic is None:
            raise ImportError("anthropic SDK is not installed")

        kwargs: dict = {}
        if self._api_key:
            kwargs["api_key"] = self._api_key
        if self._base_url:
            kwargs["base_url"] = self._base_url
        kwargs["timeout"] = self._timeout

        self._client = anthropic.AsyncAnthropic(**kwargs)
        return self._client

    def _anthropic_convert_messages(
        self, messages: list[Message]
    ) -> tuple[str | None, list[dict]]:
        """
        Convert internal ``Message`` list to Anthropic's format.

        Returns ``(system_prompt, messages_list)``.
        """
        system: str | None = None
        converted: list[dict] = []

        for msg in messages:
            if msg.role == "system":
                system = msg.content
                continue

            if msg.role == "tool":
                converted.append({
                    "role": "user",
                    "content": [
                        {
                            "type": "tool_result",
                            "tool_use_id": msg.tool_call_id or "",
                            "content": msg.content,
                        }
                    ],
                })
                continue

            if msg.role == "assistant" and msg.tool_calls:
                content_blocks: list[dict] = []
                if msg.content:
                    content_blocks.append({"type": "text", "text": msg.content})
                for tc in msg.tool_calls:
                    content_blocks.append({
                        "type": "tool_use",
                        "id": tc.id,
                        "name": tc.name,
                        "input": tc.arguments,
                    })
                converted.append({"role": "assistant", "content": content_blocks})
                continue

            converted.append({"role": msg.role, "content": msg.content})

        return system, converted

    def _anthropic_convert_tools(
        self, tools: list[dict] | None
    ) -> list[dict] | None:
        """Convert OpenAI-style tool schemas to Anthropic's format."""
        if not tools:
            return None
        converted = []
        for tool in tools:
            func = tool.get("function", tool)
            converted.append({
                "name": func["name"],
                "description": func.get("description", ""),
                "input_schema": func.get("parameters", {"type": "object"}),
            })
        return converted

    async def _anthropic_chat(
        self,
        messages: list[Message],
        tools: list[dict] | None,
        stream: bool,
        timeout: float,
    ) -> AsyncIterator[StreamChunk]:
        client = self._get_anthropic_client()
        system, converted_msgs = self._anthropic_convert_messages(messages)
        converted_tools = self._anthropic_convert_tools(tools)

        kwargs: dict = {
            "model": self._model,
            "messages": converted_msgs,
            "max_tokens": self._max_output,
        }
        if system:
            kwargs["system"] = system
        if converted_tools:
            kwargs["tools"] = converted_tools

        if stream:
            async with client.messages.stream(**kwargs) as stream_mgr:
                tool_call_idx = 0
                current_tool_id: str | None = None
                current_tool_name: str | None = None

                async for event in stream_mgr:
                    event_type = getattr(event, "type", None)

                    if event_type == "content_block_start":
                        block = getattr(event, "content_block", None)
                        if block and getattr(block, "type", None) == "tool_use":
                            current_tool_id = getattr(block, "id", f"call_{tool_call_idx}")
                            current_tool_name = getattr(block, "name", "")
                            yield StreamChunk(
                                tool_deltas=[
                                    RawToolDelta(
                                        call_index=tool_call_idx,
                                        id=current_tool_id,
                                        name_delta=current_tool_name or "",
                                    )
                                ]
                            )

                    elif event_type == "content_block_delta":
                        delta_obj = getattr(event, "delta", None)
                        if delta_obj:
                            delta_type = getattr(delta_obj, "type", None)
                            if delta_type == "text_delta":
                                yield StreamChunk(delta=getattr(delta_obj, "text", ""))
                            elif delta_type == "input_json_delta":
                                partial_json = getattr(delta_obj, "partial_json", "")
                                yield StreamChunk(
                                    tool_deltas=[
                                        RawToolDelta(
                                            call_index=tool_call_idx,
                                            args_delta=partial_json,
                                        )
                                    ]
                                )

                    elif event_type == "content_block_stop":
                        if current_tool_id is not None:
                            yield StreamChunk(
                                tool_deltas=[
                                    RawToolDelta(
                                        call_index=tool_call_idx,
                                        done=True,
                                    )
                                ]
                            )
                            tool_call_idx += 1
                            current_tool_id = None
                            current_tool_name = None

                    elif event_type == "message_stop":
                        yield StreamChunk(done=True)
                        return

            # Fallback done if message_stop was missed.
            yield StreamChunk(done=True)
        else:
            response = await client.messages.create(**kwargs)
            text_parts: list[str] = []
            tool_deltas: list[RawToolDelta] = []
            call_idx = 0

            for block in response.content:
                if getattr(block, "type", None) == "text":
                    text_parts.append(block.text)
                elif getattr(block, "type", None) == "tool_use":
                    tool_deltas.append(
                        RawToolDelta(
                            call_index=call_idx,
                            id=block.id,
                            name_delta=block.name,
                            args_delta=json.dumps(block.input),
                            done=True,
                        )
                    )
                    call_idx += 1

            yield StreamChunk(
                delta="".join(text_parts),
                tool_deltas=tool_deltas or None,
                done=True,
            )

    # ------------------------------------------------------------------
    # OpenAI SDK implementation
    # ------------------------------------------------------------------

    def _get_openai_client(self):
        if self._client is not None:
            return self._client
        openai_mod = _import_openai()
        if openai_mod is None:
            raise ImportError("openai SDK is not installed")

        kwargs: dict = {}
        if self._api_key:
            kwargs["api_key"] = self._api_key
        if self._base_url:
            kwargs["base_url"] = self._base_url
        kwargs["timeout"] = self._timeout

        self._client = openai_mod.AsyncOpenAI(**kwargs)
        return self._client

    async def _openai_chat(
        self,
        messages: list[Message],
        tools: list[dict] | None,
        stream: bool,
        timeout: float,
    ) -> AsyncIterator[StreamChunk]:
        client = self._get_openai_client()

        wire_messages: list[dict] = []
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

        kwargs: dict = {
            "model": self._model,
            "messages": wire_messages,
            "stream": stream,
        }
        if tools:
            kwargs["tools"] = tools

        if stream:
            response_stream = await client.chat.completions.create(**kwargs)
            async for chunk_data in response_stream:
                choices = chunk_data.choices
                if not choices:
                    continue

                choice = choices[0]
                delta = choice.delta
                finish_reason = choice.finish_reason

                text_delta = getattr(delta, "content", None) or ""

                tool_deltas: list[RawToolDelta] | None = None
                raw_tcs = getattr(delta, "tool_calls", None)
                if raw_tcs:
                    tool_deltas = []
                    for raw_tc in raw_tcs:
                        idx = getattr(raw_tc, "index", 0)
                        tc_id = getattr(raw_tc, "id", None)
                        func = getattr(raw_tc, "function", None)
                        name_d = getattr(func, "name", "") or "" if func else ""
                        args_d = getattr(func, "arguments", "") or "" if func else ""
                        tool_deltas.append(
                            RawToolDelta(
                                call_index=idx,
                                id=tc_id,
                                name_delta=name_d,
                                args_delta=args_d,
                                done=False,
                            )
                        )

                is_done = finish_reason is not None
                if is_done and tool_deltas:
                    for td in tool_deltas:
                        td.done = True

                yield StreamChunk(
                    delta=text_delta,
                    tool_deltas=tool_deltas,
                    done=is_done,
                )

            # If we exhausted the iterator without a finish_reason chunk:
            yield StreamChunk(done=True)
        else:
            response = await client.chat.completions.create(**kwargs)
            choice = response.choices[0] if response.choices else None
            if not choice:
                yield StreamChunk(done=True)
                return

            message = choice.message
            content = getattr(message, "content", None) or ""

            tool_deltas_list: list[RawToolDelta] | None = None
            raw_tcs = getattr(message, "tool_calls", None)
            if raw_tcs:
                tool_deltas_list = []
                for idx, raw_tc in enumerate(raw_tcs):
                    func = raw_tc.function
                    tool_deltas_list.append(
                        RawToolDelta(
                            call_index=idx,
                            id=raw_tc.id,
                            name_delta=func.name,
                            args_delta=func.arguments,
                            done=True,
                        )
                    )

            yield StreamChunk(
                delta=content,
                tool_deltas=tool_deltas_list,
                done=True,
            )
