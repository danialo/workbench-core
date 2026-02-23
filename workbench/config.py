"""
Typed configuration model with precedence-based loader.

Precedence (lowest to highest):
    defaults < config file (YAML) < env vars < CLI flags < per-session overrides
"""

from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass, field, fields, asdict
from pathlib import Path
from typing import Any

import yaml

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Section dataclasses
# ---------------------------------------------------------------------------

@dataclass
class LLMProviderConfig:
    name: str = "openai"
    type: str = "openai"            # "openai" or "claude-code"
    model: str = "gpt-4o"
    api_base: str = ""
    api_key_env: str = ""
    max_context_tokens: int = 128_000
    max_output_tokens: int = 4_096
    temperature: float = 0.0
    timeout_seconds: int = 120
    extra: dict = field(default_factory=dict)


@dataclass
class PolicyConfig:
    max_risk: str = "READ_ONLY"
    confirm_destructive: bool = True
    confirm_shell: bool = True
    confirm_write: bool = False
    blocked_patterns: list[str] = field(default_factory=list)
    allowed_patterns: list[str] = field(default_factory=list)
    redaction_patterns: list[str] = field(default_factory=list)
    audit_log_path: str = "~/.workbench/audit.jsonl"
    audit_max_size_mb: int = 10
    audit_keep_files: int = 5


@dataclass
class ToolsConfig:
    builtin: list[str] = field(default_factory=list)
    disabled: list[str] = field(default_factory=list)


@dataclass
class PluginsConfig:
    enabled: bool = False
    allow_distributions: list[str] = field(default_factory=list)
    allow_tools: list[str] = field(default_factory=list)


@dataclass
class SessionConfig:
    history_db: str = "~/.workbench/history.db"
    max_turns: int = 200
    idle_timeout_seconds: int = 3600


@dataclass
class MCPServerConfig:
    name: str = ""
    transport: str = "stdio"          # "stdio" or "sse"
    command: str = ""                 # stdio: executable name
    args: list[str] = field(default_factory=list)
    env: dict[str, str] = field(default_factory=dict)
    url: str = ""                     # sse: endpoint URL
    headers: dict[str, str] = field(default_factory=dict)
    risk_level: str = "READ_ONLY"     # ToolRisk name applied to all tools from this server
    timeout: float = 30.0
    call_concurrency: int = 1
    acquire_timeout: float = 2.0
    kill_grace_seconds: float = 1.0
    kill_force_seconds: float = 1.0
    stderr_lines_max: int = 200
    stderr_log_level: str = "DEBUG"
    stderr_rate_limit_per_sec: int = 50
    stderr_line_max_chars: int = 2000
    ping_interval_seconds: float = 15.0
    ping_timeout_seconds: float = 5.0
    stable_reset_seconds: float = 60.0
    backoff_initial: float = 1.0
    backoff_max: float = 60.0


@dataclass
class MCPClientsConfig:
    servers: list[MCPServerConfig] = field(default_factory=list)


@dataclass
class BackendsConfig:
    ssh_hosts: list[dict] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Root config
# ---------------------------------------------------------------------------

@dataclass
class WorkbenchConfig:
    llm: LLMProviderConfig = field(default_factory=LLMProviderConfig)
    providers: list[LLMProviderConfig] = field(default_factory=list)
    policy: PolicyConfig = field(default_factory=PolicyConfig)
    tools: ToolsConfig = field(default_factory=ToolsConfig)
    plugins: PluginsConfig = field(default_factory=PluginsConfig)
    session: SessionConfig = field(default_factory=SessionConfig)
    backends: BackendsConfig = field(default_factory=BackendsConfig)
    mcp_clients: MCPClientsConfig = field(default_factory=MCPClientsConfig)
    profiles: dict[str, dict[str, Any]] = field(default_factory=dict)

    # ----- per-session overrides (applied last) ----
    _overrides: dict[str, Any] = field(default_factory=dict, repr=False)

    def set_override(self, dotpath: str, value: Any) -> None:
        """Set a per-session override using dot notation (e.g. 'llm.model')."""
        self._overrides[dotpath] = value
        _apply_dotpath(self, dotpath, value)

    def get_override(self, dotpath: str) -> Any | None:
        return self._overrides.get(dotpath)

    def to_dict(self) -> dict:
        d = asdict(self)
        d.pop("_overrides", None)
        return d


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _apply_dotpath(obj: Any, dotpath: str, value: Any) -> None:
    """Walk obj via dotpath and set the final attribute."""
    parts = dotpath.split(".")
    for part in parts[:-1]:
        obj = getattr(obj, part)
    setattr(obj, parts[-1], value)


def _deep_merge(base: dict, overlay: dict) -> dict:
    """Recursively merge overlay into base, returning a new dict."""
    merged = dict(base)
    for k, v in overlay.items():
        if k in merged and isinstance(merged[k], dict) and isinstance(v, dict):
            merged[k] = _deep_merge(merged[k], v)
        else:
            merged[k] = v
    return merged


def _coerce(value: str, target_type: type) -> Any:
    """Coerce a string env value to the target type."""
    if target_type is bool:
        return value.lower() in ("1", "true", "yes", "on")
    if target_type is int:
        return int(value)
    if target_type is float:
        return float(value)
    if target_type is list:
        return [s.strip() for s in value.split(",") if s.strip()]
    return value


def _build_section(cls: type, raw: dict) -> Any:
    """Build a dataclass section from a raw dict, ignoring unknown keys."""
    valid_fields = {f.name for f in fields(cls)}
    filtered = {k: v for k, v in raw.items() if k in valid_fields}
    return cls(**filtered)


# ---------------------------------------------------------------------------
# ENV var mapping
# ---------------------------------------------------------------------------

_ENV_MAP: dict[str, tuple[str, type]] = {
    "WORKBENCH_LLM_NAME":             ("llm.name", str),
    "WORKBENCH_LLM_MODEL":            ("llm.model", str),
    "WORKBENCH_LLM_API_BASE":         ("llm.api_base", str),
    "WORKBENCH_LLM_API_KEY_ENV":      ("llm.api_key_env", str),
    "WORKBENCH_LLM_MAX_CONTEXT":      ("llm.max_context_tokens", int),
    "WORKBENCH_LLM_MAX_OUTPUT":       ("llm.max_output_tokens", int),
    "WORKBENCH_LLM_TEMPERATURE":      ("llm.temperature", float),
    "WORKBENCH_LLM_TIMEOUT":          ("llm.timeout_seconds", int),
    "WORKBENCH_POLICY_MAX_RISK":      ("policy.max_risk", str),
    "WORKBENCH_POLICY_CONFIRM_DESTR": ("policy.confirm_destructive", bool),
    "WORKBENCH_POLICY_CONFIRM_SHELL": ("policy.confirm_shell", bool),
    "WORKBENCH_POLICY_CONFIRM_WRITE": ("policy.confirm_write", bool),
    "WORKBENCH_POLICY_BLOCKED":       ("policy.blocked_patterns", list),
    "WORKBENCH_POLICY_ALLOWED":       ("policy.allowed_patterns", list),
    "WORKBENCH_POLICY_REDACTION":     ("policy.redaction_patterns", list),
    "WORKBENCH_POLICY_AUDIT_PATH":    ("policy.audit_log_path", str),
    "WORKBENCH_POLICY_AUDIT_SIZE_MB": ("policy.audit_max_size_mb", int),
    "WORKBENCH_POLICY_AUDIT_KEEP":    ("policy.audit_keep_files", int),
    "WORKBENCH_PLUGINS_ENABLED":      ("plugins.enabled", bool),
    "WORKBENCH_SESSION_HISTORY_DB":   ("session.history_db", str),
    "WORKBENCH_SESSION_MAX_TURNS":    ("session.max_turns", int),
    "WORKBENCH_SESSION_IDLE_TIMEOUT": ("session.idle_timeout_seconds", int),
}


# ---------------------------------------------------------------------------
# Environment variable loading
# ---------------------------------------------------------------------------

_EXPORT_RE = re.compile(
    r"""^\s*export\s+([A-Za-z_][A-Za-z0-9_]*)=["']?([^"'\n]*)["']?\s*$"""
)


def load_env_files() -> int:
    """
    Load environment variables from common sources into ``os.environ``.

    Sources checked (in order, later values win):
      1. ``~/.bashrc`` — export lines only
      2. ``~/.zshrc``  — export lines only
      3. ``.env``      — via python-dotenv (project root)
      4. ``~/.env``    — via python-dotenv (home dir)

    Only sets vars that are NOT already in ``os.environ`` (no override).
    Returns the number of new variables loaded.
    """
    loaded = 0

    # --- Shell profile exports (~/.bashrc, ~/.zshrc) ---
    for shell_rc in [Path.home() / ".bashrc", Path.home() / ".zshrc"]:
        if not shell_rc.is_file():
            continue
        try:
            with shell_rc.open("r", encoding="utf-8", errors="replace") as f:
                for line in f:
                    m = _EXPORT_RE.match(line)
                    if m:
                        key, val = m.group(1), m.group(2)
                        if key not in os.environ:
                            os.environ[key] = val
                            loaded += 1
        except OSError:
            pass

    # --- .env files (python-dotenv, does NOT override existing) ---
    try:
        from dotenv import load_dotenv
        for env_path in [Path(".env"), Path.home() / ".env"]:
            if env_path.is_file():
                count_before = len(os.environ)
                load_dotenv(env_path, override=False)
                loaded += len(os.environ) - count_before
    except ImportError:
        pass

    if loaded:
        logger.debug("Loaded %d env vars from shell profiles / .env files", loaded)
    return loaded


# ---------------------------------------------------------------------------
# Loader
# ---------------------------------------------------------------------------

def load_config(
    config_path: str | Path | None = None,
    *,
    profile: str | None = None,
    cli_overrides: dict[str, Any] | None = None,
) -> WorkbenchConfig:
    """
    Build a WorkbenchConfig by layering sources in precedence order:

        defaults  <  config file  <  env vars  <  CLI flags  <  per-session overrides

    Parameters
    ----------
    config_path : path to YAML config file (optional)
    profile : name of a profile to apply from the config file
    cli_overrides : dict of dotpath -> value CLI flag overrides
    """
    # --- 0. Load env vars from .env / shell profiles ---
    load_env_files()

    raw: dict[str, Any] = {}

    # --- 1. Config file ---
    if config_path is not None:
        p = Path(config_path).expanduser()
        if p.is_file():
            with p.open("r", encoding="utf-8") as f:
                file_data = yaml.safe_load(f) or {}
            raw = _deep_merge(raw, file_data)

    # --- 2. Profile overlay ---
    if profile and "profiles" in raw:
        profile_data = raw.get("profiles", {}).get(profile, {})
        if profile_data:
            raw = _deep_merge(raw, profile_data)

    # --- Build sections from raw ---
    extra_providers = [
        _build_section(LLMProviderConfig, p)
        for p in raw.get("providers", [])
        if isinstance(p, dict)
    ]
    cfg = WorkbenchConfig(
        llm=_build_section(LLMProviderConfig, raw.get("llm", {})),
        providers=extra_providers,
        policy=_build_section(PolicyConfig, raw.get("policy", {})),
        tools=_build_section(ToolsConfig, raw.get("tools", {})),
        plugins=_build_section(PluginsConfig, raw.get("plugins", {})),
        session=_build_section(SessionConfig, raw.get("session", {})),
        backends=_build_section(BackendsConfig, raw.get("backends", {})),
        mcp_clients=MCPClientsConfig(
            servers=[
                _build_section(MCPServerConfig, s)
                for s in raw.get("mcp_clients", {}).get("servers", [])
                if isinstance(s, dict)
            ]
        ),
        profiles=raw.get("profiles", {}),
    )

    # --- 3. Env var overrides ---
    for env_var, (dotpath, target_type) in _ENV_MAP.items():
        val = os.environ.get(env_var)
        if val is not None:
            _apply_dotpath(cfg, dotpath, _coerce(val, target_type))

    # --- 4. CLI flag overrides ---
    if cli_overrides:
        for dotpath, value in cli_overrides.items():
            _apply_dotpath(cfg, dotpath, value)

    return cfg
