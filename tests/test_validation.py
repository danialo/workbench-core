"""Tests for ToolValidator."""

import pytest

from workbench.tools.validation import ToolValidator
from tests.mock_tools import EchoTool, WriteTool, ExtraKeysTool


class TestToolValidator:
    """Test suite for ToolValidator.validate()."""

    def test_valid_args_pass(self):
        tool = EchoTool()
        ok, err = ToolValidator.validate(tool, {"message": "hello"})
        assert ok is True
        assert err is None

    def test_valid_args_multiple_fields(self):
        tool = WriteTool()
        ok, err = ToolValidator.validate(tool, {"path": "/tmp/x", "content": "data"})
        assert ok is True
        assert err is None

    def test_missing_required_arg_fails(self):
        tool = EchoTool()
        ok, err = ToolValidator.validate(tool, {})
        assert ok is False
        assert err is not None
        assert "message" in err.lower() or "required" in err.lower()

    def test_missing_one_of_multiple_required(self):
        tool = WriteTool()
        ok, err = ToolValidator.validate(tool, {"path": "/tmp/x"})
        assert ok is False
        assert err is not None

    def test_extra_unknown_keys_rejected(self):
        tool = EchoTool()
        ok, err = ToolValidator.validate(tool, {"message": "hello", "rogue": "value"})
        assert ok is False
        assert err is not None

    def test_additional_properties_true_allows_extra_keys(self):
        tool = ExtraKeysTool()
        ok, err = ToolValidator.validate(
            tool, {"base_param": "hello", "extra": "stuff", "another": 42}
        )
        assert ok is True
        assert err is None

    def test_type_mismatch_string_vs_integer(self):
        tool = EchoTool()
        ok, err = ToolValidator.validate(tool, {"message": 12345})
        assert ok is False
        assert err is not None

    def test_type_mismatch_string_vs_object(self):
        tool = EchoTool()
        ok, err = ToolValidator.validate(tool, {"message": {"nested": True}})
        assert ok is False
        assert err is not None

    def test_empty_dict_for_no_required_fields(self):
        """A tool with no required fields should accept an empty dict."""
        tool = ExtraKeysTool.__new__(ExtraKeysTool)
        # Override parameters to have no required fields
        class NoRequiredTool(ExtraKeysTool):
            @property
            def parameters(self) -> dict:
                return {
                    "type": "object",
                    "properties": {
                        "optional_param": {"type": "string"},
                    },
                    "additionalProperties": True,
                }

        tool = NoRequiredTool()
        ok, err = ToolValidator.validate(tool, {})
        assert ok is True
        assert err is None
