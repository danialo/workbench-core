from enum import IntEnum, Enum
from abc import ABC, abstractmethod

from workbench.types import ToolResult


class ToolRisk(IntEnum):
    READ_ONLY = 10
    WRITE = 20
    DESTRUCTIVE = 30
    SHELL = 40


class PrivacyScope(Enum):
    PUBLIC = "public"
    SENSITIVE = "sensitive"
    SECRET = "secret"


def normalize_schema(schema: dict) -> dict:
    s = dict(schema or {})
    s.setdefault("type", "object")
    s.setdefault("additionalProperties", False)
    return s


class Tool(ABC):
    @property
    @abstractmethod
    def name(self) -> str: ...

    @property
    @abstractmethod
    def description(self) -> str: ...

    @property
    @abstractmethod
    def parameters(self) -> dict: ...

    @property
    def risk_level(self) -> ToolRisk:
        return ToolRisk.READ_ONLY

    @property
    def confirmation_hint(self) -> bool:
        return self.risk_level >= ToolRisk.DESTRUCTIVE

    @property
    def privacy_scope(self) -> PrivacyScope:
        return PrivacyScope.PUBLIC

    @property
    def secret_fields(self) -> list[str]:
        return []

    @abstractmethod
    async def execute(self, **kwargs) -> ToolResult: ...

    def to_openai_schema(self) -> dict:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": normalize_schema(self.parameters),
            },
        }
