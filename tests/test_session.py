"""
Tests for session store, events, context packing, and the Session manager.
"""

from __future__ import annotations

import json
import tempfile
import uuid
from datetime import datetime, timezone
from pathlib import Path

import pytest

from workbench.llm.token_counter import TokenCounter
from workbench.llm.types import Message
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
from workbench.types import ToolResult


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def tmp_db(tmp_path: Path) -> str:
    return str(tmp_path / "test_sessions.db")


@pytest.fixture
async def store(tmp_db: str):
    s = SessionStore(tmp_db)
    await s.init()
    yield s
    await s.close()


@pytest.fixture
def counter() -> TokenCounter:
    return TokenCounter()  # heuristic mode (no tiktoken in CI)


@pytest.fixture
def packer(counter: TokenCounter) -> ContextPacker:
    return ContextPacker(counter)


@pytest.fixture
async def session(store: SessionStore, tmp_path: Path, counter: TokenCounter):
    art_store = ArtifactStore(str(tmp_path / "artifacts"))
    sess = Session(store=store, artifact_store=art_store, token_counter=counter)
    await sess.start()
    return sess


# ===================================================================
# SessionEvent tests
# ===================================================================


class TestSessionEvent:
    def test_to_dict_and_back(self):
        ev = SessionEvent(
            event_type="user_message",
            payload={"content": "hello"},
            turn_id="turn-1",
        )
        d = ev.to_dict()
        assert isinstance(d["timestamp"], str)
        restored = SessionEvent.from_dict(d)
        assert restored.event_type == ev.event_type
        assert restored.payload == ev.payload
        assert restored.event_id == ev.event_id
        assert restored.turn_id == ev.turn_id
        assert isinstance(restored.timestamp, datetime)

    def test_event_id_is_uuid(self):
        ev = SessionEvent(event_type="test", payload={})
        uuid.UUID(ev.event_id)  # Should not raise

    def test_timestamp_is_utc(self):
        ev = SessionEvent(event_type="test", payload={})
        assert ev.timestamp.tzinfo is not None


class TestEventFactories:
    def test_user_message_event(self):
        ev = user_message_event("t1", "hello")
        assert ev.event_type == EVENT_USER_MESSAGE
        assert ev.payload["content"] == "hello"
        assert ev.turn_id == "t1"

    def test_assistant_message_event(self):
        ev = assistant_message_event("t1", "hi", model="gpt-4")
        assert ev.event_type == EVENT_ASSISTANT_MESSAGE
        assert ev.payload["model"] == "gpt-4"

    def test_assistant_message_event_no_model(self):
        ev = assistant_message_event("t1", "hi")
        assert "model" not in ev.payload

    def test_tool_call_request_event(self):
        ev = tool_call_request_event("t1", "tc-1", "read_file", {"path": "/tmp"})
        assert ev.event_type == EVENT_TOOL_CALL_REQUEST
        assert ev.payload["tool_call_id"] == "tc-1"
        assert ev.payload["tool_name"] == "read_file"
        assert ev.payload["arguments"] == {"path": "/tmp"}

    def test_tool_call_result_event(self):
        result = ToolResult(success=True, content="ok", data={"key": "val"})
        ev = tool_call_result_event("t1", "tc-1", "read_file", result)
        assert ev.event_type == EVENT_TOOL_CALL_RESULT
        assert ev.payload["success"] is True
        assert ev.payload["content"] == "ok"
        assert ev.payload["data"] == {"key": "val"}

    def test_confirmation_event(self):
        ev = confirmation_event("t1", "tc-1", "delete_file", True)
        assert ev.event_type == EVENT_CONFIRMATION
        assert ev.payload["confirmed"] is True

    def test_model_switch_event(self):
        ev = model_switch_event("t1", "gpt-4", "claude-3")
        assert ev.event_type == EVENT_MODEL_SWITCH
        assert ev.payload["from_model"] == "gpt-4"
        assert ev.payload["to_model"] == "claude-3"

    def test_protocol_error_event(self):
        ev = protocol_error_event("t1", "bad response", {"raw": "..."})
        assert ev.event_type == EVENT_PROTOCOL_ERROR
        assert ev.payload["error_message"] == "bad response"
        assert ev.payload["details"] == {"raw": "..."}

    def test_protocol_error_event_no_details(self):
        ev = protocol_error_event("t1", "oops")
        assert "details" not in ev.payload


# ===================================================================
# SessionStore tests
# ===================================================================


class TestSessionStore:
    async def test_create_and_get_session(self, store: SessionStore):
        sid = await store.create_session({"name": "test"})
        info = await store.get_session(sid)
        assert info is not None
        assert info["session_id"] == sid
        assert info["metadata"] == {"name": "test"}

    async def test_create_session_default_metadata(self, store: SessionStore):
        sid = await store.create_session()
        info = await store.get_session(sid)
        assert info is not None
        assert info["metadata"] == {}

    async def test_get_nonexistent_session(self, store: SessionStore):
        result = await store.get_session("does-not-exist")
        assert result is None

    async def test_list_sessions(self, store: SessionStore):
        await store.create_session({"name": "first"})
        await store.create_session({"name": "second"})
        sessions = await store.list_sessions()
        assert len(sessions) == 2
        # Newest first
        assert sessions[0]["metadata"]["name"] == "second"
        assert sessions[1]["metadata"]["name"] == "first"

    async def test_append_and_get_events(self, store: SessionStore):
        sid = await store.create_session()
        ev1 = user_message_event("t1", "hello")
        ev2 = assistant_message_event("t1", "hi there")
        await store.append_event(sid, ev1)
        await store.append_event(sid, ev2)

        events = await store.get_events(sid)
        assert len(events) == 2
        assert events[0].event_type == EVENT_USER_MESSAGE
        assert events[0].payload["content"] == "hello"
        assert events[1].event_type == EVENT_ASSISTANT_MESSAGE
        assert events[1].payload["content"] == "hi there"

    async def test_events_ordered_chronologically(self, store: SessionStore):
        sid = await store.create_session()
        for i in range(5):
            await store.append_event(
                sid, user_message_event("t1", f"msg-{i}")
            )
        events = await store.get_events(sid)
        contents = [e.payload["content"] for e in events]
        assert contents == ["msg-0", "msg-1", "msg-2", "msg-3", "msg-4"]

    async def test_get_events_filter_by_type(self, store: SessionStore):
        sid = await store.create_session()
        await store.append_event(sid, user_message_event("t1", "hello"))
        await store.append_event(sid, assistant_message_event("t1", "hi"))
        await store.append_event(sid, user_message_event("t1", "bye"))

        user_events = await store.get_events(sid, event_type=EVENT_USER_MESSAGE)
        assert len(user_events) == 2
        assert all(e.event_type == EVENT_USER_MESSAGE for e in user_events)

    async def test_multiple_sessions_isolated(self, store: SessionStore):
        sid1 = await store.create_session()
        sid2 = await store.create_session()

        await store.append_event(sid1, user_message_event("t1", "session1"))
        await store.append_event(sid2, user_message_event("t2", "session2"))

        events1 = await store.get_events(sid1)
        events2 = await store.get_events(sid2)

        assert len(events1) == 1
        assert len(events2) == 1
        assert events1[0].payload["content"] == "session1"
        assert events2[0].payload["content"] == "session2"

    async def test_delete_session_removes_events(self, store: SessionStore):
        sid = await store.create_session()
        await store.append_event(sid, user_message_event("t1", "hello"))
        await store.append_event(sid, assistant_message_event("t1", "hi"))

        await store.delete_session(sid)

        assert await store.get_session(sid) is None
        events = await store.get_events(sid)
        assert events == []

    async def test_schema_version_tracked(self, store: SessionStore):
        version = await store.get_schema_version()
        assert version == 1

    async def test_event_roundtrip_preserves_fields(self, store: SessionStore):
        sid = await store.create_session()
        original = tool_call_request_event(
            "t1", "tc-abc", "my_tool", {"x": 42, "nested": {"a": [1, 2]}}
        )
        await store.append_event(sid, original)

        events = await store.get_events(sid)
        assert len(events) == 1
        restored = events[0]
        assert restored.event_id == original.event_id
        assert restored.turn_id == original.turn_id
        assert restored.event_type == original.event_type
        assert restored.payload == original.payload

    async def test_reopen_database(self, tmp_db: str):
        """Data persists across close/reopen."""
        store1 = SessionStore(tmp_db)
        await store1.init()
        sid = await store1.create_session({"persistent": True})
        await store1.append_event(sid, user_message_event("t1", "saved"))
        await store1.close()

        store2 = SessionStore(tmp_db)
        await store2.init()
        events = await store2.get_events(sid)
        assert len(events) == 1
        assert events[0].payload["content"] == "saved"
        await store2.close()


# ===================================================================
# ContextPacker tests
# ===================================================================


class TestContextPacker:
    def test_all_messages_fit(self, packer: ContextPacker):
        msgs = [
            Message(role="user", content="hello"),
            Message(role="assistant", content="hi"),
        ]
        packed, report = packer.pack(
            msgs, tools=None, system_prompt="You are helpful.",
            max_context_tokens=10000, max_output_tokens=1000,
        )
        assert len(packed) == 2
        assert report.dropped_messages == 0
        assert report.kept_messages == 2

    def test_drops_oldest_to_fit(self, packer: ContextPacker):
        # Create many messages to exceed a moderate budget.
        msgs = [
            Message(role="user", content=f"message number {i} " * 50)
            for i in range(20)
        ]
        packed, report = packer.pack(
            msgs, tools=None, system_prompt="sys",
            max_context_tokens=4000, max_output_tokens=1000,
        )
        assert report.dropped_messages > 0
        assert report.kept_messages > 0
        assert report.kept_messages < 20
        assert report.kept_messages == len(packed)
        # Most recent messages should be kept.
        assert packed[-1].content == msgs[-1].content

    def test_system_messages_always_kept(self, packer: ContextPacker):
        msgs = [
            Message(role="system", content="I am the system."),
            Message(role="user", content="long message " * 200),
            Message(role="user", content="short"),
        ]
        packed, report = packer.pack(
            msgs, tools=None, system_prompt="",
            max_context_tokens=200, max_output_tokens=50,
        )
        # System message must be present regardless of budget pressure.
        roles = [m.role for m in packed]
        assert "system" in roles

    def test_report_fields(self, packer: ContextPacker):
        msgs = [Message(role="user", content="hi")]
        _, report = packer.pack(
            msgs, tools=[{"type": "function", "function": {"name": "x"}}],
            system_prompt="You help.",
            max_context_tokens=8000, max_output_tokens=2000, reserve_tokens=300,
        )
        assert report.max_context_tokens == 8000
        assert report.max_output_tokens == 2000
        assert report.reserve_tokens == 300
        assert report.tool_schema_tokens > 0
        assert report.system_prompt_tokens > 0
        assert report.message_tokens > 0

    def test_empty_messages(self, packer: ContextPacker):
        packed, report = packer.pack(
            [], tools=None, system_prompt="sys",
            max_context_tokens=10000, max_output_tokens=1000,
        )
        assert packed == []
        assert report.kept_messages == 0
        assert report.dropped_messages == 0

    def test_tools_consume_budget(self, packer: ContextPacker):
        msgs = [Message(role="user", content="hi")]
        big_tools = [{"type": "function", "function": {"name": f"tool_{i}", "description": "x" * 200}} for i in range(20)]
        _, report_with_tools = packer.pack(
            msgs, tools=big_tools, system_prompt="",
            max_context_tokens=5000, max_output_tokens=1000,
        )
        _, report_no_tools = packer.pack(
            msgs, tools=None, system_prompt="",
            max_context_tokens=5000, max_output_tokens=1000,
        )
        assert report_with_tools.tool_schema_tokens > report_no_tools.tool_schema_tokens


# ===================================================================
# Session manager tests
# ===================================================================


class TestSession:
    async def test_start_creates_session(self, session: Session):
        assert session.session_id is not None

    async def test_new_turn(self, session: Session):
        t1 = session.new_turn()
        t2 = session.new_turn()
        assert t1 != t2
        assert session.turn_id == t2

    async def test_turn_id_auto_creates(self, session: Session):
        # Reset internal state
        session._turn_id = None
        tid = session.turn_id
        assert tid is not None
        uuid.UUID(tid)  # Valid UUID

    async def test_append_and_get_messages(self, session: Session):
        tid = session.new_turn()
        await session.append_event(user_message_event(tid, "What is 2+2?"))
        await session.append_event(assistant_message_event(tid, "4"))

        msgs = await session.get_messages()
        assert len(msgs) == 2
        assert msgs[0].role == "user"
        assert msgs[0].content == "What is 2+2?"
        assert msgs[1].role == "assistant"
        assert msgs[1].content == "4"

    async def test_tool_calls_attached_to_assistant(self, session: Session):
        tid = session.new_turn()
        await session.append_event(user_message_event(tid, "Read /tmp/x"))
        await session.append_event(assistant_message_event(tid, ""))
        await session.append_event(
            tool_call_request_event(tid, "tc-1", "read_file", {"path": "/tmp/x"})
        )
        await session.append_event(
            tool_call_result_event(
                tid, "tc-1", "read_file",
                ToolResult(success=True, content="file contents"),
            )
        )

        msgs = await session.get_messages()
        assert len(msgs) == 3  # user, assistant (with tool_calls), tool result
        assert msgs[1].role == "assistant"
        assert msgs[1].tool_calls is not None
        assert len(msgs[1].tool_calls) == 1
        assert msgs[1].tool_calls[0].name == "read_file"
        assert msgs[2].role == "tool"
        assert msgs[2].tool_call_id == "tc-1"

    async def test_tool_result_error_format(self, session: Session):
        tid = session.new_turn()
        await session.append_event(user_message_event(tid, "do it"))
        await session.append_event(assistant_message_event(tid, ""))
        await session.append_event(
            tool_call_request_event(tid, "tc-1", "rm", {"path": "/"})
        )
        await session.append_event(
            tool_call_result_event(
                tid, "tc-1", "rm",
                ToolResult(success=False, content="permission denied", error="EPERM"),
            )
        )

        msgs = await session.get_messages()
        tool_msg = [m for m in msgs if m.role == "tool"][0]
        assert "EPERM" in tool_msg.content
        assert "permission denied" in tool_msg.content

    async def test_get_context_window(self, session: Session):
        tid = session.new_turn()
        await session.append_event(user_message_event(tid, "hello"))
        await session.append_event(assistant_message_event(tid, "hi"))

        msgs, report = await session.get_context_window(
            tools=None,
            system_prompt="You are helpful.",
            max_context_tokens=10000,
            max_output_tokens=2000,
        )
        assert len(msgs) == 2
        assert report.kept_messages == 2
        assert report.dropped_messages == 0

    async def test_resume_nonexistent_raises(
        self, store: SessionStore, tmp_path: Path, counter: TokenCounter
    ):
        art_store = ArtifactStore(str(tmp_path / "artifacts2"))
        sess = Session(store=store, artifact_store=art_store, token_counter=counter)
        with pytest.raises(ValueError, match="Session not found"):
            await sess.resume("nonexistent-id")

    async def test_append_without_session_raises(
        self, store: SessionStore, tmp_path: Path, counter: TokenCounter
    ):
        art_store = ArtifactStore(str(tmp_path / "artifacts3"))
        sess = Session(store=store, artifact_store=art_store, token_counter=counter)
        with pytest.raises(RuntimeError, match="No active session"):
            await sess.append_event(user_message_event("t1", "hello"))

    async def test_resume_existing(self, session: Session):
        sid = session.session_id
        # Create a new Session instance and resume
        sess2 = Session(
            store=session.store,
            artifact_store=session.artifact_store,
            token_counter=session.token_counter,
        )
        await sess2.resume(sid)
        assert sess2.session_id == sid
