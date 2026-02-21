"""Recipe CRUD and execution endpoints."""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from starlette.responses import StreamingResponse

import shutil

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
    scope: str = Field(
        default="",
        description="'personal' for ~/.workbench/recipes, 'project' for workspace .workbench/recipes. Empty = auto (project if workspace has path, else personal).",
    )


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

    # Determine save location based on scope
    if req.scope == "personal":
        recipes_dir = Path.home() / ".workbench" / "recipes" / recipe.name
    elif req.scope == "project" and ws and ws.path:
        recipes_dir = Path(ws.path) / ".workbench" / "recipes" / recipe.name
    elif req.scope == "project" and (not ws or not ws.path):
        raise HTTPException(
            400, "Cannot save as project recipe: workspace has no path"
        )
    else:
        # Auto: project if workspace has path, else personal
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

    saved_scope = "personal" if str(Path.home()) in str(recipes_dir) else "project"
    return JSONResponse(
        {
            "name": recipe.name,
            "version": recipe.version,
            "path": str(recipes_dir),
            "scope": saved_scope,
        },
        status_code=201,
    )


@router.delete("/{workspace_id}/recipes/{recipe_name}")
async def delete_recipe(
    workspace_id: str, recipe_name: str, request: Request
):
    """Delete a recipe by name."""
    recipe_registry = getattr(request.app.state, "recipe_registry", None)
    if recipe_registry is None:
        raise HTTPException(404, f"Recipe not found: {recipe_name}")

    recipe = recipe_registry.get(recipe_name)
    if recipe is None:
        raise HTTPException(404, f"Recipe not found: {recipe_name}")

    # Remove from disk
    source = Path(recipe.source_path) if recipe.source_path else None
    if source and source.is_dir():
        shutil.rmtree(source)
    elif source and source.is_file():
        source.unlink()

    # Unregister
    recipe_registry.unregister(recipe_name)

    return JSONResponse({"deleted": recipe_name})
