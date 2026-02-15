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
from workbench.workspace import WorkspaceManager, GLOBAL_WORKSPACE_ID

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
    workspace_id: str = Field(default="", description="Workspace ID to scope session to")
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


class CreateWorkspaceRequest(BaseModel):
    """Request to create a new project workspace."""
    name: str = Field(..., min_length=1, max_length=100)
    path: str = Field(default="", description="Directory scope")
    backend: str = Field(default="local", description="Backend target")
    config_overrides: dict[str, Any] = Field(default_factory=dict)
    tools_enabled: list[str] = Field(default_factory=list)
    tools_disabled: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


class UpdateWorkspaceRequest(BaseModel):
    """Request to update a workspace."""
    name: str | None = None
    path: str | None = None
    backend: str | None = None
    config_overrides: dict[str, Any] | None = None
    tools_enabled: list[str] | None = None
    tools_disabled: list[str] | None = None
    metadata: dict[str, Any] | None = None


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
    workspace_manager: WorkspaceManager | None = None,
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

    # Workspace manager (JSON persistence)
    if workspace_manager is None:
        workspace_manager = WorkspaceManager()
        workspace_manager.load()
    app.state.workspace_manager = workspace_manager

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

        ws_id = req.workspace_id or GLOBAL_WORKSPACE_ID
        metadata = {**req.metadata, "workspace": req.workspace, "workspace_id": ws_id}
        session_id = await store.create_session(metadata)
        logger.info("Created session %s for workspace %s", session_id, ws_id)
        return {"session_id": session_id, "workspace": req.workspace, "workspace_id": ws_id}

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
        """List all workspaces (global + projects)."""
        mgr: WorkspaceManager = app.state.workspace_manager
        workspaces = [ws.to_dict() for ws in mgr.list_all()]
        return {"workspaces": workspaces}

    @app.post("/api/workspaces", status_code=201)
    async def create_workspace(req: CreateWorkspaceRequest):
        """Create a new project workspace."""
        mgr: WorkspaceManager = app.state.workspace_manager
        ws = mgr.create(
            name=req.name,
            path=req.path,
            backend=req.backend,
            config_overrides=req.config_overrides,
            tools_enabled=req.tools_enabled,
            tools_disabled=req.tools_disabled,
            metadata=req.metadata,
        )
        return ws.to_dict()

    @app.get("/api/workspaces/{workspace_id}")
    async def get_workspace(workspace_id: str):
        """Get workspace detail."""
        mgr: WorkspaceManager = app.state.workspace_manager
        ws = mgr.get(workspace_id)
        if ws is None:
            raise HTTPException(404, f"Workspace not found: {workspace_id}")
        return ws.to_dict()

    @app.put("/api/workspaces/{workspace_id}")
    async def update_workspace(workspace_id: str, req: UpdateWorkspaceRequest):
        """Update a workspace's configuration."""
        mgr: WorkspaceManager = app.state.workspace_manager
        fields = {k: v for k, v in req.model_dump().items() if v is not None}
        ws = mgr.update(workspace_id, **fields)
        if ws is None:
            raise HTTPException(404, f"Workspace not found: {workspace_id}")
        return ws.to_dict()

    @app.delete("/api/workspaces/{workspace_id}")
    async def delete_workspace(workspace_id: str):
        """Delete a project workspace. Cannot delete global."""
        mgr: WorkspaceManager = app.state.workspace_manager
        if workspace_id == GLOBAL_WORKSPACE_ID:
            raise HTTPException(400, "Cannot delete global workspace")
        if not mgr.delete(workspace_id):
            raise HTTPException(404, f"Workspace not found: {workspace_id}")
        return {"status": "deleted", "workspace_id": workspace_id}

    @app.post("/api/workspaces/{workspace_id}/open")
    async def open_workspace(workspace_id: str):
        """Mark workspace as active (updates last_opened)."""
        mgr: WorkspaceManager = app.state.workspace_manager
        ws = mgr.open_workspace(workspace_id)
        if ws is None:
            raise HTTPException(404, f"Workspace not found: {workspace_id}")
        return ws.to_dict()

    @app.get("/api/workspaces/{workspace_id}/sessions")
    async def list_workspace_sessions(workspace_id: str):
        """List sessions scoped to a workspace."""
        store = app.state.session_store
        if store is None:
            return {"sessions": [], "workspace_id": workspace_id}

        sessions = await store.list_sessions()
        result = []
        for s in sessions:
            meta = {}
            if s.get("metadata"):
                try:
                    meta = json.loads(s["metadata"]) if isinstance(s["metadata"], str) else s["metadata"]
                except (json.JSONDecodeError, TypeError):
                    meta = {}

            # Filter by workspace_id
            session_ws = meta.get("workspace_id", GLOBAL_WORKSPACE_ID)
            if session_ws != workspace_id:
                continue

            events = await store.get_events(s["session_id"])
            user_messages = [e for e in events if e.event_type == "user_message"]
            last_msg = user_messages[-1].payload.get("content", "")[:100] if user_messages else ""

            result.append(SessionSummary(
                session_id=s["session_id"],
                created_at=s.get("created_at", ""),
                metadata=meta,
                message_count=len(user_messages),
                last_message=last_msg,
                workspace=meta.get("workspace", "playground"),
                status=meta.get("status", "active"),
            ).model_dump())

        return {"sessions": result, "workspace_id": workspace_id}

    @app.get("/api/workspaces/{workspace_id}/config")
    async def get_workspace_config(workspace_id: str):
        """Get effective config for a workspace (global merged with project overrides)."""
        mgr: WorkspaceManager = app.state.workspace_manager
        config = mgr.get_effective_config(workspace_id)
        return {"config": config, "workspace_id": workspace_id}

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

    # ---- File Browser ----

    @app.get("/api/browse")
    async def browse_directory(path: str = Query("~", description="Directory to browse")):
        """
        List subdirectories for the workspace directory picker.

        Returns the resolved path and a list of child directories.
        """
        import os
        try:
            resolved = str(Path(path).expanduser().resolve())
            if not Path(resolved).is_dir():
                raise HTTPException(400, f"Not a directory: {path}")

            entries = []
            try:
                for entry in sorted(Path(resolved).iterdir()):
                    if entry.is_dir() and not entry.name.startswith('.'):
                        entries.append({
                            "name": entry.name,
                            "path": str(entry),
                            "has_children": any(
                                c.is_dir() for c in entry.iterdir()
                                if not c.name.startswith('.')
                            ) if os.access(str(entry), os.R_OK) else False,
                        })
            except PermissionError:
                pass  # Skip unreadable dirs

            # Parent path for "go up"
            parent = str(Path(resolved).parent)

            return {
                "path": resolved,
                "parent": parent if parent != resolved else None,
                "entries": entries,
            }
        except Exception as e:
            raise HTTPException(400, f"Browse error: {e}")

    @app.post("/api/browse/mkdir")
    async def make_directory(request: Request):
        """Create a new subdirectory inside a given parent path."""
        body = await request.json()
        parent = body.get("parent", "")
        name = body.get("name", "").strip()

        if not parent or not name:
            raise HTTPException(400, "parent and name are required")

        # Basic safety: no path separators or special names
        if "/" in name or "\\" in name or name in (".", ".."):
            raise HTTPException(400, "Invalid folder name")

        target = Path(parent).expanduser().resolve() / name
        if target.exists():
            raise HTTPException(409, f"Already exists: {name}")

        try:
            target.mkdir(parents=False, exist_ok=False)
            return {"path": str(target), "name": name}
        except Exception as e:
            raise HTTPException(400, f"Failed to create folder: {e}")

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
