"""SSE streaming infrastructure for the web UI."""
from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass
from typing import Any, AsyncIterator

from workbench.llm.router import LLMRouter
from workbench.llm.token_counter import TokenCounter
from workbench.orchestrator.core import Orchestrator
from workbench.orchestrator.events import OrchestratorEvent
from workbench.session.artifacts import ArtifactStore
from workbench.session.session import Session
from workbench.session.store import SessionStore
from workbench.tools.policy import PolicyEngine
from workbench.tools.registry import ToolRegistry

logger = logging.getLogger(__name__)


class OrchestratorFactory:
    """Creates per-session Orchestrator instances with shared infrastructure."""

    def __init__(
        self,
        session_store: SessionStore,
        registry: ToolRegistry,
        router: LLMRouter,
        policy: PolicyEngine,
        artifact_store: ArtifactStore,
        token_counter: TokenCounter,
        system_prompt: str = "",
        tool_timeout: float = 30.0,
        max_turns: int = 20,
    ):
        self.session_store = session_store
        self.registry = registry
        self.router = router
        self.policy = policy
        self.artifact_store = artifact_store
        self.token_counter = token_counter
        self.system_prompt = system_prompt
        self.tool_timeout = tool_timeout
        self.max_turns = max_turns

    async def create(
        self,
        session_id: str,
        confirmation_callback=None,
        allowed_patterns: list[str] | None = None,
        context_prefix: str = "",
        registry: ToolRegistry | None = None,
    ) -> Orchestrator:
        """Create an Orchestrator wired to an existing session.

        Parameters
        ----------
        allowed_patterns : list[str] | None
            If provided, creates a scoped PolicyEngine copy with these
            patterns instead of using the shared policy's patterns.
            This keeps concurrent streams isolated.
        context_prefix : str
            If provided, prepended to the system prompt for this
            orchestrator instance (e.g. investigation context).
        registry : ToolRegistry | None
            If provided, overrides the shared registry (e.g. for
            recipe-filtered tool sets).
        """
        session = Session(
            store=self.session_store,
            artifact_store=self.artifact_store,
            token_counter=self.token_counter,
        )
        await session.resume(session_id)

        # Use a scoped policy if custom allowed_patterns are provided
        policy = self.policy
        if allowed_patterns is not None:
            policy = PolicyEngine(
                max_risk=self.policy.max_risk,
                confirm_destructive=self.policy.confirm_destructive,
                confirm_shell=self.policy.confirm_shell,
                confirm_write=self.policy.confirm_write,
                blocked_patterns=self.policy.blocked_patterns,
                allowed_patterns=allowed_patterns,
                redaction_patterns=[rx.pattern for rx in self.policy._redaction_patterns],
                audit_log_path=str(self.policy.audit_path),
                audit_max_size_mb=self.policy.audit_max_bytes // (1024 * 1024),
                audit_keep_files=self.policy.audit_keep_files,
            )

        effective_prompt = (context_prefix + self.system_prompt) if context_prefix else self.system_prompt

        effective_registry = registry or self.registry

        return Orchestrator(
            session=session,
            registry=effective_registry,
            router=self.router,
            policy=policy,
            system_prompt=effective_prompt,
            tool_timeout=self.tool_timeout,
            max_turns=self.max_turns,
            confirmation_callback=confirmation_callback,
        )


@dataclass
class PendingConfirmation:
    """A tool call waiting for user confirmation."""

    session_id: str
    tool_call_id: str
    tool_name: str
    tool_args: dict
    event: asyncio.Event
    confirmed: bool = False


class ConfirmationManager:
    """Manages pending tool call confirmations between SSE stream and REST endpoint."""

    def __init__(self, timeout: float = 120.0):
        self._pending: dict[tuple[str, str], PendingConfirmation] = {}
        self._timeout = timeout

    async def wait_for_confirmation(
        self,
        session_id: str,
        tool_call_id: str,
        tool_name: str,
        tool_args: dict,
    ) -> bool:
        """Block until the user confirms or denies (or timeout)."""
        key = (session_id, tool_call_id)
        pending = PendingConfirmation(
            session_id=session_id,
            tool_call_id=tool_call_id,
            tool_name=tool_name,
            tool_args=tool_args,
            event=asyncio.Event(),
        )
        self._pending[key] = pending

        try:
            await asyncio.wait_for(pending.event.wait(), timeout=self._timeout)
            return pending.confirmed
        except asyncio.TimeoutError:
            logger.warning("Confirmation timeout for %s:%s", session_id, tool_call_id)
            return False
        finally:
            self._pending.pop(key, None)

    def resolve(self, session_id: str, tool_call_id: str, confirmed: bool) -> bool:
        """Resolve a pending confirmation. Returns True if found."""
        key = (session_id, tool_call_id)
        pending = self._pending.get(key)
        if pending is None:
            return False
        pending.confirmed = confirmed
        pending.event.set()
        return True

    def has_pending(self, session_id: str, tool_call_id: str) -> bool:
        return (session_id, tool_call_id) in self._pending


async def sse_generator(
    events: AsyncIterator[OrchestratorEvent],
) -> AsyncIterator[str]:
    """Format OrchestratorEvents as SSE lines."""
    try:
        async for event in events:
            data = json.dumps(event.data, default=str)
            yield f"event: {event.type}\ndata: {data}\n\n"
    except Exception as e:
        error_data = json.dumps({"message": str(e)})
        yield f"event: error\ndata: {error_data}\n\n"
    finally:
        yield f"event: done\ndata: {{}}\n\n"
