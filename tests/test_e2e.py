"""End-to-end test using the demo backend."""

from __future__ import annotations

import json
import tempfile

import pytest

from tests.mock_providers import MockProvider, make_text_provider, make_tool_call_provider
from workbench.backends.bridge import (
    ListDiagnosticsTool,
    ResolveTargetTool,
    RunDiagnosticTool,
    SummarizeArtifactTool,
)
from workbench.backends.demo import DemoBackend
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


@pytest.fixture
async def full_stack(tmp_path):
    """Wire up the complete stack with demo backend and mock LLM."""
    # Stores
    store = SessionStore(str(tmp_path / "e2e.db"))
    await store.init()
    artifact_store = ArtifactStore(str(tmp_path / "artifacts"))
    token_counter = TokenCounter(None)

    # Session
    session = Session(store, artifact_store, token_counter)
    await session.start({"test": "e2e"})

    # Backend + Bridge tools
    backend = DemoBackend()
    registry = ToolRegistry()
    registry.register(ResolveTargetTool(backend))
    registry.register(ListDiagnosticsTool(backend))
    registry.register(RunDiagnosticTool(backend))
    registry.register(SummarizeArtifactTool(artifact_store))

    # Policy (permissive for e2e)
    policy = PolicyEngine(
        max_risk=ToolRisk.SHELL,
        confirm_destructive=False,
        confirm_shell=False,
        confirm_write=False,
        audit_log_path=str(tmp_path / "audit.jsonl"),
    )

    yield {
        "session": session,
        "store": store,
        "registry": registry,
        "policy": policy,
        "artifact_store": artifact_store,
    }

    await store.close()


class TestE2EResolveTarget:
    async def test_resolve_then_text(self, full_stack):
        """Full flow: resolve a target, then get text response."""
        session = full_stack["session"]
        registry = full_stack["registry"]
        policy = full_stack["policy"]

        class TwoTurnProvider(MockProvider):
            _turn = 0
            async def chat(self, messages, tools=None, stream=True, timeout=30.0):
                self._turn += 1
                if self._turn == 1:
                    inner = make_tool_call_provider(
                        "resolve_target", {"target": "demo-host-1"}
                    )
                    async for chunk in inner.chat(messages, tools, stream, timeout):
                        yield chunk
                else:
                    inner = make_text_provider(
                        "The host demo-host-1 is online running Ubuntu 22.04."
                    )
                    async for chunk in inner.chat(messages, tools, stream, timeout):
                        yield chunk

        router = LLMRouter()
        router.register_provider("test", TwoTurnProvider())

        orch = Orchestrator(
            session=session,
            registry=registry,
            router=router,
            policy=policy,
            system_prompt="You are a diagnostic assistant.",
        )

        chunks = []
        async for chunk in orch.run("Check demo-host-1"):
            chunks.append(chunk)

        # Verify events
        events = await session.store.get_events(session.session_id)
        event_types = [e.event_type for e in events]

        assert "user_message" in event_types
        assert "tool_call_request" in event_types
        assert "tool_call_result" in event_types
        assert "assistant_message" in event_types

        # Tool call result should contain the resolved target info
        result_events = [e for e in events if e.event_type == "tool_call_result"]
        assert result_events[0].payload["success"] is True
        assert "demo-host-1" in result_events[0].payload["content"]


class TestE2ERunDiagnostic:
    async def test_ping_diagnostic(self, full_stack):
        """Run a ping diagnostic against demo host."""
        session = full_stack["session"]
        registry = full_stack["registry"]
        policy = full_stack["policy"]

        class DiagProvider(MockProvider):
            _turn = 0
            async def chat(self, messages, tools=None, stream=True, timeout=30.0):
                self._turn += 1
                if self._turn == 1:
                    inner = make_tool_call_provider(
                        "run_diagnostic",
                        {"action": "ping", "target": "demo-host-1"},
                    )
                    async for chunk in inner.chat(messages, tools, stream, timeout):
                        yield chunk
                else:
                    inner = make_text_provider(
                        "Ping successful. No packet loss detected."
                    )
                    async for chunk in inner.chat(messages, tools, stream, timeout):
                        yield chunk

        router = LLMRouter()
        router.register_provider("test", DiagProvider())

        orch = Orchestrator(
            session=session,
            registry=registry,
            router=router,
            policy=policy,
        )

        chunks = []
        async for chunk in orch.run("Ping demo-host-1"):
            chunks.append(chunk)

        events = await session.store.get_events(session.session_id)
        result_events = [e for e in events if e.event_type == "tool_call_result"]
        assert len(result_events) >= 1
        assert result_events[0].payload["success"] is True

        # Verify the diagnostic result contains ping data
        content = result_events[0].payload["content"]
        data = json.loads(content)
        assert "packets_sent" in data
        assert "rtt_avg_ms" in data


class TestE2EUnknownTarget:
    async def test_unknown_target_returns_error(self, full_stack):
        """Resolving an unknown target returns a backend error."""
        session = full_stack["session"]
        registry = full_stack["registry"]
        policy = full_stack["policy"]

        class ErrorProvider(MockProvider):
            _turn = 0
            async def chat(self, messages, tools=None, stream=True, timeout=30.0):
                self._turn += 1
                if self._turn == 1:
                    inner = make_tool_call_provider(
                        "resolve_target", {"target": "nonexistent-host"}
                    )
                    async for chunk in inner.chat(messages, tools, stream, timeout):
                        yield chunk
                else:
                    inner = make_text_provider("Target not found.")
                    async for chunk in inner.chat(messages, tools, stream, timeout):
                        yield chunk

        router = LLMRouter()
        router.register_provider("test", ErrorProvider())

        orch = Orchestrator(
            session=session,
            registry=registry,
            router=router,
            policy=policy,
        )

        chunks = []
        async for chunk in orch.run("Check nonexistent-host"):
            chunks.append(chunk)

        events = await session.store.get_events(session.session_id)
        result_events = [e for e in events if e.event_type == "tool_call_result"]
        assert len(result_events) >= 1
        assert result_events[0].payload["success"] is False
        assert "unknown target" in result_events[0].payload["content"].lower()


class TestE2EAuditTrail:
    async def test_audit_log_written(self, full_stack, tmp_path):
        """Audit log is written after tool execution."""
        session = full_stack["session"]
        registry = full_stack["registry"]
        policy = full_stack["policy"]

        class SimpleProvider(MockProvider):
            _turn = 0
            async def chat(self, messages, tools=None, stream=True, timeout=30.0):
                self._turn += 1
                if self._turn == 1:
                    inner = make_tool_call_provider(
                        "resolve_target", {"target": "demo-host-1"}
                    )
                    async for chunk in inner.chat(messages, tools, stream, timeout):
                        yield chunk
                else:
                    inner = make_text_provider("Done.")
                    async for chunk in inner.chat(messages, tools, stream, timeout):
                        yield chunk

        router = LLMRouter()
        router.register_provider("test", SimpleProvider())

        orch = Orchestrator(
            session=session,
            registry=registry,
            router=router,
            policy=policy,
        )

        await _collect(orch, "Check demo-host-1")

        # Verify audit log exists and has content
        assert policy.audit_path.exists()
        content = policy.audit_path.read_text()
        assert content.strip()
        record = json.loads(content.strip().split("\n")[0])
        assert record["tool_name"] == "resolve_target"
        assert record["success"] is True


async def _collect(orch, text):
    chunks = []
    async for c in orch.run(text):
        chunks.append(c)
    return chunks
