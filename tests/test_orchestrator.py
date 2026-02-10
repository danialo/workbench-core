"""Tests for the orchestrator core."""

from __future__ import annotations

import asyncio
import json
import tempfile
import uuid

import pytest

from tests.mock_providers import (
    MockProvider,
    make_malformed_tool_call_provider,
    make_text_provider,
    make_tool_call_provider,
)
from tests.mock_tools import DestructiveTool, EchoTool, ShellTool, WriteTool
from workbench.llm.router import LLMRouter
from workbench.llm.token_counter import TokenCounter
from workbench.llm.types import RawToolDelta, StreamChunk
from workbench.orchestrator.core import Orchestrator
from workbench.session.artifacts import ArtifactStore
from workbench.session.session import Session
from workbench.session.store import SessionStore
from workbench.tools.base import ToolRisk
from workbench.tools.policy import PolicyEngine
from workbench.tools.registry import ToolRegistry
from workbench.types import ArtifactPayload, ToolResult


@pytest.fixture
def tmp_dir(tmp_path):
    return tmp_path


@pytest.fixture
async def session_store(tmp_dir):
    store = SessionStore(str(tmp_dir / "test.db"))
    await store.init()
    yield store
    await store.close()


@pytest.fixture
def artifact_store(tmp_dir):
    return ArtifactStore(str(tmp_dir / "artifacts"))


@pytest.fixture
def token_counter():
    return TokenCounter(None)


@pytest.fixture
async def session(session_store, artifact_store, token_counter):
    s = Session(session_store, artifact_store, token_counter)
    await s.start()
    return s


@pytest.fixture
def registry():
    reg = ToolRegistry()
    reg.register(EchoTool())
    reg.register(WriteTool())
    reg.register(DestructiveTool())
    reg.register(ShellTool())
    return reg


@pytest.fixture
def policy(tmp_dir):
    return PolicyEngine(
        max_risk=ToolRisk.SHELL,
        confirm_destructive=False,
        confirm_shell=False,
        confirm_write=False,
        audit_log_path=str(tmp_dir / "audit.jsonl"),
    )


def _make_orchestrator(
    session, registry, policy, provider, system_prompt="", confirmation_callback=None
):
    router = LLMRouter()
    router.register_provider("test", provider)
    return Orchestrator(
        session=session,
        registry=registry,
        router=router,
        policy=policy,
        system_prompt=system_prompt,
        tool_timeout=5.0,
        max_turns=10,
        confirmation_callback=confirmation_callback,
    )


async def _collect_chunks(orchestrator, user_input):
    chunks = []
    async for chunk in orchestrator.run(user_input):
        chunks.append(chunk)
    return chunks


class TestSuccessfulToolCall:
    async def test_echo_tool_lifecycle(self, session, registry, policy):
        """Successful tool call goes through full lifecycle."""
        provider = make_tool_call_provider("echo", {"message": "hello"})
        # After tool call, the provider needs to respond with text on second call
        # We need a provider that first returns a tool call, then returns text
        call_count = [0]

        class TwoTurnProvider(MockProvider):
            async def chat(self, messages, tools=None, stream=True, timeout=30.0):
                call_count[0] += 1
                if call_count[0] == 1:
                    # First call: return tool call
                    inner = make_tool_call_provider("echo", {"message": "hello"})
                    async for chunk in inner.chat(messages, tools, stream, timeout):
                        yield chunk
                else:
                    # Second call: return text response
                    inner = make_text_provider("Echo result: hello")
                    async for chunk in inner.chat(messages, tools, stream, timeout):
                        yield chunk

        provider = TwoTurnProvider()
        orch = _make_orchestrator(session, registry, policy, provider)
        chunks = await _collect_chunks(orch, "test echo")

        # Should have chunks from both the tool result and final response
        assert len(chunks) > 0
        all_text = "".join(c.delta for c in chunks if c.delta)
        assert "hello" in all_text.lower() or "echo" in all_text.lower()

    async def test_events_recorded(self, session, registry, policy):
        """Events are recorded in the session store."""
        provider = make_text_provider("Simple response")
        orch = _make_orchestrator(session, registry, policy, provider)
        await _collect_chunks(orch, "test message")

        events = await session.store.get_events(session.session_id)
        event_types = [e.event_type for e in events]
        assert "user_message" in event_types
        assert "assistant_message" in event_types


class TestUnknownTool:
    async def test_unknown_tool_produces_error_event(self, session, registry, policy):
        """Unknown tool call produces unknown_tool error."""
        provider = make_tool_call_provider("nonexistent_tool", {"arg": "val"})

        class OneTurnProvider(MockProvider):
            _turn = 0
            async def chat(self, messages, tools=None, stream=True, timeout=30.0):
                self._turn += 1
                if self._turn == 1:
                    inner = make_tool_call_provider("nonexistent_tool", {"arg": "val"})
                    async for chunk in inner.chat(messages, tools, stream, timeout):
                        yield chunk
                else:
                    inner = make_text_provider("I see the tool failed.")
                    async for chunk in inner.chat(messages, tools, stream, timeout):
                        yield chunk

        orch = _make_orchestrator(session, registry, policy, OneTurnProvider())
        await _collect_chunks(orch, "use nonexistent tool")

        events = await session.store.get_events(session.session_id)
        result_events = [e for e in events if e.event_type == "tool_call_result"]
        assert len(result_events) >= 1
        assert result_events[0].payload["error_code"] == "unknown_tool"


class TestValidationError:
    async def test_bad_args_produce_validation_error(self, session, registry, policy):
        """Invalid args produce validation_error event."""
        # echo tool requires "message" string, send integer
        provider = make_tool_call_provider("echo", {"message": 12345})

        class OneTurnProvider(MockProvider):
            _turn = 0
            async def chat(self, messages, tools=None, stream=True, timeout=30.0):
                self._turn += 1
                if self._turn == 1:
                    inner = make_tool_call_provider("echo", {"message": 12345})
                    async for chunk in inner.chat(messages, tools, stream, timeout):
                        yield chunk
                else:
                    inner = make_text_provider("Validation failed.")
                    async for chunk in inner.chat(messages, tools, stream, timeout):
                        yield chunk

        orch = _make_orchestrator(session, registry, policy, OneTurnProvider())
        await _collect_chunks(orch, "test bad args")

        events = await session.store.get_events(session.session_id)
        result_events = [e for e in events if e.event_type == "tool_call_result"]
        assert len(result_events) >= 1
        assert result_events[0].payload["error_code"] == "validation_error"


class TestPolicyBlock:
    async def test_policy_blocks_high_risk_tool(self, session, registry, tmp_path):
        """Policy blocks tools above max_risk."""
        strict_policy = PolicyEngine(
            max_risk=ToolRisk.READ_ONLY,
            confirm_destructive=False,
            audit_log_path=str(tmp_path / "audit.jsonl"),
        )

        class OneTurnProvider(MockProvider):
            _turn = 0
            async def chat(self, messages, tools=None, stream=True, timeout=30.0):
                self._turn += 1
                if self._turn == 1:
                    inner = make_tool_call_provider("write_file", {"path": "/tmp/x", "content": "y"})
                    async for chunk in inner.chat(messages, tools, stream, timeout):
                        yield chunk
                else:
                    inner = make_text_provider("Tool was blocked.")
                    async for chunk in inner.chat(messages, tools, stream, timeout):
                        yield chunk

        orch = _make_orchestrator(session, registry, strict_policy, OneTurnProvider())
        await _collect_chunks(orch, "write a file")

        events = await session.store.get_events(session.session_id)
        result_events = [e for e in events if e.event_type == "tool_call_result"]
        assert len(result_events) >= 1
        assert result_events[0].payload["error_code"] == "policy_block"


class TestConfirmation:
    async def test_cancelled_confirmation(self, session, registry, tmp_path):
        """Declined confirmation produces cancelled event."""
        confirm_policy = PolicyEngine(
            max_risk=ToolRisk.SHELL,
            confirm_destructive=True,
            audit_log_path=str(tmp_path / "audit.jsonl"),
        )

        async def deny_all(tool_name, tool_call):
            return False

        class OneTurnProvider(MockProvider):
            _turn = 0
            async def chat(self, messages, tools=None, stream=True, timeout=30.0):
                self._turn += 1
                if self._turn == 1:
                    inner = make_tool_call_provider("delete_resource", {"resource_id": "abc"})
                    async for chunk in inner.chat(messages, tools, stream, timeout):
                        yield chunk
                else:
                    inner = make_text_provider("Cancelled.")
                    async for chunk in inner.chat(messages, tools, stream, timeout):
                        yield chunk

        orch = _make_orchestrator(
            session, registry, confirm_policy, OneTurnProvider(),
            confirmation_callback=deny_all,
        )
        await _collect_chunks(orch, "delete something")

        events = await session.store.get_events(session.session_id)
        confirm_events = [e for e in events if e.event_type == "confirmation"]
        assert len(confirm_events) >= 1
        assert confirm_events[0].payload["confirmed"] is False

        result_events = [e for e in events if e.event_type == "tool_call_result"]
        assert any(e.payload["error_code"] == "cancelled" for e in result_events)


class TestProtocolError:
    async def test_assembler_errors_produce_protocol_error(self, session, registry, policy):
        """Assembler errors record protocol_error event, no tools executed."""
        provider = make_malformed_tool_call_provider()
        orch = _make_orchestrator(session, registry, policy, provider)
        await _collect_chunks(orch, "test malformed")

        events = await session.store.get_events(session.session_id)
        event_types = [e.event_type for e in events]
        assert "protocol_error" in event_types
        # No tool_call_request events since tools weren't executed
        assert "tool_call_request" not in event_types


class TestMaxTurns:
    async def test_max_turns_limit(self, session, registry, policy):
        """Orchestrator stops after max_turns."""

        class InfiniteToolProvider(MockProvider):
            async def chat(self, messages, tools=None, stream=True, timeout=30.0):
                # Always return a tool call
                inner = make_tool_call_provider("echo", {"message": "loop"})
                async for chunk in inner.chat(messages, tools, stream, timeout):
                    yield chunk

        orch = _make_orchestrator(session, registry, policy, InfiniteToolProvider())
        orch.max_turns = 3
        chunks = await _collect_chunks(orch, "infinite loop")

        all_text = "".join(c.delta for c in chunks if c.delta)
        assert "maximum" in all_text.lower() or "3" in all_text


class TestTextOnlyResponse:
    async def test_no_tools_returns_text(self, session, registry, policy):
        """LLM response with no tool calls returns text directly."""
        provider = make_text_provider("Just a text response.")
        orch = _make_orchestrator(session, registry, policy, provider)
        chunks = await _collect_chunks(orch, "hello")

        all_text = "".join(c.delta for c in chunks if c.delta)
        assert "Just a text response." in all_text
        assert any(c.done for c in chunks)
