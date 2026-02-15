"""Orchestrator event types for streaming."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class OrchestratorEvent:
    """A typed event yielded by the streaming orchestrator."""

    type: str  # text_delta, tool_call_start, tool_call_result, confirmation_required, error, done
    data: dict[str, Any] = field(default_factory=dict)
