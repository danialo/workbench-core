"""
High-level session manager.

Ties together the event store, artifact store, and context packer to
provide a coherent API for orchestrator code:

- Record events.
- Derive a ``Message`` list from the event history.
- Build a token-budgeted context window for LLM calls.
"""

from __future__ import annotations

import json
import uuid
from typing import Any

from workbench.llm.types import Message, ToolCall
from workbench.session.artifacts import ArtifactStore
from workbench.session.context import ContextPacker
from workbench.session.events import (
    EVENT_ASSISTANT_MESSAGE,
    EVENT_TOOL_CALL_REQUEST,
    EVENT_TOOL_CALL_RESULT,
    EVENT_USER_MESSAGE,
    SessionEvent,
)
from workbench.session.store import SessionStore
from workbench.types import ContextPackReport


class Session:
    """
    Manages a single conversation session.

    Parameters
    ----------
    store:
        Persistent event/session store.
    artifact_store:
        Content-addressed artifact store.
    token_counter:
        Token counter instance (``workbench.llm.token_counter.TokenCounter``).
    """

    def __init__(
        self,
        store: SessionStore,
        artifact_store: ArtifactStore,
        token_counter: Any,
    ) -> None:
        self.store = store
        self.artifact_store = artifact_store
        self.token_counter = token_counter
        self.session_id: str | None = None
        self._turn_id: str | None = None
        self._packer = ContextPacker(token_counter)

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def start(self, metadata: dict | None = None) -> str:
        """Create a new session and return its id."""
        self.session_id = await self.store.create_session(metadata)
        self._turn_id = None
        return self.session_id

    async def resume(self, session_id: str) -> None:
        """
        Attach to an existing session.

        Raises
        ------
        ValueError
            If the session does not exist in the store.
        """
        info = await self.store.get_session(session_id)
        if info is None:
            raise ValueError(f"Session not found: {session_id}")
        self.session_id = session_id
        self._turn_id = None

    # ------------------------------------------------------------------
    # Turn management
    # ------------------------------------------------------------------

    def new_turn(self) -> str:
        """Start a new conversational turn and return its id."""
        self._turn_id = str(uuid.uuid4())
        return self._turn_id

    @property
    def turn_id(self) -> str:
        """Current turn id.  Creates a new turn if none exists."""
        if not self._turn_id:
            return self.new_turn()
        return self._turn_id

    # ------------------------------------------------------------------
    # Event recording
    # ------------------------------------------------------------------

    async def append_event(self, event: SessionEvent) -> None:
        """Persist an event to the current session."""
        if self.session_id is None:
            raise RuntimeError("No active session -- call start() or resume() first")
        await self.store.append_event(self.session_id, event)

    # ------------------------------------------------------------------
    # Message derivation
    # ------------------------------------------------------------------

    async def get_messages(self) -> list[Message]:
        """
        Derive the ordered ``Message`` list from the event history.

        Maps event types to message roles:

        - ``user_message``     -> role ``user``
        - ``assistant_message`` -> role ``assistant`` (may include tool_calls
          assembled from subsequent ``tool_call_request`` events)
        - ``tool_call_result`` -> role ``tool``

        Tool-call requests that immediately follow an assistant message are
        attached to that assistant message's ``tool_calls`` list rather than
        emitted as separate messages (matching the chat-completion API
        convention).
        """
        if self.session_id is None:
            raise RuntimeError("No active session")

        events = await self.store.get_events(self.session_id)
        messages: list[Message] = []

        # We accumulate tool_calls for the current assistant message.
        pending_tool_calls: list[ToolCall] = []

        for event in events:
            et = event.event_type
            p = event.payload

            if et == EVENT_USER_MESSAGE:
                # Flush any pending assistant with tool calls.
                self._flush_pending(messages, pending_tool_calls)
                pending_tool_calls = []

                messages.append(Message(role="user", content=p.get("content", "")))

            elif et == EVENT_ASSISTANT_MESSAGE:
                # Flush previous pending tool calls.
                self._flush_pending(messages, pending_tool_calls)
                pending_tool_calls = []

                messages.append(
                    Message(
                        role="assistant",
                        content=p.get("content", ""),
                        model=p.get("model"),
                    )
                )

            elif et == EVENT_TOOL_CALL_REQUEST:
                pending_tool_calls.append(
                    ToolCall(
                        id=p["tool_call_id"],
                        name=p["tool_name"],
                        arguments=p.get("arguments", {}),
                    )
                )

            elif et == EVENT_TOOL_CALL_RESULT:
                # Flush tool calls onto the preceding assistant message.
                self._flush_pending(messages, pending_tool_calls)
                pending_tool_calls = []

                # Build the tool-result content string.
                content = p.get("content", "")
                if p.get("error"):
                    content = f"[Error] {p['error']}: {content}"

                messages.append(
                    Message(
                        role="tool",
                        content=content,
                        tool_call_id=p.get("tool_call_id"),
                    )
                )

            # confirmation, model_switch, protocol_error events are metadata;
            # they don't map to LLM messages.

        # Final flush in case the conversation ends with tool_call_requests.
        self._flush_pending(messages, pending_tool_calls)

        return messages

    @staticmethod
    def _flush_pending(
        messages: list[Message],
        pending_tool_calls: list[ToolCall],
    ) -> None:
        """Attach accumulated tool calls to the last assistant message."""
        if not pending_tool_calls:
            return
        # Walk backwards to find the most recent assistant message.
        for msg in reversed(messages):
            if msg.role == "assistant":
                if msg.tool_calls is None:
                    msg.tool_calls = []
                msg.tool_calls.extend(pending_tool_calls)
                break
        pending_tool_calls.clear()

    # ------------------------------------------------------------------
    # Context window
    # ------------------------------------------------------------------

    async def get_context_window(
        self,
        tools: list[dict] | None,
        system_prompt: str,
        max_context_tokens: int,
        max_output_tokens: int,
        reserve_tokens: int = 200,
    ) -> tuple[list[Message], ContextPackReport]:
        """
        Build a token-budgeted context window from the session history.

        Returns the trimmed message list and a :class:`ContextPackReport`.
        """
        messages = await self.get_messages()
        return self._packer.pack(
            messages=messages,
            tools=tools,
            system_prompt=system_prompt,
            max_context_tokens=max_context_tokens,
            max_output_tokens=max_output_tokens,
            reserve_tokens=reserve_tokens,
        )
