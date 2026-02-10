"""
Session event model.

Every interaction in a session is recorded as a SessionEvent. Events are
immutable once created and can be serialized to/from dicts for SQLite
storage.
"""

from __future__ import annotations

import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any

from workbench.types import ToolResult


# ---------------------------------------------------------------------------
# Base event
# ---------------------------------------------------------------------------


@dataclass
class SessionEvent:
    """
    A single event in a session's history.

    Attributes
    ----------
    event_type:
        One of the core event types (``user_message``, ``assistant_message``,
        ``tool_call_request``, ``tool_call_result``, ``confirmation``,
        ``model_switch``, ``protocol_error``).
    payload:
        Arbitrary event-specific data stored as a JSON-compatible dict.
    event_id:
        Unique identifier for the event (UUID4).
    turn_id:
        Groups events that belong to the same user turn.
    timestamp:
        UTC timestamp of event creation.
    """

    event_type: str
    payload: dict[str, Any]
    event_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    turn_id: str = ""
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    # ------------------------------------------------------------------
    # Serialization
    # ------------------------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        """Serialize to a plain dict suitable for JSON / SQLite storage."""
        d = asdict(self)
        d["timestamp"] = self.timestamp.isoformat()
        return d

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> SessionEvent:
        """Reconstruct a SessionEvent from a dict produced by ``to_dict``."""
        data = dict(data)  # shallow copy so we don't mutate the caller's dict
        ts = data.get("timestamp")
        if isinstance(ts, str):
            data["timestamp"] = datetime.fromisoformat(ts)
        return cls(**data)


# ---------------------------------------------------------------------------
# Core event types
# ---------------------------------------------------------------------------

EVENT_USER_MESSAGE = "user_message"
EVENT_ASSISTANT_MESSAGE = "assistant_message"
EVENT_TOOL_CALL_REQUEST = "tool_call_request"
EVENT_TOOL_CALL_RESULT = "tool_call_result"
EVENT_CONFIRMATION = "confirmation"
EVENT_MODEL_SWITCH = "model_switch"
EVENT_PROTOCOL_ERROR = "protocol_error"


# ---------------------------------------------------------------------------
# Factory helpers
# ---------------------------------------------------------------------------


def user_message_event(turn_id: str, content: str) -> SessionEvent:
    """Create a ``user_message`` event."""
    return SessionEvent(
        event_type=EVENT_USER_MESSAGE,
        payload={"content": content},
        turn_id=turn_id,
    )


def assistant_message_event(
    turn_id: str,
    content: str,
    model: str | None = None,
) -> SessionEvent:
    """Create an ``assistant_message`` event."""
    payload: dict[str, Any] = {"content": content}
    if model is not None:
        payload["model"] = model
    return SessionEvent(
        event_type=EVENT_ASSISTANT_MESSAGE,
        payload=payload,
        turn_id=turn_id,
    )


def tool_call_request_event(
    turn_id: str,
    tool_call_id: str,
    tool_name: str,
    arguments: dict[str, Any],
) -> SessionEvent:
    """Create a ``tool_call_request`` event."""
    return SessionEvent(
        event_type=EVENT_TOOL_CALL_REQUEST,
        payload={
            "tool_call_id": tool_call_id,
            "tool_name": tool_name,
            "arguments": arguments,
        },
        turn_id=turn_id,
    )


def tool_call_result_event(
    turn_id: str,
    tool_call_id: str,
    tool_name: str,
    result: ToolResult,
) -> SessionEvent:
    """Create a ``tool_call_result`` event."""
    return SessionEvent(
        event_type=EVENT_TOOL_CALL_RESULT,
        payload={
            "tool_call_id": tool_call_id,
            "tool_name": tool_name,
            "success": result.success,
            "content": result.content,
            "data": result.data,
            "error": result.error,
            "error_code": result.error_code,
            "metadata": result.metadata,
        },
        turn_id=turn_id,
    )


def confirmation_event(
    turn_id: str,
    tool_call_id: str,
    tool_name: str,
    confirmed: bool,
) -> SessionEvent:
    """Create a ``confirmation`` event (user accepts or rejects a tool call)."""
    return SessionEvent(
        event_type=EVENT_CONFIRMATION,
        payload={
            "tool_call_id": tool_call_id,
            "tool_name": tool_name,
            "confirmed": confirmed,
        },
        turn_id=turn_id,
    )


def model_switch_event(
    turn_id: str,
    from_model: str,
    to_model: str,
) -> SessionEvent:
    """Create a ``model_switch`` event."""
    return SessionEvent(
        event_type=EVENT_MODEL_SWITCH,
        payload={
            "from_model": from_model,
            "to_model": to_model,
        },
        turn_id=turn_id,
    )


def protocol_error_event(
    turn_id: str,
    error_message: str,
    details: dict[str, Any] | None = None,
) -> SessionEvent:
    """Create a ``protocol_error`` event."""
    payload: dict[str, Any] = {"error_message": error_message}
    if details is not None:
        payload["details"] = details
    return SessionEvent(
        event_type=EVENT_PROTOCOL_ERROR,
        payload=payload,
        turn_id=turn_id,
    )
