import jsonschema

from workbench.tools.base import Tool, normalize_schema


class ToolValidator:
    @staticmethod
    def validate(tool: Tool, arguments: dict) -> tuple[bool, str | None]:
        try:
            jsonschema.validate(
                instance=arguments,
                schema=normalize_schema(tool.parameters),
            )
            return True, None
        except jsonschema.ValidationError as e:
            return False, str(e.message)
