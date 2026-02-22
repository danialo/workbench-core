"""
FastAPI web server for the Agent Manager UI.

Provides REST endpoints for sessions, workspaces, inbox, tools, and config.
All tool execution is policy-gated and audited through the existing
orchestrator pipeline.
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import logging
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from starlette.responses import StreamingResponse
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


from workbench.web.routes.agents import AgentRegistry, router as agents_router
from workbench.web.routes.investigations import router as investigations_router


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
    llm_router: Any = None,
    token_counter: Any = None,
    artifact_store: Any = None,
    system_prompt: str = "",
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

    # --- Memory providers ---
    from workbench.memory.sqlite_provider import SQLiteMemoryProvider
    from workbench.memory.file_provider import FileMemoryProvider

    db_path = str(Path(config.session.history_db).expanduser()) if config else "~/.workbench/history.db"
    memory_provider = SQLiteMemoryProvider(db_path)
    file_memory_provider = FileMemoryProvider()
    app.state.memory_provider = memory_provider
    app.state.file_memory_provider = file_memory_provider

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

    # ---- Streaming infrastructure ----
    confirmation_manager = None
    orchestrator_factory = None

    if llm_router is not None:
        from workbench.web.streaming import OrchestratorFactory, ConfirmationManager

        confirmation_manager = ConfirmationManager()
        orchestrator_factory = OrchestratorFactory(
            session_store=session_store,
            registry=registry,
            router=llm_router,
            policy=policy,
            artifact_store=artifact_store,
            token_counter=token_counter,
            system_prompt=system_prompt,
        )

    app.state.orchestrator_factory = orchestrator_factory
    app.state.confirmation_manager = confirmation_manager

    # ---- Memory tools ----
    if registry is not None:
        from workbench.memory.tools import MemoryReadTool, MemoryWriteTool
        registry.register(MemoryReadTool(memory_provider, GLOBAL_WORKSPACE_ID))
        registry.register(MemoryWriteTool(memory_provider, GLOBAL_WORKSPACE_ID))

    # ---- Agent Registry ----
    agent_registry = AgentRegistry()
    app.state.agent_registry = agent_registry

    # ---- Investigations DB path (used by investigations router) ----
    app.state.investigations_db_path = str(Path(db_path).expanduser())

    # ---- Include feature routers ----
    app.include_router(agents_router)
    app.include_router(investigations_router)

    from workbench.web.routes.context import router as context_router, ensure_context_pills_table
    app.include_router(context_router)

    from workbench.web.routes.recipes import router as recipes_router
    app.include_router(recipes_router)

    # ---- Recipe Registry ----
    from workbench.recipes.registry import RecipeRegistry
    recipe_registry = RecipeRegistry()
    recipe_registry.discover_global()
    app.state.recipe_registry = recipe_registry

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
        for tool in reg.list():
            tools.append(ToolInfo(
                name=tool.name,
                description=tool.description,
                risk_level=tool.risk_level.name,
                parameters=tool.parameters,
            ).model_dump())

        return {"tools": tools}

    # ---- Streaming & LLM endpoints ----

    @app.post("/api/sessions/{session_id}/stream")
    async def stream_message(session_id: str, request: Request):
        """Stream LLM response via SSE."""
        if app.state.orchestrator_factory is None:
            return JSONResponse(
                {"error": "LLM not configured. Running in UI-only mode."},
                status_code=503,
            )

        body = await request.json()
        content = body.get("content", "").strip()
        if not content:
            return JSONResponse({"error": "Empty message"}, status_code=400)

        # Verify session exists and resolve workspace
        session_meta = await session_store.get_session(session_id)
        if session_meta is None:
            return JSONResponse({"error": "Session not found"}, status_code=404)

        # Resolve workspace-scoped allowlist for this session
        ws_id = GLOBAL_WORKSPACE_ID
        if session_meta.get("metadata"):
            try:
                meta = json.loads(session_meta["metadata"]) if isinstance(session_meta["metadata"], str) else session_meta["metadata"]
                ws_id = meta.get("workspace_id", GLOBAL_WORKSPACE_ID)
            except (json.JSONDecodeError, TypeError):
                pass
        effective_patterns = await _resolve_effective_allowlist(ws_id)

        # Look up investigation context if this session is linked to one
        context_prefix = ""
        if hasattr(app.state, "investigations_db_path"):
            from workbench.web.routes.investigations import get_investigation_context_for_session
            context_prefix = await get_investigation_context_for_session(
                app.state.investigations_db_path, session_id
            )

        # Inject workspace context pills
        if hasattr(app.state, "investigations_db_path"):
            from workbench.web.routes.context import build_context_pills_prefix
            pills_ctx = await build_context_pills_prefix(
                app.state.investigations_db_path, ws_id
            )
            if pills_ctx:
                context_prefix = pills_ctx + context_prefix

        cm = app.state.confirmation_manager

        async def confirmation_callback(tool_name: str, tool_call: Any) -> bool:
            return await cm.wait_for_confirmation(
                session_id=session_id,
                tool_call_id=tool_call.id,
                tool_name=tool_name,
                tool_args=tool_call.arguments,
            )

        orch = await app.state.orchestrator_factory.create(
            session_id=session_id,
            confirmation_callback=confirmation_callback,
            allowed_patterns=effective_patterns,
            context_prefix=context_prefix,
        )

        from workbench.web.streaming import sse_generator
        events = orch.run_streaming(content)

        # Resolve workspace name for HUD display
        ws_name = "Playground"
        if ws_id != GLOBAL_WORKSPACE_ID:
            ws_obj = workspace_manager.get(ws_id)
            if ws_obj:
                ws_name = ws_obj.name

        # Register agent in HUD
        agent_registry.register(session_id, ws_id, ws_name)

        async def tracked_sse_generator():
            """Wrap SSE generator to update agent registry on key events."""
            try:
                async for event in events:
                    data = json.dumps(event.data, default=str)

                    # Update agent state based on event type
                    if event.type == "tool_call_start":
                        tool_name = event.data.get("name", "")
                        agent_registry.update(
                            session_id,
                            status="running",
                            current_action=f"Calling {tool_name}",
                        )
                    elif event.type == "confirmation_required":
                        agent_registry.update(
                            session_id,
                            status="waiting",
                            current_action=f"Awaiting: {event.data.get('tool_name', '')}",
                            pending_confirmation={
                                "tool_call_id": event.data.get("tool_call_id", ""),
                                "tool_name": event.data.get("tool_name", ""),
                            },
                        )
                    elif event.type == "tool_call_result":
                        agent_registry.update(
                            session_id,
                            status="running",
                            current_action=None,
                            pending_confirmation=None,
                        )
                    elif event.type == "text_delta":
                        agent_registry.update(
                            session_id,
                            status="running",
                            current_action="Generating response",
                        )

                    yield f"event: {event.type}\ndata: {data}\n\n"
            except Exception as e:
                agent_registry.unregister(session_id, status="error")
                error_data = json.dumps({"message": str(e)})
                yield f"event: error\ndata: {error_data}\n\n"
            else:
                agent_registry.unregister(session_id, status="completed")
            finally:
                yield f"event: done\ndata: {{}}\n\n"

        return StreamingResponse(
            tracked_sse_generator(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
            },
        )

    @app.post("/api/sessions/{session_id}/confirm")
    async def confirm_tool_call(session_id: str, request: Request):
        """Resolve a pending tool call confirmation."""
        if app.state.confirmation_manager is None:
            return JSONResponse({"error": "Not available"}, status_code=503)

        body = await request.json()
        tool_call_id = body.get("tool_call_id", "")
        confirmed = body.get("confirmed", False)

        found = app.state.confirmation_manager.resolve(session_id, tool_call_id, confirmed)
        if not found:
            return JSONResponse({"error": "No pending confirmation found"}, status_code=404)

        # Clear pending state in HUD
        agent_registry.update(session_id, pending_confirmation=None)

        return JSONResponse({"status": "resolved", "confirmed": confirmed})

    @app.get("/api/providers")
    async def list_providers():
        """List registered LLM providers with details."""
        if llm_router is None:
            return JSONResponse({"providers": [], "active": None})

        # Build details from config
        all_cfgs = [config.llm] + list(config.providers)
        cfg_map = {c.name: c for c in all_cfgs}

        details = []
        for name in llm_router.provider_names:
            c = cfg_map.get(name)
            details.append({
                "name": name,
                "type": getattr(c, "type", "openai") if c else "openai",
                "model": c.model if c else "unknown",
                "api_base": c.api_base if c else "",
                "api_key_env": c.api_key_env if c else "",
                "max_context_tokens": c.max_context_tokens if c else 0,
                "max_output_tokens": c.max_output_tokens if c else 0,
            })

        return JSONResponse({
            "providers": details,
            "active": llm_router.active_name,
        })

    @app.post("/api/providers/{name}/activate")
    async def activate_provider(name: str, request: Request):
        """Switch the active LLM provider."""
        if llm_router is None:
            return JSONResponse({"error": "No LLM providers configured"}, status_code=503)
        try:
            llm_router.set_active(name)
            return JSONResponse({"active": name})
        except KeyError:
            return JSONResponse(
                {"error": f"Unknown provider: {name}", "available": llm_router.provider_names},
                status_code=404,
            )

    def _find_config_path() -> Path | None:
        """Find the workbench.yaml config file."""
        for p in [
            Path.cwd() / "workbench.yaml",
            Path.home() / ".config" / "workbench" / "config.yaml",
            Path.home() / ".workbench" / "config.yaml",
        ]:
            if p.is_file():
                return p
        return None

    def _reload_provider(pcfg_dict: dict) -> str | None:
        """Create and register a provider from a config dict. Returns name or None."""
        if llm_router is None:
            return None
        from workbench.config import LLMProviderConfig
        from workbench.llm.providers import create_provider
        lcfg = LLMProviderConfig(**{k: v for k, v in pcfg_dict.items()
                                     if k in {f.name for f in __import__('dataclasses').fields(LLMProviderConfig)}})
        p = create_provider(lcfg)
        if p:
            llm_router.register_provider(lcfg.name, p)
            # Update in-memory config
            existing = [pc for pc in config.providers if pc.name == lcfg.name]
            if existing:
                for attr, val in pcfg_dict.items():
                    if hasattr(existing[0], attr):
                        setattr(existing[0], attr, val)
            else:
                config.providers.append(lcfg)
            return lcfg.name
        return None

    @app.post("/api/providers")
    async def add_provider(request: Request):
        """Add a new provider to config and register it."""
        import yaml
        body = await request.json()
        name = body.get("name", "").strip()
        if not name:
            return JSONResponse({"error": "name is required"}, status_code=400)

        # Save to YAML
        cfg_path = _find_config_path()
        if cfg_path:
            raw = yaml.safe_load(cfg_path.read_text()) or {}
            providers_list = raw.get("providers", [])
            # Remove existing with same name
            providers_list = [p for p in providers_list if p.get("name") != name]
            providers_list.append(body)
            raw["providers"] = providers_list
            cfg_path.write_text(yaml.dump(raw, default_flow_style=False, sort_keys=False))

        # Hot-reload into router
        result = _reload_provider(body)
        if result:
            return JSONResponse({"saved": True, "name": result})
        return JSONResponse({"error": "Failed to create provider (check API key env var)"}, status_code=400)

    @app.put("/api/providers/{name}")
    async def update_provider(name: str, request: Request):
        """Update an existing provider config."""
        import yaml
        body = await request.json()
        body["name"] = name  # Ensure name matches URL

        cfg_path = _find_config_path()
        if cfg_path:
            raw = yaml.safe_load(cfg_path.read_text()) or {}
            # Check if it's the primary llm config
            if raw.get("llm", {}).get("name") == name:
                raw["llm"].update(body)
            else:
                providers_list = raw.get("providers", [])
                providers_list = [p for p in providers_list if p.get("name") != name]
                providers_list.append(body)
                raw["providers"] = providers_list
            cfg_path.write_text(yaml.dump(raw, default_flow_style=False, sort_keys=False))

        result = _reload_provider(body)
        if result:
            return JSONResponse({"saved": True, "name": result})
        return JSONResponse({"error": "Failed to update provider"}, status_code=400)

    @app.delete("/api/providers/{name}")
    async def delete_provider(name: str, request: Request):
        """Remove a provider from config and router."""
        import yaml
        # Don't allow deleting the primary
        if config and config.llm.name == name:
            return JSONResponse({"error": "Cannot delete primary provider"}, status_code=400)

        cfg_path = _find_config_path()
        if cfg_path:
            raw = yaml.safe_load(cfg_path.read_text()) or {}
            providers_list = raw.get("providers", [])
            raw["providers"] = [p for p in providers_list if p.get("name") != name]
            cfg_path.write_text(yaml.dump(raw, default_flow_style=False, sort_keys=False))

        # Remove from in-memory
        config.providers = [pc for pc in config.providers if pc.name != name]
        if llm_router and name in llm_router.provider_names:
            llm_router._providers.pop(name, None)
            if llm_router.active_name == name and llm_router.provider_names:
                llm_router.set_active(llm_router.provider_names[0])

        return JSONResponse({"deleted": True, "name": name})

    # --- Memory endpoints ---

    @app.get("/api/workspaces/{workspace_id}/memory")
    async def list_memory(workspace_id: str):
        """List all memory entries for a workspace."""
        await memory_provider._ensure_init()
        entries = await memory_provider.get_all(workspace_id)
        return JSONResponse({"entries": entries})

    @app.get("/api/workspaces/{workspace_id}/memory/local")
    async def get_local_memory(workspace_id: str):
        """Read workspace-local memory files (CLAUDE.md, etc.)."""
        ws = workspace_manager.get(workspace_id)
        if ws is None or not ws.path:
            return JSONResponse({"files": {}})

        files = await file_memory_provider.get_all(ws.path)
        return JSONResponse({"files": files})

    @app.put("/api/workspaces/{workspace_id}/memory/{key:path}")
    async def set_memory(workspace_id: str, key: str, request: Request):
        """Set a memory value."""
        body = await request.json()
        value = body.get("value", "")
        await memory_provider.set(workspace_id, key, value)
        return JSONResponse({"status": "ok"})

    @app.delete("/api/workspaces/{workspace_id}/memory/{key:path}")
    async def delete_memory(workspace_id: str, key: str):
        """Delete a memory entry."""
        deleted = await memory_provider.delete(workspace_id, key)
        if not deleted:
            return JSONResponse({"error": "Not found"}, status_code=404)
        return JSONResponse({"status": "deleted"})

    # --- Allowlist endpoints ---

    ALLOWLIST_KEY = "_allowlist"
    GLOBAL_ALLOWLIST_WS = "__global__"

    async def _load_allowlist(ws_id: str) -> list[str]:
        """Load allowlist patterns from SQLite for a given scope."""
        await memory_provider._ensure_init()
        raw = await memory_provider.get(ws_id, ALLOWLIST_KEY)
        if raw is None:
            return []
        try:
            patterns = json.loads(raw)
            return patterns if isinstance(patterns, list) else []
        except (json.JSONDecodeError, TypeError):
            return []

    async def _save_allowlist(ws_id: str, patterns: list[str]) -> None:
        """Persist allowlist patterns to SQLite."""
        await memory_provider._ensure_init()
        await memory_provider.set(ws_id, ALLOWLIST_KEY, json.dumps(patterns))

    async def _resolve_effective_allowlist(workspace_id: str) -> list[str]:
        """Merge global + workspace allowlist. Workspace patterns add to global."""
        global_patterns = await _load_allowlist(GLOBAL_ALLOWLIST_WS)
        if workspace_id == GLOBAL_ALLOWLIST_WS or workspace_id == GLOBAL_WORKSPACE_ID:
            return global_patterns
        ws_patterns = await _load_allowlist(workspace_id)
        # Workspace adds to global (deduplicated, order preserved)
        seen = set()
        merged = []
        for p in global_patterns + ws_patterns:
            if p not in seen:
                seen.add(p)
                merged.append(p)
        return merged

    def _validate_patterns(patterns: list) -> str | None:
        """Validate regex patterns. Returns error message or None."""
        for pat in patterns:
            if not isinstance(pat, str):
                return f"Pattern must be a string, got {type(pat).__name__}"
            try:
                re.compile(pat)
            except re.error as e:
                return f"Invalid regex pattern '{pat}': {e}"
        return None

    @app.get("/api/policy/allowlist")
    async def get_global_allowlist():
        """Get the global command allowlist."""
        patterns = await _load_allowlist(GLOBAL_ALLOWLIST_WS)
        return JSONResponse({"patterns": patterns, "scope": "global"})

    @app.put("/api/policy/allowlist")
    async def update_global_allowlist(request: Request):
        """Update the global command allowlist (persisted)."""
        body = await request.json()
        patterns = body.get("patterns", [])
        err = _validate_patterns(patterns)
        if err:
            return JSONResponse({"error": err}, status_code=400)
        await _save_allowlist(GLOBAL_ALLOWLIST_WS, patterns)
        # Also update the in-memory policy for non-streaming paths
        policy.allowed_patterns = patterns
        return JSONResponse({"status": "updated", "patterns": patterns, "scope": "global"})

    @app.get("/api/workspaces/{workspace_id}/allowlist")
    async def get_workspace_allowlist(workspace_id: str):
        """Get effective allowlist for a workspace (global + workspace-specific)."""
        ws_patterns = await _load_allowlist(workspace_id)
        effective = await _resolve_effective_allowlist(workspace_id)
        global_patterns = await _load_allowlist(GLOBAL_ALLOWLIST_WS)
        return JSONResponse({
            "effective": effective,
            "workspace": ws_patterns,
            "global": global_patterns,
            "scope": workspace_id,
        })

    @app.put("/api/workspaces/{workspace_id}/allowlist")
    async def update_workspace_allowlist(workspace_id: str, request: Request):
        """Update workspace-specific allowlist (adds to global)."""
        if workspace_id == GLOBAL_WORKSPACE_ID:
            return JSONResponse(
                {"error": "Use PUT /api/policy/allowlist for global scope"},
                status_code=400,
            )
        body = await request.json()
        patterns = body.get("patterns", [])
        err = _validate_patterns(patterns)
        if err:
            return JSONResponse({"error": err}, status_code=400)
        await _save_allowlist(workspace_id, patterns)
        effective = await _resolve_effective_allowlist(workspace_id)
        return JSONResponse({
            "status": "updated",
            "workspace": patterns,
            "effective": effective,
            "scope": workspace_id,
        })

    @app.delete("/api/workspaces/{workspace_id}/allowlist")
    async def reset_workspace_allowlist(workspace_id: str):
        """Reset workspace allowlist to global only (remove workspace overrides)."""
        if workspace_id == GLOBAL_WORKSPACE_ID:
            return JSONResponse({"error": "Cannot reset global"}, status_code=400)
        await memory_provider._ensure_init()
        await memory_provider.delete(workspace_id, ALLOWLIST_KEY)
        effective = await _resolve_effective_allowlist(workspace_id)
        return JSONResponse({
            "status": "reset",
            "effective": effective,
            "scope": workspace_id,
        })

    return app
