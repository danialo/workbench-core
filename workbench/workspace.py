"""
Workspace model and manager.

Two-tier workspace system for the support workbench:

- **Global workspace**: always present, holds shared tools/config/connections.
  Acts as the default for anything not overridden at the project level.
- **Project workspaces**: scoped to a directory (local or remote), with
  per-project config overrides, tool settings, and conversations.

Workspaces are persisted in ``~/.workbench/workspaces.json``.
"""

from __future__ import annotations

import json
import logging
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# Default location for the workspaces file
_DEFAULT_WORKSPACES_PATH = "~/.workbench/workspaces.json"

# The global workspace has a fixed ID
GLOBAL_WORKSPACE_ID = "global"


# ---------------------------------------------------------------------------
# Workspace model
# ---------------------------------------------------------------------------

@dataclass
class Workspace:
    """A workspace scope for the support workbench."""

    workspace_id: str
    name: str
    type: str = "project"       # "global" | "project"
    path: str = ""              # Directory scope (local path or remote path)
    backend: str = "local"      # Execution backend: "local" | SSH target name
    config_overrides: dict[str, Any] = field(default_factory=dict)
    tools_enabled: list[str] = field(default_factory=list)
    tools_disabled: list[str] = field(default_factory=list)
    created_at: str = ""
    last_opened: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> Workspace:
        """Build a Workspace from a JSON-compatible dict, ignoring unknown keys."""
        known = {f.name for f in cls.__dataclass_fields__.values()}
        filtered = {k: v for k, v in d.items() if k in known}
        return cls(**filtered)

    @property
    def is_global(self) -> bool:
        return self.type == "global"


def _make_global() -> Workspace:
    """Create the default global workspace."""
    now = datetime.now(timezone.utc).isoformat()
    return Workspace(
        workspace_id=GLOBAL_WORKSPACE_ID,
        name="Global",
        type="global",
        path="",
        backend="local",
        created_at=now,
        last_opened=now,
    )


# ---------------------------------------------------------------------------
# WorkspaceManager — JSON file persistence
# ---------------------------------------------------------------------------

class WorkspaceManager:
    """
    Manages workspaces with JSON file persistence.

    The file stores a list of workspace objects.  On first use, a
    global workspace is created automatically.

    Usage::

        mgr = WorkspaceManager()
        mgr.load()
        ws = mgr.create("my-project", path="/home/d/projects/api", backend="vps1")
        mgr.save()
    """

    def __init__(self, path: str | Path | None = None) -> None:
        self._path = Path(path or _DEFAULT_WORKSPACES_PATH).expanduser().resolve()
        self._workspaces: dict[str, Workspace] = {}
        self._loaded = False

    @property
    def path(self) -> Path:
        return self._path

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------

    def load(self) -> None:
        """Load workspaces from disk.  Creates file + global workspace if missing."""
        if self._path.exists():
            try:
                raw = json.loads(self._path.read_text(encoding="utf-8"))
                ws_list = raw.get("workspaces", []) if isinstance(raw, dict) else raw
                self._workspaces = {}
                for d in ws_list:
                    ws = Workspace.from_dict(d)
                    self._workspaces[ws.workspace_id] = ws
            except (json.JSONDecodeError, KeyError, TypeError) as e:
                logger.warning("Failed to parse workspaces.json, resetting: %s", e)
                self._workspaces = {}

        # Ensure global workspace always exists
        if GLOBAL_WORKSPACE_ID not in self._workspaces:
            self._workspaces[GLOBAL_WORKSPACE_ID] = _make_global()
            self.save()

        self._loaded = True

    def save(self) -> None:
        """Write current workspaces to disk."""
        self._path.parent.mkdir(parents=True, exist_ok=True)
        data = {
            "version": 1,
            "workspaces": [ws.to_dict() for ws in self._workspaces.values()],
        }
        self._path.write_text(
            json.dumps(data, indent=2, default=str) + "\n",
            encoding="utf-8",
        )

    # ------------------------------------------------------------------
    # Accessors
    # ------------------------------------------------------------------

    def get_global(self) -> Workspace:
        """Return the global workspace (always exists)."""
        self._ensure_loaded()
        return self._workspaces[GLOBAL_WORKSPACE_ID]

    def get(self, workspace_id: str) -> Workspace | None:
        """Return a workspace by ID, or None."""
        self._ensure_loaded()
        return self._workspaces.get(workspace_id)

    def list_all(self) -> list[Workspace]:
        """Return all workspaces, global first."""
        self._ensure_loaded()
        result = []
        if GLOBAL_WORKSPACE_ID in self._workspaces:
            result.append(self._workspaces[GLOBAL_WORKSPACE_ID])
        for ws in self._workspaces.values():
            if ws.workspace_id != GLOBAL_WORKSPACE_ID:
                result.append(ws)
        return result

    def list_projects(self) -> list[Workspace]:
        """Return only project workspaces."""
        return [ws for ws in self.list_all() if ws.type == "project"]

    def list_recent(self, limit: int = 10) -> list[Workspace]:
        """Return recently opened workspaces."""
        projects = self.list_projects()
        projects.sort(key=lambda w: w.last_opened or "", reverse=True)
        return projects[:limit]

    # ------------------------------------------------------------------
    # Mutations
    # ------------------------------------------------------------------

    def create(
        self,
        name: str,
        *,
        path: str = "",
        backend: str = "local",
        config_overrides: dict[str, Any] | None = None,
        tools_enabled: list[str] | None = None,
        tools_disabled: list[str] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> Workspace:
        """Create a new project workspace and save."""
        self._ensure_loaded()
        now = datetime.now(timezone.utc).isoformat()
        ws = Workspace(
            workspace_id=str(uuid.uuid4()),
            name=name,
            type="project",
            path=path,
            backend=backend,
            config_overrides=config_overrides or {},
            tools_enabled=tools_enabled or [],
            tools_disabled=tools_disabled or [],
            created_at=now,
            last_opened=now,
            metadata=metadata or {},
        )
        self._workspaces[ws.workspace_id] = ws
        self.save()
        logger.info("Created workspace %s (%s) at %s", ws.name, ws.workspace_id, ws.path)
        return ws

    def update(self, workspace_id: str, **fields: Any) -> Workspace | None:
        """Update workspace fields and save.  Returns updated workspace or None."""
        self._ensure_loaded()
        ws = self._workspaces.get(workspace_id)
        if ws is None:
            return None

        for key, value in fields.items():
            if hasattr(ws, key) and key not in ("workspace_id", "type", "created_at"):
                setattr(ws, key, value)

        self.save()
        return ws

    def delete(self, workspace_id: str) -> bool:
        """Delete a workspace.  Cannot delete the global workspace.  Returns success."""
        self._ensure_loaded()
        if workspace_id == GLOBAL_WORKSPACE_ID:
            logger.warning("Cannot delete global workspace")
            return False
        if workspace_id not in self._workspaces:
            return False

        del self._workspaces[workspace_id]
        self.save()
        logger.info("Deleted workspace %s", workspace_id)
        return True

    def open_workspace(self, workspace_id: str) -> Workspace | None:
        """Mark a workspace as opened (updates last_opened timestamp)."""
        ws = self.get(workspace_id)
        if ws is None:
            return None
        ws.last_opened = datetime.now(timezone.utc).isoformat()
        self.save()
        return ws

    # ------------------------------------------------------------------
    # Config resolution
    # ------------------------------------------------------------------

    def get_effective_config(self, workspace_id: str) -> dict[str, Any]:
        """
        Resolve effective config for a workspace.

        Merges: global config_overrides  <  project config_overrides
        """
        global_ws = self.get_global()
        base = dict(global_ws.config_overrides)

        if workspace_id == GLOBAL_WORKSPACE_ID:
            return base

        ws = self.get(workspace_id)
        if ws is None:
            return base

        return _deep_merge(base, ws.config_overrides)

    def get_effective_tools(
        self, workspace_id: str, all_tools: list[str] | None = None
    ) -> tuple[list[str], list[str]]:
        """
        Resolve effective tool enable/disable lists for a workspace.

        Returns (enabled, disabled) where project overrides global.
        """
        global_ws = self.get_global()
        enabled = list(global_ws.tools_enabled)
        disabled = list(global_ws.tools_disabled)

        if workspace_id != GLOBAL_WORKSPACE_ID:
            ws = self.get(workspace_id)
            if ws is not None:
                if ws.tools_enabled:
                    enabled = list(ws.tools_enabled)
                disabled = list(set(disabled) | set(ws.tools_disabled))

        return enabled, disabled

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _ensure_loaded(self) -> None:
        if not self._loaded:
            self.load()


def _deep_merge(base: dict, overlay: dict) -> dict:
    """Recursively merge overlay into base."""
    merged = dict(base)
    for k, v in overlay.items():
        if k in merged and isinstance(merged[k], dict) and isinstance(v, dict):
            merged[k] = _deep_merge(merged[k], v)
        else:
            merged[k] = v
    return merged
