from __future__ import annotations

from importlib.metadata import entry_points

from workbench.tools.base import Tool, ToolRisk


class ToolRegistry:
    def __init__(self):
        self._tools: dict[str, Tool] = {}

    def register(self, tool: Tool, *, overwrite: bool = False) -> None:
        if tool.name in self._tools and not overwrite:
            raise ValueError(f"Tool already registered: {tool.name}")
        self._tools[tool.name] = tool

    def get(self, name: str) -> Tool | None:
        return self._tools.get(name)

    def require(self, name: str) -> Tool:
        t = self.get(name)
        if not t:
            raise KeyError(name)
        return t

    def list(self, max_risk: ToolRisk | None = None) -> list[Tool]:
        tools = list(self._tools.values())
        if max_risk is None:
            return sorted(tools, key=lambda t: t.name)
        return sorted(
            [t for t in tools if t.risk_level <= max_risk],
            key=lambda t: t.name,
        )

    def to_openai_schema(self) -> list[dict]:
        return [t.to_openai_schema() for t in self.list()]

    def load_plugins(
        self,
        *,
        enabled: bool,
        group: str = "workbench.tools",
        allow_distributions: set[str] | None = None,
        allow_tools: set[str] | None = None,
    ) -> int:
        if not enabled:
            return 0
        loaded = 0
        for ep in entry_points(group=group):
            dist = getattr(ep, "dist", None)
            dist_name = getattr(dist, "name", None)
            if allow_distributions and dist_name and dist_name not in allow_distributions:
                continue
            if allow_tools and ep.name not in allow_tools:
                continue
            tool_cls = ep.load()
            self.register(tool_cls())
            loaded += 1
        return loaded
