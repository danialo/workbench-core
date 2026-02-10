"""
Token-budgeted context window packer.

Given a list of messages, an optional tool schema, and a system prompt,
:class:`ContextPacker` trims the conversation to fit within a target
context window while preserving the most recent messages and any system
messages.

The trimming strategy is:

1.  Compute the *budget* (tokens available for conversation messages).
2.  Walk backwards from the most recent message, accumulating token
    counts.
3.  When the budget is exhausted, drop all older non-system messages.
4.  System messages at any position are always retained.
5.  Return the packed message list together with a
    :class:`~workbench.types.ContextPackReport`.
"""

from __future__ import annotations

import json
from typing import Any

from workbench.llm.types import Message
from workbench.types import ContextPackReport


class ContextPacker:
    """
    Pack a conversation into a token budget.

    Parameters
    ----------
    token_counter:
        Any object exposing ``count_text(str) -> int`` and
        ``count_messages(list[Message]) -> int``.  The
        ``workbench.llm.token_counter.TokenCounter`` class satisfies this
        interface.
    """

    def __init__(self, token_counter: Any) -> None:
        self.token_counter = token_counter

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _message_tokens(self, msg: Message) -> int:
        """Estimate the token cost of a single message."""
        # Per-message overhead (role, separators, priming) -- same constant
        # used by TokenCounter.count_messages.
        overhead = 4
        tokens = overhead + self.token_counter.count_text(msg.content or "")

        if msg.tool_calls:
            for tc in msg.tool_calls:
                tokens += self.token_counter.count_text(tc.name)
                tokens += self.token_counter.count_text(json.dumps(tc.arguments))

        if msg.tool_call_id:
            tokens += self.token_counter.count_text(msg.tool_call_id)

        return tokens

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def pack(
        self,
        messages: list[Message],
        tools: list[dict] | None,
        system_prompt: str,
        max_context_tokens: int,
        max_output_tokens: int,
        reserve_tokens: int = 200,
    ) -> tuple[list[Message], ContextPackReport]:
        """
        Fit *messages* into the available token budget.

        Returns
        -------
        tuple[list[Message], ContextPackReport]
            The trimmed message list and a report describing what was kept
            and dropped.
        """
        # -- Fixed costs ------------------------------------------------
        tool_schema_tokens = (
            self.token_counter.count_text(json.dumps(tools)) if tools else 0
        )
        system_prompt_tokens = self.token_counter.count_text(system_prompt)

        budget = (
            max_context_tokens
            - max_output_tokens
            - reserve_tokens
            - tool_schema_tokens
            - system_prompt_tokens
        )

        if budget < 0:
            budget = 0

        # -- Separate system messages -----------------------------------
        # System messages are always kept (they are cheap and essential).
        # We compute their cost upfront and subtract from the budget.
        system_indices: set[int] = set()
        system_tokens = 0
        for idx, msg in enumerate(messages):
            if msg.role == "system":
                system_indices.add(idx)
                system_tokens += self._message_tokens(msg)

        remaining_budget = budget - system_tokens
        if remaining_budget < 0:
            remaining_budget = 0

        # -- Walk backwards, keeping most-recent first ------------------
        non_system: list[tuple[int, Message]] = [
            (idx, msg)
            for idx, msg in enumerate(messages)
            if idx not in system_indices
        ]

        kept_indices: set[int] = set(system_indices)
        running_tokens = 0

        for idx, msg in reversed(non_system):
            cost = self._message_tokens(msg)
            if running_tokens + cost <= remaining_budget:
                running_tokens += cost
                kept_indices.add(idx)
            # Once we exceed the budget we stop adding older messages but
            # continue the loop so we count dropped messages correctly.

        # Preserve original ordering.
        kept_messages = [
            messages[i] for i in sorted(kept_indices)
        ]

        message_tokens = system_tokens + running_tokens
        kept_count = len(kept_messages)
        dropped_count = len(messages) - kept_count

        report = ContextPackReport(
            max_context_tokens=max_context_tokens,
            max_output_tokens=max_output_tokens,
            reserve_tokens=reserve_tokens,
            tool_schema_tokens=tool_schema_tokens,
            system_prompt_tokens=system_prompt_tokens,
            message_tokens=message_tokens,
            kept_messages=kept_count,
            dropped_messages=dropped_count,
        )

        return kept_messages, report
