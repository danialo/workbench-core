"""Mock tool implementations for testing."""

from workbench.tools.base import Tool, ToolRisk, PrivacyScope
from workbench.types import ToolResult


class EchoTool(Tool):
    @property
    def name(self) -> str:
        return "echo"

    @property
    def description(self) -> str:
        return "Echoes the input message back."

    @property
    def parameters(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "message": {"type": "string", "description": "Message to echo"},
            },
            "required": ["message"],
        }

    @property
    def risk_level(self) -> ToolRisk:
        return ToolRisk.READ_ONLY

    @property
    def privacy_scope(self) -> PrivacyScope:
        return PrivacyScope.PUBLIC

    async def execute(self, **kwargs) -> ToolResult:
        msg = kwargs.get("message", "")
        return ToolResult(success=True, content=msg)


class WriteTool(Tool):
    @property
    def name(self) -> str:
        return "write_file"

    @property
    def description(self) -> str:
        return "Writes content to a file path."

    @property
    def parameters(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "File path to write"},
                "content": {"type": "string", "description": "Content to write"},
            },
            "required": ["path", "content"],
        }

    @property
    def risk_level(self) -> ToolRisk:
        return ToolRisk.WRITE

    @property
    def privacy_scope(self) -> PrivacyScope:
        return PrivacyScope.PUBLIC

    async def execute(self, **kwargs) -> ToolResult:
        return ToolResult(
            success=True,
            content=f"Wrote to {kwargs.get('path', '')}",
        )


class DestructiveTool(Tool):
    @property
    def name(self) -> str:
        return "delete_resource"

    @property
    def description(self) -> str:
        return "Deletes a resource by ID."

    @property
    def parameters(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "resource_id": {"type": "string", "description": "Resource to delete"},
            },
            "required": ["resource_id"],
        }

    @property
    def risk_level(self) -> ToolRisk:
        return ToolRisk.DESTRUCTIVE

    @property
    def privacy_scope(self) -> PrivacyScope:
        return PrivacyScope.SENSITIVE

    async def execute(self, **kwargs) -> ToolResult:
        return ToolResult(
            success=True,
            content=f"Deleted {kwargs.get('resource_id', '')}",
        )


class ShellTool(Tool):
    @property
    def name(self) -> str:
        return "shell"

    @property
    def description(self) -> str:
        return "Executes a shell command."

    @property
    def parameters(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "command": {"type": "string", "description": "Shell command to run"},
                "timeout": {"type": "integer", "description": "Timeout in seconds"},
            },
            "required": ["command"],
        }

    @property
    def risk_level(self) -> ToolRisk:
        return ToolRisk.SHELL

    @property
    def privacy_scope(self) -> PrivacyScope:
        return PrivacyScope.SECRET

    @property
    def secret_fields(self) -> list[str]:
        return ["command"]

    async def execute(self, **kwargs) -> ToolResult:
        return ToolResult(
            success=True,
            content=f"Executed: {kwargs.get('command', '')}",
        )


class ExtraKeysTool(Tool):
    """A tool that explicitly allows additionalProperties in its schema."""

    @property
    def name(self) -> str:
        return "flexible"

    @property
    def description(self) -> str:
        return "Accepts arbitrary extra keys."

    @property
    def parameters(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "base_param": {"type": "string", "description": "A base parameter"},
            },
            "required": ["base_param"],
            "additionalProperties": True,
        }

    @property
    def risk_level(self) -> ToolRisk:
        return ToolRisk.READ_ONLY

    @property
    def privacy_scope(self) -> PrivacyScope:
        return PrivacyScope.PUBLIC

    async def execute(self, **kwargs) -> ToolResult:
        return ToolResult(success=True, content=str(kwargs))
