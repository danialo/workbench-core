"""Recipe schema — dataclass model and YAML serialization."""
from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

import yaml


@dataclass
class RecipeParameter:
    """A single recipe parameter definition."""

    name: str
    type: str = "string"  # string | int | float | bool | choice
    description: str = ""
    default: Any = None
    required: bool = True
    choices: list[str] = field(default_factory=list)


@dataclass
class Recipe:
    """A reusable, parameterized execution template."""

    name: str
    description: str = ""
    version: str = "1.0.0"
    trigger: list[str] = field(default_factory=list)  # regex patterns
    prompt_template: str = ""
    parameters: list[RecipeParameter] = field(default_factory=list)
    tools: list[str] = field(default_factory=list)  # tool name whitelist
    output_format: str = ""
    tags: list[str] = field(default_factory=list)
    source_path: str = ""

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d.pop("source_path", None)
        return d

    @classmethod
    def from_dict(cls, d: dict[str, Any], source_path: str = "") -> Recipe:
        params_raw = d.get("parameters", [])
        params = []
        known_fields = {f.name for f in RecipeParameter.__dataclass_fields__.values()}
        for p in params_raw:
            if isinstance(p, dict):
                params.append(
                    RecipeParameter(**{k: v for k, v in p.items() if k in known_fields})
                )

        return cls(
            name=d.get("name", ""),
            description=d.get("description", ""),
            version=str(d.get("version", "1.0.0")),
            trigger=d.get("trigger", []),
            prompt_template=d.get("prompt_template", ""),
            parameters=params,
            tools=d.get("tools", []),
            output_format=d.get("output_format", ""),
            tags=d.get("tags", []),
            source_path=source_path,
        )

    def validate(self) -> list[str]:
        """Return list of validation errors, empty if valid."""
        errors = []
        if not self.name:
            errors.append("Recipe name is required")
        elif not self.name.replace("-", "").replace("_", "").isalnum():
            errors.append(
                "Recipe name must be alphanumeric with hyphens/underscores"
            )
        if not self.prompt_template:
            errors.append("Recipe prompt_template is required")
        # Check template references match declared parameters
        template_params = set(re.findall(r"\{\{(\w+)\}\}", self.prompt_template))
        declared_params = {p.name for p in self.parameters}
        undeclared = template_params - declared_params
        if undeclared:
            errors.append(
                f"Template references undeclared parameters: {undeclared}"
            )
        return errors


def load_recipe_from_dir(recipe_dir: Path) -> Recipe:
    """Load a recipe from a directory containing recipe.yaml and optional prompt.md."""
    yaml_path = recipe_dir / "recipe.yaml"
    if not yaml_path.exists():
        raise FileNotFoundError(f"No recipe.yaml in {recipe_dir}")

    with yaml_path.open("r", encoding="utf-8") as f:
        raw = yaml.safe_load(f) or {}

    recipe = Recipe.from_dict(raw, source_path=str(recipe_dir))

    # Load prompt.md if it exists and prompt_template is not inline
    prompt_md = recipe_dir / "prompt.md"
    if prompt_md.exists() and not recipe.prompt_template:
        recipe.prompt_template = prompt_md.read_text(encoding="utf-8")

    return recipe


def load_recipe_from_yaml(yaml_content: str) -> Recipe:
    """Parse a recipe from raw YAML string."""
    raw = yaml.safe_load(yaml_content) or {}
    return Recipe.from_dict(raw)
