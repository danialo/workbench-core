"""Recipe registry — discovers, indexes, and matches recipes."""
from __future__ import annotations

import logging
import re
from pathlib import Path

from workbench.recipes.schema import Recipe, load_recipe_from_dir

logger = logging.getLogger(__name__)

RECIPES_SUBDIR = ".workbench/recipes"


class RecipeRegistry:
    """Discovers and indexes recipes from workspace directories."""

    def __init__(self):
        self._recipes: dict[str, Recipe] = {}
        self._compiled_triggers: dict[str, list[re.Pattern]] = {}

    def register(self, recipe: Recipe) -> bool:
        """Register a recipe. Returns True if valid and registered."""
        errors = recipe.validate()
        if errors:
            logger.warning("Skipping invalid recipe %s: %s", recipe.name, errors)
            return False
        self._recipes[recipe.name] = recipe
        patterns = []
        for pattern_str in recipe.trigger:
            try:
                patterns.append(re.compile(pattern_str, re.IGNORECASE))
            except re.error as e:
                logger.warning(
                    "Invalid trigger regex in recipe %s: %s", recipe.name, e
                )
        self._compiled_triggers[recipe.name] = patterns
        return True

    def get(self, name: str) -> Recipe | None:
        return self._recipes.get(name)

    def list(self, tag: str | None = None) -> list[Recipe]:
        recipes = list(self._recipes.values())
        if tag:
            recipes = [r for r in recipes if tag in r.tags]
        return sorted(recipes, key=lambda r: r.name)

    def match_trigger(self, text: str) -> list[Recipe]:
        """Return recipes whose trigger patterns match the given text."""
        matches = []
        for name, patterns in self._compiled_triggers.items():
            for pat in patterns:
                if pat.search(text):
                    matches.append(self._recipes[name])
                    break
        return matches

    def discover(self, workspace_path: str) -> int:
        """Scan {workspace_path}/.workbench/recipes/ for recipe directories."""
        recipes_dir = Path(workspace_path) / RECIPES_SUBDIR
        return self._discover_from(recipes_dir)

    def discover_global(self) -> int:
        """Scan ~/.workbench/recipes/ for global recipes."""
        global_dir = Path.home() / ".workbench" / "recipes"
        return self._discover_from(global_dir)

    def _discover_from(self, recipes_dir: Path) -> int:
        if not recipes_dir.is_dir():
            return 0
        loaded = 0
        for entry in sorted(recipes_dir.iterdir()):
            if entry.is_dir() and (entry / "recipe.yaml").exists():
                try:
                    recipe = load_recipe_from_dir(entry)
                    if not self.register(recipe):
                        continue
                    loaded += 1
                    logger.info(
                        "Loaded recipe: %s (v%s)", recipe.name, recipe.version
                    )
                except Exception as e:
                    logger.warning(
                        "Failed to load recipe from %s: %s", entry, e
                    )
        return loaded
