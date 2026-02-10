"""Execution Backend Interface (abstract)."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field


class BackendError(Exception):
    """Structured error from a backend operation."""

    def __init__(self, message: str, code: str = ""):
        super().__init__(message)
        self.code = code


@dataclass
class DiagnosticInfo:
    """Describes a single diagnostic action available for a target."""

    name: str
    description: str
    target_type: str
    parameters: dict = field(default_factory=dict)


class ExecutionBackend(ABC):
    """
    Abstract interface for execution backends.

    Concrete adapters (SSH, K8s, vendor APIs) implement this interface
    and are loaded via entry points in the adapter pack repo.
    """

    @abstractmethod
    async def resolve_target(self, target: str, **kwargs) -> dict:
        """Resolve a target identifier to structured info."""
        ...

    @abstractmethod
    async def list_diagnostics(self, target: str, **kwargs) -> list[DiagnosticInfo]:
        """List available diagnostics for a target."""
        ...

    @abstractmethod
    async def run_diagnostic(self, action: str, target: str, **kwargs) -> dict:
        """Run a diagnostic action against a target."""
        ...

    async def run_shell(self, command: str, target: str, **kwargs) -> dict:
        """Optional structured shell execution. Default raises BackendError."""
        raise BackendError(
            "Shell execution not supported by this backend",
            code="not_supported",
        )
