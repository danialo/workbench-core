"""Diagnostics catalog -- registry of available diagnostic actions."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class DiagnosticAction:
    """Describes a diagnostic action that can be run against targets."""

    name: str
    description: str
    category: str
    target_types: list[str]
    parameters: dict = field(default_factory=dict)
    risk_level: str = "read_only"


class DiagnosticsCatalog:
    """Registry of diagnostic actions."""

    def __init__(self) -> None:
        self._actions: dict[str, DiagnosticAction] = {}

    def register(self, action: DiagnosticAction) -> None:
        self._actions[action.name] = action

    def get(self, name: str) -> DiagnosticAction | None:
        return self._actions.get(name)

    def list_all(self) -> list[DiagnosticAction]:
        return sorted(self._actions.values(), key=lambda a: a.name)

    def list_for_target(self, target_type: str) -> list[DiagnosticAction]:
        return sorted(
            [a for a in self._actions.values() if target_type in a.target_types],
            key=lambda a: a.name,
        )

    def list_by_category(self, category: str) -> list[DiagnosticAction]:
        return sorted(
            [a for a in self._actions.values() if a.category == category],
            key=lambda a: a.name,
        )
