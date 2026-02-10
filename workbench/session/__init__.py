"""Session management: events, persistence, artifacts, context packing."""

from workbench.session.artifacts import ArtifactStore
from workbench.session.context import ContextPacker
from workbench.session.events import (
    EVENT_ASSISTANT_MESSAGE,
    EVENT_CONFIRMATION,
    EVENT_MODEL_SWITCH,
    EVENT_PROTOCOL_ERROR,
    EVENT_TOOL_CALL_REQUEST,
    EVENT_TOOL_CALL_RESULT,
    EVENT_USER_MESSAGE,
    SessionEvent,
    assistant_message_event,
    confirmation_event,
    model_switch_event,
    protocol_error_event,
    tool_call_request_event,
    tool_call_result_event,
    user_message_event,
)
from workbench.session.session import Session
from workbench.session.store import SessionStore

__all__ = [
    "ArtifactStore",
    "ContextPacker",
    "Session",
    "SessionEvent",
    "SessionStore",
    # Event type constants
    "EVENT_ASSISTANT_MESSAGE",
    "EVENT_CONFIRMATION",
    "EVENT_MODEL_SWITCH",
    "EVENT_PROTOCOL_ERROR",
    "EVENT_TOOL_CALL_REQUEST",
    "EVENT_TOOL_CALL_RESULT",
    "EVENT_USER_MESSAGE",
    # Factory functions
    "assistant_message_event",
    "confirmation_event",
    "model_switch_event",
    "protocol_error_event",
    "tool_call_request_event",
    "tool_call_result_event",
    "user_message_event",
]
