"""Tool that lets the LLM create and save recipes via tool call."""
from __future__ import annotations

import logging
from pathlib import Path

from workbench.recipes.registry import RecipeRegistry
from workbench.recipes.schema import load_recipe_from_yaml
from workbench.tools.base import Tool, ToolRisk
from workbench.types import ToolResult

logger = logging.getLogger(__name__)


class SaveRecipeTool(Tool):
    """Save a new recipe from YAML content."""

    def __init__(
        self,
        registry: RecipeRegistry,
        global_recipes_dir: Path | str | None = None,
    ) -> None:
        self._registry = registry
        self._global_dir = Path(
            global_recipes_dir or Path.home() / ".workbench" / "recipes"
        )

    @property
    def name(self) -> str:
        return "save_recipe"

    @property
    def description(self) -> str:
        return (
            "Save a new automation recipe from YAML content. The YAML must "
            "include at minimum: name, prompt_template. Optional fields: "
            "description, version, parameters (list of {name, type, description, "
            "required, default}), tools (list of tool names to restrict to), "
            "tags (list of strings), output_format. "
            "Example:\n"
            "  name: disk-check\n"
            "  description: Check disk usage on a host\n"
            "  prompt_template: Run df -h on {{target}}\n"
            "  parameters:\n"
            "    - name: target\n"
            "      type: string\n"
            "      description: Host to check\n"
            "  tools: [run_shell]\n"
            "  tags: [ops, diagnostics]"
        )

    @property
    def parameters(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "yaml_content": {
                    "type": "string",
                    "description": "Full recipe YAML content.",
                },
            },
            "required": ["yaml_content"],
        }

    @property
    def risk_level(self) -> ToolRisk:
        return ToolRisk.WRITE

    async def execute(self, **kwargs) -> ToolResult:
        yaml_content = kwargs.get("yaml_content", "")
        if not yaml_content.strip():
            return ToolResult(success=False, content="yaml_content is required.")

        # Parse
        try:
            recipe = load_recipe_from_yaml(yaml_content)
        except Exception as e:
            return ToolResult(success=False, content=f"Invalid recipe YAML: {e}")

        # Validate
        errors = recipe.validate()
        if errors:
            return ToolResult(
                success=False,
                content=f"Recipe validation failed: {'; '.join(errors)}",
            )

        # Save to disk
        recipes_dir = self._global_dir / recipe.name
        recipes_dir.mkdir(parents=True, exist_ok=True)
        recipe_path = recipes_dir / "recipe.yaml"
        recipe_path.write_text(yaml_content, encoding="utf-8")

        # Register in the live registry
        recipe.source_path = str(recipes_dir)
        self._registry.register(recipe)

        logger.info("Saved recipe '%s' to %s", recipe.name, recipes_dir)
        return ToolResult(
            success=True,
            content=(
                f"Recipe '{recipe.name}' (v{recipe.version}) saved to "
                f"{recipes_dir}. It is now available in the recipe browser."
            ),
        )
