"""
Orchestrator core -- the main loop that ties everything together.

The orchestrator:
1. Takes user input
2. Builds context window from session history
3. Sends to LLM via router with tool schemas
4. Processes tool calls through the full lifecycle
5. Loops until LLM responds with no tool calls (final response)
6. Supports streaming (yields chunks for UI)
"""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from typing import AsyncIterator, Awaitable, Callable

from workbench.llm.router import LLMRouter
from workbench.llm.types import AssembledAssistant, Message, StreamChunk, ToolCall
from workbench.session.events import (
    assistant_message_event,
    confirmation_event,
    protocol_error_event,
    tool_call_request_event,
    tool_call_result_event,
    user_message_event,
)
from workbench.session.session import Session
from workbench.tools.policy import PolicyEngine
from workbench.tools.registry import ToolRegistry
from workbench.tools.validation import ToolValidator
from workbench.types import ArtifactPayload, ErrorCode, ToolResult

logger = logging.getLogger(__name__)


class Orchestrator:
    """
    Main orchestrator loop.

    Parameters
    ----------
    session : Session
        Active session manager.
    registry : ToolRegistry
        Registered tools.
    router : LLMRouter
        LLM provider router.
    policy : PolicyEngine
        Policy enforcement engine.
    system_prompt : str
        System prompt for LLM calls.
    tool_timeout : float
        Max seconds for a single tool execution.
    max_turns : int
        Max tool-call rounds before forcing a text response.
    confirmation_callback : callable
        Async callback for tool confirmation. Receives (tool_name, tool_call)
        and returns True if confirmed.
    """

    def __init__(
        self,
        session: Session,
        registry: ToolRegistry,
        router: LLMRouter,
        policy: PolicyEngine,
        system_prompt: str = "",
        tool_timeout: float = 30.0,
        max_turns: int = 20,
        confirmation_callback: Callable[[str, ToolCall], Awaitable[bool]] | None = None,
    ) -> None:
        self.session = session
        self.registry = registry
        self.router = router
        self.policy = policy
        self.system_prompt = system_prompt
        self.tool_timeout = tool_timeout
        self.max_turns = max_turns
        self.confirmation_callback = confirmation_callback

    async def run(self, user_input: str) -> AsyncIterator[StreamChunk]:
        """
        Process a user message through the full orchestrator loop.

        Yields StreamChunk objects for UI rendering. The loop continues
        until the LLM produces a response with no tool calls, or max_turns
        is reached.
        """
        turn_id = self.session.new_turn()

        # Record user message event
        event = user_message_event(turn_id, user_input)
        await self.session.append_event(event)

        tools_schema = self.registry.to_openai_schema()

        for turn in range(self.max_turns):
            # Build context window
            messages, _report = await self.session.get_context_window(
                tools=tools_schema,
                system_prompt=self.system_prompt,
                max_context_tokens=self.router.active_provider.max_context_tokens,
                max_output_tokens=self.router.active_provider.max_output_tokens,
            )

            # Prepend system prompt
            if self.system_prompt:
                messages = [Message(role="system", content=self.system_prompt)] + messages

            # Get LLM response
            assembled = await self.router.chat_complete(
                messages=messages,
                tools=tools_schema if tools_schema else None,
                stream=True,
            )

            # Check for protocol errors (assembler failures)
            if assembled.metadata.get("assembler_errors"):
                errors = assembled.metadata["assembler_errors"]
                error_event = protocol_error_event(
                    turn_id,
                    "Tool call assembly failed",
                    details={"errors": errors},
                )
                await self.session.append_event(error_event)

                # Record assistant message with content only (no tool calls)
                if assembled.content:
                    assistant_event = assistant_message_event(
                        turn_id, assembled.content, model=assembled.model
                    )
                    await self.session.append_event(assistant_event)
                    yield StreamChunk(delta=assembled.content, done=True)
                else:
                    error_msg = "I encountered a protocol error processing tool calls. Please try rephrasing your request."
                    assistant_event = assistant_message_event(turn_id, error_msg)
                    await self.session.append_event(assistant_event)
                    yield StreamChunk(delta=error_msg, done=True)
                return

            # No tool calls -> final response
            if not assembled.tool_calls:
                if assembled.content:
                    assistant_event = assistant_message_event(
                        turn_id, assembled.content, model=assembled.model
                    )
                    await self.session.append_event(assistant_event)
                    yield StreamChunk(delta=assembled.content, done=True)
                return

            # Record assistant message (before tool calls)
            if assembled.content:
                assistant_event = assistant_message_event(
                    turn_id, assembled.content, model=assembled.model
                )
                await self.session.append_event(assistant_event)
                yield StreamChunk(delta=assembled.content)

            # Process each tool call through the lifecycle
            for tc in assembled.tool_calls:
                result = await self._execute_tool_call(turn_id, tc)

                # Build tool result message for context
                tool_result_content = result.content
                if not result.success and result.error:
                    tool_result_content = f"[Error: {result.error_code}] {result.error}"

                # Yield a chunk indicating tool result
                yield StreamChunk(
                    delta=f"\n[Tool: {tc.name}] {tool_result_content[:200]}\n"
                )

        # Max turns exceeded
        max_turns_msg = f"Reached maximum of {self.max_turns} tool call rounds. Please provide more specific guidance."
        assistant_event = assistant_message_event(turn_id, max_turns_msg)
        await self.session.append_event(assistant_event)
        yield StreamChunk(delta=max_turns_msg, done=True)

    async def _execute_tool_call(
        self, turn_id: str, tool_call: ToolCall
    ) -> ToolResult:
        """
        Execute a single tool call through the full lifecycle.

        Steps:
        1. Record request event
        2. Registry lookup
        3. Validate args
        4. Policy check
        5. Confirmation (if needed)
        6. Execute with timeout
        7. Store artifacts
        8. Audit log
        9. Record result event
        """
        # 1. Record request event
        request_event = tool_call_request_event(
            turn_id, tool_call.id, tool_call.name, tool_call.arguments
        )
        await self.session.append_event(request_event)

        # 2. Registry lookup
        tool = self.registry.get(tool_call.name)
        if tool is None:
            result = ToolResult(
                success=False,
                content=f"Unknown tool: {tool_call.name}",
                error=f"Unknown tool: {tool_call.name}",
                error_code=ErrorCode.UNKNOWN_TOOL,
            )
            result_event = tool_call_result_event(
                turn_id, tool_call.id, tool_call.name, result
            )
            await self.session.append_event(result_event)
            return result

        # 3. Validate args
        valid, error_msg = ToolValidator.validate(tool, tool_call.arguments)
        if not valid:
            result = ToolResult(
                success=False,
                content=f"Validation error: {error_msg}",
                error=error_msg,
                error_code=ErrorCode.VALIDATION_ERROR,
            )
            result_event = tool_call_result_event(
                turn_id, tool_call.id, tool_call.name, result
            )
            await self.session.append_event(result_event)
            return result

        # 4. Policy check
        decision = self.policy.check(tool, tool_call.arguments)
        if not decision.allowed:
            result = ToolResult(
                success=False,
                content=f"Policy blocked: {decision.reason}",
                error=decision.reason,
                error_code=ErrorCode.POLICY_BLOCK,
            )
            result_event = tool_call_result_event(
                turn_id, tool_call.id, tool_call.name, result
            )
            await self.session.append_event(result_event)
            return result

        # 5. Confirmation
        if decision.requires_confirmation:
            confirmed = False
            if self.confirmation_callback:
                confirmed = await self.confirmation_callback(tool_call.name, tool_call)

            confirm_ev = confirmation_event(
                turn_id, tool_call.id, tool_call.name, confirmed
            )
            await self.session.append_event(confirm_ev)

            if not confirmed:
                result = ToolResult(
                    success=False,
                    content="Tool call cancelled by user",
                    error="User declined confirmation",
                    error_code=ErrorCode.CANCELLED,
                )
                result_event = tool_call_result_event(
                    turn_id, tool_call.id, tool_call.name, result
                )
                await self.session.append_event(result_event)
                return result

        # 6. Execute with timeout
        start = time.monotonic()
        try:
            result = await asyncio.wait_for(
                tool.execute(**tool_call.arguments),
                timeout=self.tool_timeout,
            )
        except asyncio.TimeoutError:
            duration_ms = int((time.monotonic() - start) * 1000)
            result = ToolResult(
                success=False,
                content=f"Tool timed out after {self.tool_timeout}s",
                error=f"Timeout after {self.tool_timeout}s",
                error_code=ErrorCode.TIMEOUT,
            )
            result_event = tool_call_result_event(
                turn_id, tool_call.id, tool_call.name, result
            )
            await self.session.append_event(result_event)
            return result
        except Exception as e:
            duration_ms = int((time.monotonic() - start) * 1000)
            result = ToolResult(
                success=False,
                content=f"Tool exception: {e}",
                error=str(e),
                error_code=ErrorCode.TOOL_EXCEPTION,
            )
            result_event = tool_call_result_event(
                turn_id, tool_call.id, tool_call.name, result
            )
            await self.session.append_event(result_event)
            # Still audit even on exception
            try:
                await self.policy.audit_log(
                    session_id=self.session.session_id or "",
                    event_id=result_event.event_id,
                    tool=tool,
                    args=tool_call.arguments,
                    result=result,
                    duration_ms=duration_ms,
                    tool_call_id=tool_call.id,
                )
            except Exception:
                logger.exception("Audit log failed")
            return result

        duration_ms = int((time.monotonic() - start) * 1000)

        # 7. Store artifact payloads
        if result.artifact_payloads:
            for payload in result.artifact_payloads:
                ref = self.session.artifact_store.store(payload)
                result.artifacts.append(ref)
            result.artifact_payloads = []

        # 8. Audit log
        try:
            await self.policy.audit_log(
                session_id=self.session.session_id or "",
                event_id=request_event.event_id,
                tool=tool,
                args=tool_call.arguments,
                result=result,
                duration_ms=duration_ms,
                tool_call_id=tool_call.id,
            )
        except Exception:
            logger.exception("Audit log failed")

        # 9. Record result event
        result_event = tool_call_result_event(
            turn_id, tool_call.id, tool_call.name, result
        )
        await self.session.append_event(result_event)

        return result
