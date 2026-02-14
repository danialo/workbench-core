"""
FastAPI web server for the Agent Manager UI.

Provides REST endpoints for sessions, workspaces, inbox, tools, and config.
All tool execution is policy-gated and audited through the existing
orchestrator pipeline.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from workbench.web.middleware import (
    AuthMiddleware,
    CSRFMiddleware,
    RateLimitMiddleware,
    SecurityHeadersMiddleware,
)

logger = logging.getLogger(__name__)

# -----------------------------------------------------------------------
# Paths
# -----------------------------------------------------------------------
_WEB_DIR = Path(__file__).parent
_STATIC_DIR = _WEB_DIR / "static"

# -----------------------------------------------------------------------
# Pydantic models — validated inputs/outputs
# -----------------------------------------------------------------------


class CreateSessionRequest(BaseModel):
    """Request to create a new conversation session."""
    workspace: str = Field(default="playground", description="Target workspace/backend")
    metadata: dict[str, Any] = Field(default_factory=dict, description="Optional session metadata")


class SendMessageRequest(BaseModel):
    """Request to send a message in a conversation."""
    content: str = Field(..., min_length=1, max_length=32_000, description="Message content")


class SessionSummary(BaseModel):
    """Summary of a session for list views."""
    session_id: str
    created_at: str
    metadata: dict[str, Any] = Field(default_factory=dict)
    message_count: int = 0
    last_message: str = ""
    workspace: str = "playground"
    status: str = "active"


class SessionDetail(BaseModel):
    """Full session detail with events."""
    session_id: str
    created_at: str
    metadata: dict[str, Any] = Field(default_factory=dict)
    events: list[dict[str, Any]] = Field(default_factory=list)


class WorkspaceInfo(BaseModel):
    """Info about a registered workspace/backend."""
    name: str
    type: str  # "local" or "ssh"
    connected: bool = False
    host: str = ""
    port: int = 22
    username: str = ""


class ToolInfo(BaseModel):
    """Registered tool info."""
    name: str
    description: str
    risk_level: str
    parameters: dict[str, Any] = Field(default_factory=dict)


class MessageResponse(BaseModel):
    """Response after sending a message."""
    status: str = "received"
    session_id: str
    turn_id: str
    response: str = ""


# -----------------------------------------------------------------------
# App factory
# -----------------------------------------------------------------------

def create_app(
    *,
    config: Any = None,
    session_store: Any = None,
    registry: Any = None,
    backend_router: Any = None,
    policy: Any = None,
    orchestrator: Any = None,
    auth_token: str | None = None,
) -> FastAPI:
    """
    Build the FastAPI application with the full workbench stack wired in.

    Parameters
    ----------
    config : WorkbenchConfig
        Effective configuration.
    session_store : SessionStore
        SQLite session/event store.
    registry : ToolRegistry
        Registered tools.
    backend_router : BackendRouter
        Backend dispatcher (local + SSH targets).
    policy : PolicyEngine
        Tool execution policy.
    orchestrator : Orchestrator | None
        Orchestrator for running conversations (optional for UI-only mode).
    auth_token : str | None
        Bearer token for API authentication.  None = auth disabled.
    """

    app = FastAPI(
        title="Workbench Agent Manager",
        version="0.1.0",
        docs_url=None,   # Disable Swagger in production
        redoc_url=None,
    )

    # Store references for endpoint handlers
    app.state.config = config
    app.state.session_store = session_store
    app.state.registry = registry
    app.state.backend_router = backend_router
    app.state.policy = policy
    app.state.orchestrator = orchestrator

    # ---- Security middleware (order matters: outermost runs first) ----
    app.add_middleware(SecurityHeadersMiddleware)
    app.add_middleware(RateLimitMiddleware, max_requests=120, window_seconds=60)

    # CSRF: share a single secret between middleware and token endpoint
    import secrets as _secrets
    csrf_secret = _secrets.token_hex(32)
    app.add_middleware(CSRFMiddleware, secret=csrf_secret)
    app.add_middleware(AuthMiddleware, auth_token=auth_token)

    # Store secret for token generation in endpoint
    app.state.csrf_secret = csrf_secret

    # ---- Static files ----
    if _STATIC_DIR.exists():
        app.mount("/static", StaticFiles(directory=str(_STATIC_DIR)), name="static")

    # ------------------------------------------------------------------
    # Routes
    # ------------------------------------------------------------------

    @app.get("/", response_class=HTMLResponse)
    async def index():
        """Serve the main Agent Manager UI."""
        index_path = _STATIC_DIR / "index.html"
        if not index_path.exists():
            raise HTTPException(404, "index.html not found")
        return FileResponse(str(index_path), media_type="text/html")

    @app.get("/api/csrf-token")
    async def get_csrf_token():
        """Get a CSRF token for state-changing requests."""
        nonce = _secrets.token_hex(16)
        sig = hmac.new(
            app.state.csrf_secret.encode(), nonce.encode(), hashlib.sha256
        ).hexdigest()
        return {"csrf_token": f"{nonce}:{sig}"}

    # ---- Sessions ----

    @app.get("/api/sessions")
    async def list_sessions():
        """List all conversation sessions."""
        store = app.state.session_store
        if store is None:
            return {"sessions": []}

        sessions = await store.list_sessions()
        result = []
        for s in sessions:
            meta = {}
            if s.get("metadata"):
                try:
                    meta = json.loads(s["metadata"]) if isinstance(s["metadata"], str) else s["metadata"]
                except (json.JSONDecodeError, TypeError):
                    meta = {}

            # Count events for summary
            events = await store.get_events(s["session_id"])
            user_messages = [e for e in events if e.event_type == "user_message"]
            last_msg = ""
            if user_messages:
                last_msg = user_messages[-1].payload.get("content", "")[:100]

            result.append(SessionSummary(
                session_id=s["session_id"],
                created_at=s.get("created_at", ""),
                metadata=meta,
                message_count=len(user_messages),
                last_message=last_msg,
                workspace=meta.get("workspace", "playground"),
                status=meta.get("status", "active"),
            ).model_dump())

        return {"sessions": result}

    @app.post("/api/sessions", status_code=201)
    async def create_session(req: CreateSessionRequest):
        """Create a new conversation session."""
        store = app.state.session_store
        if store is None:
            raise HTTPException(503, "Session store not available")

        metadata = {**req.metadata, "workspace": req.workspace}
        session_id = await store.create_session(metadata)
        logger.info("Created session %s for workspace %s", session_id, req.workspace)
        return {"session_id": session_id, "workspace": req.workspace}

    @app.get("/api/sessions/{session_id}")
    async def get_session(session_id: str):
        """Get session details with events."""
        store = app.state.session_store
        if store is None:
            raise HTTPException(503, "Session store not available")

        info = await store.get_session(session_id)
        if info is None:
            raise HTTPException(404, f"Session not found: {session_id}")

        events = await store.get_events(session_id)
        event_dicts = [e.to_dict() for e in events]

        meta = {}
        if info.get("metadata"):
            try:
                meta = json.loads(info["metadata"]) if isinstance(info["metadata"], str) else info["metadata"]
            except (json.JSONDecodeError, TypeError):
                meta = {}

        return SessionDetail(
            session_id=session_id,
            created_at=info.get("created_at", ""),
            metadata=meta,
            events=event_dicts,
        ).model_dump()

    @app.delete("/api/sessions/{session_id}")
    async def delete_session(session_id: str):
        """Delete a session and all its events."""
        store = app.state.session_store
        if store is None:
            raise HTTPException(503, "Session store not available")

        info = await store.get_session(session_id)
        if info is None:
            raise HTTPException(404, f"Session not found: {session_id}")

        await store.delete_session(session_id)
        logger.info("Deleted session %s", session_id)
        return {"status": "deleted", "session_id": session_id}

    @app.post("/api/sessions/{session_id}/messages")
    async def send_message(session_id: str, req: SendMessageRequest):
        """
        Send a message in a conversation.

        If an orchestrator is wired up, runs the full pipeline.
        Otherwise, stores the user message and returns a placeholder.
        """
        store = app.state.session_store
        if store is None:
            raise HTTPException(503, "Session store not available")

        info = await store.get_session(session_id)
        if info is None:
            raise HTTPException(404, f"Session not found: {session_id}")

        # Import here to avoid circular deps
        from workbench.session.events import user_message_event, assistant_message_event
        import uuid

        turn_id = str(uuid.uuid4())

        # Store user message
        user_event = user_message_event(turn_id=turn_id, content=req.content)
        await store.append_event(session_id, user_event)

        # If orchestrator is available, run the full pipeline
        response_text = ""
        orch = app.state.orchestrator
        if orch is not None:
            try:
                chunks = []
                async for chunk in orch.run(req.content):
                    if hasattr(chunk, "text") and chunk.text:
                        chunks.append(chunk.text)
                response_text = "".join(chunks)
            except Exception as e:
                logger.error("Orchestrator error: %s", e)
                response_text = f"[Error] {e}"
        else:
            response_text = (
                "Agent Manager is running in UI-only mode. "
                "Connect an LLM provider to enable AI responses."
            )

        # Store assistant response
        assistant_event = assistant_message_event(
            turn_id=turn_id,
            content=response_text,
        )
        await store.append_event(session_id, assistant_event)

        return MessageResponse(
            status="completed",
            session_id=session_id,
            turn_id=turn_id,
            response=response_text,
        ).model_dump()

    # ---- Config ----

    @app.get("/api/config")
    async def get_config():
        """
        Return effective config with secrets sanitised.

        API keys are stripped — only their env var names are exposed.
        """
        cfg = app.state.config
        if cfg is None:
            return {"config": {}}

        d = cfg.to_dict()

        # Sanitise: remove actual API key values, keep env var name
        if "llm" in d:
            d["llm"].pop("api_key", None)
            # Keep api_key_env so the UI knows which env var is configured

        return {"config": d}

    # ---- Workspaces ----

    @app.get("/api/workspaces")
    async def list_workspaces():
        """
        List all available workspaces/backends.

        Includes local backend and all registered SSH targets.
        """
        workspaces: list[dict] = []

        # Always include local
        workspaces.append(WorkspaceInfo(
            name="local",
            type="local",
            connected=True,
        ).model_dump())

        # SSH targets from backend router
        router = app.state.backend_router
        if router is not None:
            for target in router.targets:
                if target not in ("localhost", "local", "127.0.0.1"):
                    workspaces.append(WorkspaceInfo(
                        name=target,
                        type="ssh",
                        connected=False,
                    ).model_dump())

        # SSH hosts from config
        cfg = app.state.config
        if cfg is not None and hasattr(cfg, "backends"):
            for host_cfg in cfg.backends.ssh_hosts:
                name = host_cfg.get("name", host_cfg.get("host", ""))
                # Don't duplicate if already in router
                if name and not any(w["name"] == name for w in workspaces):
                    workspaces.append(WorkspaceInfo(
                        name=name,
                        type="ssh",
                        host=host_cfg.get("host", ""),
                        port=host_cfg.get("port", 22),
                        username=host_cfg.get("username", ""),
                    ).model_dump())

        return {"workspaces": workspaces}

    @app.post("/api/workspaces/{target}/connect")
    async def connect_workspace(target: str):
        """Test connectivity to an SSH workspace."""
        router = app.state.backend_router
        if router is None:
            raise HTTPException(503, "Backend router not available")

        try:
            result = await router.resolve_target(target)
            return {"status": "connected", "target": target, "info": result}
        except Exception as e:
            logger.error("Connection to %s failed: %s", target, e)
            raise HTTPException(502, f"Connection failed: {e}")

    # ---- Inbox ----

    @app.get("/api/inbox")
    async def list_inbox():
        """
        List completed/returned conversations.

        An inbox item is a session marked with status 'completed'
        or 'returned' in its metadata.
        """
        store = app.state.session_store
        if store is None:
            return {"items": []}

        sessions = await store.list_sessions()
        items = []
        for s in sessions:
            meta = {}
            if s.get("metadata"):
                try:
                    meta = json.loads(s["metadata"]) if isinstance(s["metadata"], str) else s["metadata"]
                except (json.JSONDecodeError, TypeError):
                    meta = {}

            status = meta.get("status", "active")
            if status in ("completed", "returned"):
                events = await store.get_events(s["session_id"])
                user_messages = [e for e in events if e.event_type == "user_message"]
                items.append({
                    "session_id": s["session_id"],
                    "created_at": s.get("created_at", ""),
                    "status": status,
                    "workspace": meta.get("workspace", "playground"),
                    "message_count": len(user_messages),
                    "last_message": user_messages[-1].payload.get("content", "")[:100] if user_messages else "",
                    "metadata": meta,
                })

        return {"items": items}

    @app.get("/api/inbox/search")
    async def search_inbox(q: str = Query("", min_length=0, max_length=500)):
        """Search inbox items by message content."""
        store = app.state.session_store
        if store is None:
            return {"items": [], "query": q}

        if not q.strip():
            # Return all inbox items if no query
            inbox = await list_inbox()
            return {**inbox, "query": q}

        sessions = await store.list_sessions()
        items = []
        q_lower = q.lower()

        for s in sessions:
            meta = {}
            if s.get("metadata"):
                try:
                    meta = json.loads(s["metadata"]) if isinstance(s["metadata"], str) else s["metadata"]
                except (json.JSONDecodeError, TypeError):
                    meta = {}

            events = await store.get_events(s["session_id"])
            # Search across all message content
            match = False
            for event in events:
                content = event.payload.get("content", "")
                if q_lower in content.lower():
                    match = True
                    break

            if match:
                user_messages = [e for e in events if e.event_type == "user_message"]
                items.append({
                    "session_id": s["session_id"],
                    "created_at": s.get("created_at", ""),
                    "status": meta.get("status", "active"),
                    "workspace": meta.get("workspace", "playground"),
                    "message_count": len(user_messages),
                    "last_message": user_messages[-1].payload.get("content", "")[:100] if user_messages else "",
                    "metadata": meta,
                })

        return {"items": items, "query": q}

    # ---- Tools ----

    @app.get("/api/tools")
    async def list_tools():
        """List all registered tools."""
        reg = app.state.registry
        if reg is None:
            return {"tools": []}

        tools = []
        for tool in reg.all_tools():
            tools.append(ToolInfo(
                name=tool.name,
                description=tool.description,
                risk_level=tool.risk_level.name,
                parameters=tool.parameters,
            ).model_dump())

        return {"tools": tools}

    return app
