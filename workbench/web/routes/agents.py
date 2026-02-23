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
        self._tasks: dict[str, asyncio.Task] = {}  # background task handles (not serialized)
        self._subscribers: list[asyncio.Queue] = []

    _MAX_HISTORY = 20

    def register(self, session_id: str, workspace_id: str, workspace_name: str, label: str | None = None) -> None:
        """Register an agent when streaming starts."""
        now = datetime.now(timezone.utc).isoformat()
        self._agents[session_id] = {
            "session_id": session_id,
            "workspace_id": workspace_id,
            "workspace_name": workspace_name,
            "label": label,
            "status": "running",
            "current_action": None,
            "action_history": [],
            "started_at": now,
            "last_update": now,
            "pending_confirmation": None,
        }
        self._notify(self._agents[session_id])

    def update(self, session_id: str, **kwargs) -> None:
        """Update agent state, accumulating action_history."""
        agent = self._agents.get(session_id)
        if agent is None:
            return
        # Accumulate action history when current_action changes to a non-None value
        new_action = kwargs.get("current_action")
        if new_action and new_action != agent.get("current_action"):
            history = agent.setdefault("action_history", [])
            history.append(new_action)
            if len(history) > self._MAX_HISTORY:
                history[:] = history[-self._MAX_HISTORY:]
        agent.update(kwargs)
        agent["last_update"] = datetime.now(timezone.utc).isoformat()
        self._notify(agent)

    def attach_task(self, session_id: str, task: asyncio.Task) -> None:
        """Attach a background asyncio.Task so it can be cancelled later."""
        self._tasks[session_id] = task

    def cancel(self, session_id: str) -> bool:
        """Cancel a background task. Returns True if a task was found and cancelled."""
        task = self._tasks.pop(session_id, None)
        if task and not task.done():
            task.cancel()
            return True
        return False

    def unregister(self, session_id: str, status: str = "completed") -> None:
        """Mark agent as completed/errored (keeps entry in registry)."""
        self._tasks.pop(session_id, None)
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

@router.post("/{session_id}/stop")
async def stop_agent(session_id: str, request: Request):
    """Cancel a running background agent task."""
    registry: AgentRegistry = request.app.state.agent_registry
    cancelled = registry.cancel(session_id)
    # Mark stopped in registry regardless (task may already be done)
    registry.unregister(session_id, status="stopped")
    return JSONResponse({"cancelled": cancelled, "session_id": session_id})


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
