"""Recipe executor — renders templates, filters tools, streams execution."""
from __future__ import annotations

import logging
import re
from typing import Any, AsyncIterator

from workbench.orchestrator.events import OrchestratorEvent
from workbench.recipes.schema import Recipe
from workbench.tools.registry import ToolRegistry

logger = logging.getLogger(__name__)


class RecipeError(Exception):
    """Raised for recipe rendering or validation failures."""


def validate_parameters(recipe: Recipe, params: dict[str, Any]) -> dict[str, Any]:
    """Validate and coerce parameters against recipe schema.

    Returns the validated parameter dict (with defaults applied).
    Raises RecipeError on validation failure.
    """
    result = {}

    for pdef in recipe.parameters:
        value = params.get(pdef.name, pdef.default)

        if value is None and pdef.required:
            raise RecipeError(f"Missing required parameter: {pdef.name}")
        if value is None:
            continue

        # Type coercion
        if pdef.type == "int":
            try:
                value = int(value)
            except (ValueError, TypeError):
                raise RecipeError(f"Parameter '{pdef.name}' must be an integer")
        elif pdef.type == "float":
            try:
                value = float(value)
            except (ValueError, TypeError):
                raise RecipeError(f"Parameter '{pdef.name}' must be a float")
        elif pdef.type == "bool":
            if isinstance(value, str):
                value = value.lower() in ("true", "1", "yes")
            else:
                value = bool(value)
        elif pdef.type == "choice":
            if str(value) not in pdef.choices:
                raise RecipeError(
                    f"Parameter '{pdef.name}' must be one of: {pdef.choices}"
                )
        else:
            value = str(value)

        result[pdef.name] = value

    # Warn about unknown parameters
    declared = {p.name for p in recipe.parameters}
    for key in params:
        if key not in declared:
            logger.warning(
                "Ignoring unknown parameter '%s' for recipe '%s'",
                key,
                recipe.name,
            )

    return result


def render_template(template: str, params: dict[str, Any]) -> str:
    """Render a recipe prompt template with {{param}} substitution."""

    def replacer(match):
        key = match.group(1)
        if key in params:
            return str(params[key])
        return match.group(0)

    return re.sub(r"\{\{(\w+)\}\}", replacer, template)


def build_recipe_context(recipe: Recipe, rendered_prompt: str) -> str:
    """Build the context_prefix for the orchestrator from a rendered recipe."""
    sections = [f"## Recipe: {recipe.name}\n"]

    if recipe.description:
        sections.append(recipe.description)

    sections.append(rendered_prompt)

    if recipe.output_format:
        sections.append(f"\n## Output Format\n\n{recipe.output_format}")

    return "\n\n".join(sections) + "\n\n"


def filter_registry(
    full_registry: ToolRegistry, tool_names: list[str]
) -> ToolRegistry:
    """Create a new ToolRegistry containing only the named tools."""
    if not tool_names:
        return full_registry

    filtered = ToolRegistry()
    for name in tool_names:
        tool = full_registry.get(name)
        if tool is not None:
            filtered.register(tool)
        else:
            logger.warning("Recipe references unknown tool: %s", name)
    return filtered


class RecipeExecutor:
    """Execute a recipe through the orchestrator pipeline."""

    def __init__(self, orchestrator_factory):
        self.factory = orchestrator_factory

    async def execute(
        self,
        recipe: Recipe,
        params: dict[str, Any],
        session_id: str,
        user_message: str = "",
        confirmation_callback=None,
    ) -> AsyncIterator[OrchestratorEvent]:
        """Render and execute a recipe, yielding OrchestratorEvents."""
        validated = validate_parameters(recipe, params)
        rendered = render_template(recipe.prompt_template, validated)
        context_prefix = build_recipe_context(recipe, rendered)

        # Filter registry if tools whitelist is specified
        filtered_registry = None
        if recipe.tools:
            filtered_registry = filter_registry(
                self.factory.registry, recipe.tools
            )

        orch = await self.factory.create(
            session_id=session_id,
            confirmation_callback=confirmation_callback,
            context_prefix=context_prefix,
            registry=filtered_registry,
        )

        # Use the rendered prompt as the user message if none provided
        message = user_message or rendered

        async for event in orch.run_streaming(message):
            yield event
