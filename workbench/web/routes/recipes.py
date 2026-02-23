"""Recipe CRUD and execution endpoints."""
from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from starlette.responses import StreamingResponse

from workbench.recipes.executor import RecipeError, RecipeExecutor
from workbench.recipes.schema import load_recipe_from_yaml

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/workspaces", tags=["recipes"])


# -----------------------------------------------------------------------
# Pydantic models
# -----------------------------------------------------------------------


class RunRecipeRequest(BaseModel):
    parameters: dict[str, Any] = Field(default_factory=dict)
    session_id: str = Field(
        default="", description="Session to use; empty = create new"
    )
    message: str = Field(
        default="", description="Optional user message override"
    )


class SaveRecipeRequest(BaseModel):
    yaml_content: str = Field(..., min_length=1)


class DeployRecipeRequest(BaseModel):
    parameters: dict[str, Any] = Field(default_factory=dict)


# -----------------------------------------------------------------------
# Endpoints
# -----------------------------------------------------------------------


@router.get("/{workspace_id}/recipes")
async def list_recipes(workspace_id: str, request: Request, tag: str = ""):
    """List available recipes for a workspace."""
    recipe_registry = getattr(request.app.state, "recipe_registry", None)
    if recipe_registry is None:
        return JSONResponse({"recipes": []})

    ws_manager = request.app.state.workspace_manager
    ws = ws_manager.get(workspace_id)

    # Re-discover for this workspace + global
    if ws and ws.path:
        recipe_registry.discover(ws.path)
    recipe_registry.discover_global()

    recipes = recipe_registry.list(tag=tag or None)
    return JSONResponse({"recipes": [r.to_dict() for r in recipes]})


@router.get("/{workspace_id}/recipes/{recipe_name}")
async def get_recipe(
    workspace_id: str, recipe_name: str, request: Request
):
    """Get recipe details."""
    recipe_registry = getattr(request.app.state, "recipe_registry", None)
    if recipe_registry is None:
        raise HTTPException(404, f"Recipe not found: {recipe_name}")

    recipe = recipe_registry.get(recipe_name)
    if recipe is None:
        raise HTTPException(404, f"Recipe not found: {recipe_name}")
    return JSONResponse(recipe.to_dict())


@router.post("/{workspace_id}/recipes/{recipe_name}/run")
async def run_recipe(
    workspace_id: str,
    recipe_name: str,
    req: RunRecipeRequest,
    request: Request,
):
    """Execute a recipe with parameters, streaming results via SSE."""
    recipe_registry = getattr(request.app.state, "recipe_registry", None)
    if recipe_registry is None:
        raise HTTPException(404, f"Recipe not found: {recipe_name}")

    recipe = recipe_registry.get(recipe_name)
    if recipe is None:
        raise HTTPException(404, f"Recipe not found: {recipe_name}")

    factory = getattr(request.app.state, "orchestrator_factory", None)
    if factory is None:
        return JSONResponse({"error": "LLM not configured"}, status_code=503)

    # Resolve or create session
    session_store = request.app.state.session_store
    session_id = req.session_id
    if not session_id:
        metadata = {
            "workspace_id": workspace_id,
            "recipe_name": recipe_name,
            "recipe_version": recipe.version,
            "parameters": req.parameters,
            "status": "active",
        }
        session_id = await session_store.create_session(metadata)

    executor = RecipeExecutor(factory)

    try:
        events = executor.execute(
            recipe=recipe,
            params=req.parameters,
            session_id=session_id,
            user_message=req.message,
        )
    except RecipeError as e:
        raise HTTPException(400, str(e))

    from workbench.web.streaming import sse_generator

    return StreamingResponse(
        sse_generator(events),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Session-Id": session_id,
            "X-Recipe-Name": recipe_name,
        },
    )


@router.post("/{workspace_id}/recipes/{recipe_name}/deploy")
async def deploy_recipe(
    workspace_id: str,
    recipe_name: str,
    req: DeployRecipeRequest,
    request: Request,
):
    """Deploy a recipe as a background agent. Returns session_id immediately."""
    recipe_registry = getattr(request.app.state, "recipe_registry", None)
    if recipe_registry is None:
        raise HTTPException(404, f"Recipe not found: {recipe_name}")

    recipe = recipe_registry.get(recipe_name)
    if recipe is None:
        raise HTTPException(404, f"Recipe not found: {recipe_name}")

    factory = getattr(request.app.state, "orchestrator_factory", None)
    if factory is None:
        return JSONResponse({"error": "LLM not configured"}, status_code=503)

    session_store = request.app.state.session_store
    agent_registry = request.app.state.agent_registry
    cm = request.app.state.confirmation_manager
    ws_manager = request.app.state.workspace_manager

    # Resolve workspace name for HUD display
    ws_name = "Playground"
    ws = ws_manager.get(workspace_id)
    if ws:
        ws_name = ws.name

    # Create session tagged as an agent deployment
    metadata = {
        "workspace_id": workspace_id,
        "recipe_name": recipe_name,
        "recipe_version": recipe.version,
        "parameters": req.parameters,
        "deployed_as_agent": True,
        "status": "active",
    }
    session_id = await session_store.create_session(metadata)

    executor = RecipeExecutor(factory)

    async def _confirmation_callback(tool_name: str, tool_call) -> bool:
        return await cm.wait_for_confirmation(
            session_id=session_id,
            tool_call_id=tool_call.id,
            tool_name=tool_name,
            tool_args=tool_call.arguments,
        )

    # Register in HUD before the background task starts
    label = recipe.description or recipe.name
    agent_registry.register(session_id, workspace_id, ws_name, label=f"{recipe.name}: {label}" if recipe.description else recipe.name)

    async def _background_run():
        try:
            events = executor.execute(
                recipe=recipe,
                params=req.parameters,
                session_id=session_id,
                confirmation_callback=_confirmation_callback,
            )
            async for event in events:
                if event.type == "tool_call_start":
                    agent_registry.update(
                        session_id,
                        status="running",
                        current_action=f"Calling {event.data.get('name', '')}",
                    )
                elif event.type == "confirmation_required":
                    agent_registry.update(
                        session_id,
                        status="waiting",
                        current_action=f"Awaiting: {event.data.get('tool_name', '')}",
                        pending_confirmation={
                            "tool_call_id": event.data.get("tool_call_id"),
                            "tool_name": event.data.get("tool_name"),
                            "tool_args": event.data.get("tool_args"),
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
        except Exception as e:
            logger.error("Background agent error for session %s: %s", session_id, e)
            agent_registry.unregister(session_id, status="error")
        else:
            agent_registry.unregister(session_id, status="completed")

    task = asyncio.create_task(_background_run())
    agent_registry.attach_task(session_id, task)

    return JSONResponse(
        {"session_id": session_id, "recipe_name": recipe_name, "status": "deployed"},
        status_code=202,
    )


@router.post("/{workspace_id}/recipes")
async def save_recipe(
    workspace_id: str, req: SaveRecipeRequest, request: Request
):
    """Save a new recipe from YAML content."""
    ws_manager = request.app.state.workspace_manager
    ws = ws_manager.get(workspace_id)

    try:
        recipe = load_recipe_from_yaml(req.yaml_content)
    except Exception as e:
        raise HTTPException(400, f"Invalid recipe YAML: {e}")

    errors = recipe.validate()
    if errors:
        raise HTTPException(
            400, f"Recipe validation failed: {'; '.join(errors)}"
        )

    # Save to workspace recipes dir (or global if no workspace path)
    if ws and ws.path:
        recipes_dir = Path(ws.path) / ".workbench" / "recipes" / recipe.name
    else:
        recipes_dir = Path.home() / ".workbench" / "recipes" / recipe.name

    recipes_dir.mkdir(parents=True, exist_ok=True)
    (recipes_dir / "recipe.yaml").write_text(
        req.yaml_content, encoding="utf-8"
    )

    # Register it
    recipe_registry = getattr(request.app.state, "recipe_registry", None)
    if recipe_registry:
        recipe.source_path = str(recipes_dir)
        recipe_registry.register(recipe)

    return JSONResponse(
        {
            "name": recipe.name,
            "version": recipe.version,
            "path": str(recipes_dir),
        },
        status_code=201,
    )
