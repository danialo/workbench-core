"""Tests for the recipe system."""

import pytest
from pathlib import Path

from workbench.recipes.schema import (
    Recipe,
    RecipeParameter,
    load_recipe_from_dir,
    load_recipe_from_yaml,
)
from workbench.recipes.registry import RecipeRegistry
from workbench.recipes.executor import (
    RecipeError,
    build_recipe_context,
    filter_registry,
    render_template,
    validate_parameters,
)
from workbench.tools.registry import ToolRegistry
from tests.mock_tools import EchoTool, WriteTool


# -----------------------------------------------------------------------
# Schema tests
# -----------------------------------------------------------------------


class TestRecipeSchema:
    def test_from_dict_minimal(self):
        r = Recipe.from_dict({"name": "test", "prompt_template": "hello"})
        assert r.name == "test"
        assert r.version == "1.0.0"
        assert r.parameters == []
        assert r.tools == []

    def test_from_dict_with_parameters(self):
        r = Recipe.from_dict({
            "name": "check",
            "prompt_template": "check {{host}}",
            "parameters": [
                {"name": "host", "type": "string", "required": True}
            ],
        })
        assert len(r.parameters) == 1
        assert r.parameters[0].name == "host"
        assert r.parameters[0].type == "string"

    def test_from_dict_version_coerced_to_string(self):
        r = Recipe.from_dict({"name": "t", "prompt_template": "x", "version": 1.1})
        assert r.version == "1.1"

    def test_validate_missing_name(self):
        r = Recipe(name="", prompt_template="x")
        errors = r.validate()
        assert any("name is required" in e for e in errors)

    def test_validate_bad_name(self):
        r = Recipe(name="has spaces!", prompt_template="x")
        errors = r.validate()
        assert any("alphanumeric" in e for e in errors)

    def test_validate_valid_name_with_hyphens(self):
        r = Recipe(
            name="health-check_v2",
            prompt_template="{{host}}",
            parameters=[RecipeParameter(name="host")],
        )
        assert r.validate() == []

    def test_validate_missing_template(self):
        r = Recipe(name="t", prompt_template="")
        errors = r.validate()
        assert any("prompt_template" in e for e in errors)

    def test_validate_undeclared_params(self):
        r = Recipe(name="t", prompt_template="{{foo}} {{bar}}")
        errors = r.validate()
        assert any("undeclared" in e for e in errors)

    def test_validate_valid(self):
        r = Recipe(
            name="t",
            prompt_template="{{host}}",
            parameters=[RecipeParameter(name="host")],
        )
        assert r.validate() == []

    def test_to_dict_excludes_source_path(self):
        r = Recipe(name="t", prompt_template="x", source_path="/tmp/foo")
        d = r.to_dict()
        assert "source_path" not in d
        assert d["name"] == "t"

    def test_load_from_yaml(self):
        yaml_str = """
name: health-check
description: Run a health check
version: "1.1"
prompt_template: "Check health of {{service}}"
parameters:
  - name: service
    type: string
    required: true
tools:
  - run_shell
tags:
  - ops
"""
        r = load_recipe_from_yaml(yaml_str)
        assert r.name == "health-check"
        assert r.version == "1.1"
        assert len(r.parameters) == 1
        assert r.parameters[0].name == "service"
        assert r.tools == ["run_shell"]
        assert r.tags == ["ops"]

    def test_load_from_yaml_empty(self):
        r = load_recipe_from_yaml("")
        assert r.name == ""

    def test_load_from_dir(self, tmp_path):
        recipe_dir = tmp_path / "my-recipe"
        recipe_dir.mkdir()
        (recipe_dir / "recipe.yaml").write_text(
            "name: my-recipe\nprompt_template: hello {{name}}\n"
            "parameters:\n  - name: name\n    type: string\n"
        )
        r = load_recipe_from_dir(recipe_dir)
        assert r.name == "my-recipe"
        assert r.source_path == str(recipe_dir)

    def test_load_from_dir_with_prompt_md(self, tmp_path):
        recipe_dir = tmp_path / "md-recipe"
        recipe_dir.mkdir()
        (recipe_dir / "recipe.yaml").write_text(
            "name: md-recipe\nparameters:\n  - name: target\n    type: string\n"
        )
        (recipe_dir / "prompt.md").write_text(
            "Check the status of {{target}} and report findings."
        )
        r = load_recipe_from_dir(recipe_dir)
        assert "{{target}}" in r.prompt_template
        assert r.validate() == []

    def test_load_from_dir_inline_overrides_prompt_md(self, tmp_path):
        recipe_dir = tmp_path / "inline"
        recipe_dir.mkdir()
        (recipe_dir / "recipe.yaml").write_text(
            "name: inline\nprompt_template: inline template\n"
        )
        (recipe_dir / "prompt.md").write_text("should be ignored")
        r = load_recipe_from_dir(recipe_dir)
        assert r.prompt_template == "inline template"

    def test_load_from_dir_missing_yaml(self, tmp_path):
        with pytest.raises(FileNotFoundError):
            load_recipe_from_dir(tmp_path / "nonexistent")


# -----------------------------------------------------------------------
# Registry tests
# -----------------------------------------------------------------------


class TestRecipeRegistry:
    def test_register_and_get(self):
        reg = RecipeRegistry()
        r = Recipe(name="t", prompt_template="x")
        reg.register(r)
        assert reg.get("t") is r

    def test_list_sorted(self):
        reg = RecipeRegistry()
        reg.register(Recipe(name="b", prompt_template="x"))
        reg.register(Recipe(name="a", prompt_template="x"))
        assert [r.name for r in reg.list()] == ["a", "b"]

    def test_list_by_tag(self):
        reg = RecipeRegistry()
        reg.register(Recipe(name="a", prompt_template="x", tags=["ops"]))
        reg.register(Recipe(name="b", prompt_template="x", tags=["dev"]))
        result = reg.list(tag="ops")
        assert len(result) == 1
        assert result[0].name == "a"

    def test_match_trigger(self):
        reg = RecipeRegistry()
        r = Recipe(
            name="t",
            prompt_template="x",
            trigger=[r"^check health"],
        )
        reg.register(r)
        assert len(reg.match_trigger("check health of api")) == 1
        assert len(reg.match_trigger("deploy something")) == 0

    def test_match_trigger_case_insensitive(self):
        reg = RecipeRegistry()
        r = Recipe(
            name="t",
            prompt_template="x",
            trigger=[r"health"],
        )
        reg.register(r)
        assert len(reg.match_trigger("Check HEALTH status")) == 1

    def test_invalid_recipe_skipped(self):
        reg = RecipeRegistry()
        r = Recipe(name="", prompt_template="x")
        reg.register(r)
        assert reg.get("") is None
        assert len(reg.list()) == 0

    def test_discover(self, tmp_path):
        recipes_dir = tmp_path / ".workbench" / "recipes" / "test-recipe"
        recipes_dir.mkdir(parents=True)
        (recipes_dir / "recipe.yaml").write_text(
            "name: test-recipe\nprompt_template: hello\n"
        )

        reg = RecipeRegistry()
        loaded = reg.discover(str(tmp_path))
        assert loaded == 1
        assert reg.get("test-recipe") is not None

    def test_discover_empty(self, tmp_path):
        reg = RecipeRegistry()
        loaded = reg.discover(str(tmp_path))
        assert loaded == 0

    def test_discover_skips_invalid(self, tmp_path):
        recipes_dir = tmp_path / ".workbench" / "recipes" / "bad"
        recipes_dir.mkdir(parents=True)
        (recipes_dir / "recipe.yaml").write_text("name: \nprompt_template: x\n")

        reg = RecipeRegistry()
        loaded = reg.discover(str(tmp_path))
        assert loaded == 0


# -----------------------------------------------------------------------
# Executor tests
# -----------------------------------------------------------------------


class TestRenderTemplate:
    def test_basic(self):
        result = render_template(
            "Hello {{name}}, check {{host}}",
            {"name": "ops", "host": "srv1"},
        )
        assert result == "Hello ops, check srv1"

    def test_missing_param_left_as_is(self):
        result = render_template("Hello {{name}}", {})
        assert result == "Hello {{name}}"

    def test_multiple_occurrences(self):
        result = render_template(
            "{{x}} and {{x}} again", {"x": "val"}
        )
        assert result == "val and val again"

    def test_no_placeholders(self):
        result = render_template("plain text", {"x": "y"})
        assert result == "plain text"


class TestValidateParameters:
    def _recipe(self, **kwargs):
        return Recipe(name="t", prompt_template="x", **kwargs)

    def test_required_missing(self):
        r = self._recipe(
            parameters=[RecipeParameter(name="host", required=True)]
        )
        with pytest.raises(RecipeError, match="Missing required"):
            validate_parameters(r, {})

    def test_default_applied(self):
        r = self._recipe(
            parameters=[RecipeParameter(name="host", default="localhost")]
        )
        result = validate_parameters(r, {})
        assert result["host"] == "localhost"

    def test_int_coercion(self):
        r = self._recipe(
            parameters=[RecipeParameter(name="port", type="int")]
        )
        result = validate_parameters(r, {"port": "8080"})
        assert result["port"] == 8080

    def test_int_coercion_invalid(self):
        r = self._recipe(
            parameters=[RecipeParameter(name="port", type="int")]
        )
        with pytest.raises(RecipeError, match="integer"):
            validate_parameters(r, {"port": "abc"})

    def test_float_coercion(self):
        r = self._recipe(
            parameters=[RecipeParameter(name="rate", type="float")]
        )
        result = validate_parameters(r, {"rate": "0.5"})
        assert result["rate"] == 0.5

    def test_bool_coercion(self):
        r = self._recipe(
            parameters=[RecipeParameter(name="verbose", type="bool")]
        )
        result = validate_parameters(r, {"verbose": "true"})
        assert result["verbose"] is True

    def test_bool_false(self):
        r = self._recipe(
            parameters=[RecipeParameter(name="verbose", type="bool")]
        )
        result = validate_parameters(r, {"verbose": "no"})
        assert result["verbose"] is False

    def test_choice_valid(self):
        r = self._recipe(
            parameters=[
                RecipeParameter(
                    name="env", type="choice", choices=["dev", "prod"]
                )
            ]
        )
        result = validate_parameters(r, {"env": "dev"})
        assert result["env"] == "dev"

    def test_choice_invalid(self):
        r = self._recipe(
            parameters=[
                RecipeParameter(
                    name="env", type="choice", choices=["dev", "prod"]
                )
            ]
        )
        with pytest.raises(RecipeError, match="must be one of"):
            validate_parameters(r, {"env": "staging"})

    def test_optional_not_provided(self):
        r = self._recipe(
            parameters=[
                RecipeParameter(name="tag", required=False, default=None)
            ]
        )
        result = validate_parameters(r, {})
        assert "tag" not in result

    def test_no_parameters(self):
        r = self._recipe(parameters=[])
        result = validate_parameters(r, {"extra": "ignored"})
        assert result == {}


class TestFilterRegistry:
    def test_filter_to_subset(self):
        full = ToolRegistry()
        full.register(EchoTool())
        full.register(WriteTool())
        filtered = filter_registry(full, ["echo"])
        assert filtered.get("echo") is not None
        assert filtered.get("write_file") is None

    def test_empty_list_returns_full(self):
        full = ToolRegistry()
        full.register(EchoTool())
        result = filter_registry(full, [])
        assert result is full

    def test_unknown_tool_skipped(self):
        full = ToolRegistry()
        full.register(EchoTool())
        filtered = filter_registry(full, ["echo", "nonexistent"])
        assert filtered.get("echo") is not None
        assert len(filtered.list()) == 1


class TestBuildRecipeContext:
    def test_basic(self):
        r = Recipe(name="test", description="A test recipe")
        ctx = build_recipe_context(r, "rendered prompt here")
        assert "Recipe: test" in ctx
        assert "A test recipe" in ctx
        assert "rendered prompt here" in ctx

    def test_with_output_format(self):
        r = Recipe(
            name="test",
            prompt_template="x",
            output_format="Return JSON with keys: status, details",
        )
        ctx = build_recipe_context(r, "prompt")
        assert "Output Format" in ctx
        assert "Return JSON" in ctx

    def test_no_description_or_output(self):
        r = Recipe(name="test", prompt_template="x")
        ctx = build_recipe_context(r, "prompt")
        assert "Recipe: test" in ctx
        assert "prompt" in ctx
