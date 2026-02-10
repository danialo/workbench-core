"""
Token counting with optional tiktoken backend.

If ``tiktoken`` is installed the counter delegates to its BPE encoder for the
requested model.  Otherwise a simple character-based heuristic is used
(~4 characters per token).
"""

from __future__ import annotations

import json
from typing import Any


class TokenCounter:
    """
    Estimate token counts for text and message lists.

    Parameters
    ----------
    model:
        Model name passed to ``tiktoken.encoding_for_model``.  Ignored when
        tiktoken is not available.
    """

    def __init__(self, model: str | None = None) -> None:
        self.model = model
        self._tiktoken_enc: Any = None
        try:
            import tiktoken  # type: ignore[import-untyped]

            self._tiktoken_enc = tiktoken.encoding_for_model(model or "gpt-4")
        except Exception:
            # tiktoken missing or model not recognised -- fall back silently.
            pass

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def count_text(self, text: str) -> int:
        """Return the estimated token count for a plain string."""
        if not text:
            return 0
        if self._tiktoken_enc is not None:
            return len(self._tiktoken_enc.encode(text))
        # Heuristic: roughly 4 characters per token for English text.
        return max(1, len(text) // 4)

    def count_messages(
        self,
        messages: list,
        tools: list[dict] | None = None,
    ) -> int:
        """
        Estimate the total token count for a conversation.

        Each message adds a small constant overhead (for role markers, etc.)
        plus the token count for content and any embedded tool calls.

        If *tools* are provided (OpenAI function-calling schema list) their
        JSON representation is counted as well -- the model "sees" them in the
        prompt.
        """
        total = 0
        for msg in messages:
            # Per-message overhead (role, separators, priming).
            total += 4

            content = getattr(msg, "content", None) or ""
            total += self.count_text(content)

            tool_calls = getattr(msg, "tool_calls", None)
            if tool_calls:
                for tc in tool_calls:
                    total += self.count_text(tc.name)
                    total += self.count_text(json.dumps(tc.arguments))

            # Tool-result messages include a tool_call_id.
            tool_call_id = getattr(msg, "tool_call_id", None)
            if tool_call_id:
                total += self.count_text(tool_call_id)

        if tools:
            total += self.count_text(json.dumps(tools))

        return total
