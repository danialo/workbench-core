"""Agent HUD endpoints — list active agents, SSE stream of state changes."""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from starlette.responses import StreamingResponse

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/agents", tags=["agents"])


# -----------------------------------------------------------------------
# Agent Registry — tracks active agents across sessions
# -----------------------------------------------------------------------

class AgentRegistry:
    """Tracks active agent sessions for the HUD overlay."""

    def __init__(self):
        self._agents: dict[str, dict[str, Any]] = {}
        self._subscribers: list[asyncio.Queue] = []

    def register(self, session_id: str, workspace_id: str, workspace_name: str) -> None:
        """Register an agent when streaming starts."""
        now = datetime.now(timezone.utc).isoformat()
        self._agents[session_id] = {
            "session_id": session_id,
            "workspace_id": workspace_id,
            "workspace_name": workspace_name,
            "status": "running",
            "current_action": None,
            "started_at": now,
            "last_update": now,
            "pending_confirmation": None,
        }
        self._notify(self._agents[session_id])

    def update(self, session_id: str, **kwargs) -> None:
        """Update agent state."""
        agent = self._agents.get(session_id)
        if agent is None:
            return
        agent.update(kwargs)
        agent["last_update"] = datetime.now(timezone.utc).isoformat()
        self._notify(agent)

    def unregister(self, session_id: str, status: str = "completed") -> None:
        """Mark agent as completed/errored."""
        agent = self._agents.get(session_id)
        if agent is None:
            return
        agent["status"] = status
        agent["last_update"] = datetime.now(timezone.utc).isoformat()
        agent["pending_confirmation"] = None
        self._notify(agent)

    def list_agents(self) -> list[dict]:
        return list(self._agents.values())

    def subscribe(self) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue()
        self._subscribers.append(q)
        return q

    def unsubscribe(self, q: asyncio.Queue) -> None:
        try:
            self._subscribers.remove(q)
        except ValueError:
            pass

    def _notify(self, data: dict) -> None:
        for q in self._subscribers:
            try:
                q.put_nowait(data)
            except asyncio.QueueFull:
                pass


# -----------------------------------------------------------------------
# Endpoints
# -----------------------------------------------------------------------

@router.get("")
async def list_agents(request: Request):
    """List all tracked agents."""
    registry: AgentRegistry = request.app.state.agent_registry
    return JSONResponse({"agents": registry.list_agents()})


@router.get("/stream")
async def stream_agents(request: Request):
    """SSE stream of agent state changes."""
    registry: AgentRegistry = request.app.state.agent_registry
    q = registry.subscribe()

    async def event_generator():
        try:
            while True:
                if await request.is_disconnected():
                    break
                try:
                    data = await asyncio.wait_for(q.get(), timeout=30.0)
                    yield f"event: agent_update\ndata: {json.dumps(data, default=str)}\n\n"
                except asyncio.TimeoutError:
                    yield ": keepalive\n\n"
        finally:
            registry.unsubscribe(q)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
