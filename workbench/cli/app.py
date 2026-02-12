"""
Main CLI application for workbench-core.

Usage:
    wb chat [--provider NAME] [--profile NAME] [--session ID]
    wb sessions list|show|delete|export
    wb tools list|info
    wb config show|validate
    wb version
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import sys
from pathlib import Path
from typing import Optional

import typer
from rich.console import Console

from workbench.config import load_config

app = typer.Typer(name="wb", help="Workbench - Support & Diagnostics CLI")
sessions_app = typer.Typer(help="Session management")
tools_app = typer.Typer(help="Tool management")
config_app = typer.Typer(help="Configuration management")

app.add_typer(sessions_app, name="sessions")
app.add_typer(tools_app, name="tools")
app.add_typer(config_app, name="config")

console = Console()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _get_config_path() -> Path | None:
    """Find config file in standard locations."""
    candidates = [
        Path.cwd() / "workbench.yaml",
        Path.cwd() / "workbench.yml",
        Path.home() / ".config" / "workbench" / "config.yaml",
        Path.home() / ".workbench" / "config.yaml",
    ]
    for p in candidates:
        if p.is_file():
            return p
    return None


async def _setup_stack(
    provider: str | None = None,
    profile: str | None = None,
    session_id: str | None = None,
):
    """Wire up the full stack for chat."""
    from workbench.cli.chat import ChatHandler
    from workbench.cli.output import OutputFormatter
    from workbench.llm.router import LLMRouter
    from workbench.llm.token_counter import TokenCounter
    from workbench.orchestrator.core import Orchestrator
    from workbench.prompts.system import build_system_prompt
    from workbench.session.artifacts import ArtifactStore
    from workbench.session.session import Session
    from workbench.session.store import SessionStore
    from workbench.tools.base import ToolRisk
    from workbench.tools.policy import PolicyEngine
    from workbench.tools.registry import ToolRegistry

    config_path = _get_config_path()
    cfg = load_config(config_path, profile=profile)

    # Session store
    store = SessionStore(cfg.session.history_db)
    await store.init()

    # Artifact store
    artifact_dir = Path(cfg.policy.audit_log_path).parent / "artifacts"
    artifact_store = ArtifactStore(str(artifact_dir))

    # Token counter
    token_counter = TokenCounter(cfg.llm.model)

    # Session
    session = Session(store, artifact_store, token_counter)
    if session_id:
        await session.resume(session_id)
    else:
        await session.start({"profile": profile or "default"})

    # Tool registry
    registry = ToolRegistry()

    # Backend bridge tools — route through BackendRouter
    _log = logging.getLogger(__name__)
    router = None
    try:
        from workbench.backends.bridge import (
            ListDiagnosticsTool,
            ResolveTargetTool,
            RunDiagnosticTool,
            RunShellTool,
            SummarizeArtifactTool,
        )
        from workbench.backends.local import LocalBackend
        from workbench.backends.router import BackendRouter
        from workbench.backends.ssh import SSHBackend

        router = BackendRouter()
        router.set_default(LocalBackend())

        # Connect SSH backends from config
        for host_cfg in cfg.backends.ssh_hosts:
            ssh = SSHBackend(
                host=host_cfg["host"],
                port=host_cfg.get("port", 22),
                username=host_cfg.get("username", "root"),
                key_path=host_cfg.get("key_path"),
                password=os.environ.get(host_cfg.get("password_env", "")) if host_cfg.get("password_env") else None,
                timeout=host_cfg.get("timeout", 10),
            )
            try:
                await ssh.connect()
                router.register(host_cfg["name"], ssh)
                router.register(host_cfg["host"], ssh)
                _log.info("SSH connected: %s (%s)", host_cfg["name"], host_cfg["host"])
            except Exception as e:
                _log.warning("SSH connect failed for %s: %s", host_cfg.get("name", host_cfg["host"]), e)

        registry.register(ResolveTargetTool(router))
        registry.register(ListDiagnosticsTool(router))
        registry.register(RunDiagnosticTool(router))
        registry.register(RunShellTool(router))
        registry.register(SummarizeArtifactTool(artifact_store))
    except Exception:
        _log.exception("Failed to register backend tools")

    # Load plugins (after router so plugins can receive the backend)
    registry.load_plugins(
        enabled=cfg.plugins.enabled,
        allow_distributions=set(cfg.plugins.allow_distributions) if cfg.plugins.allow_distributions else None,
        allow_tools=set(cfg.plugins.allow_tools) if cfg.plugins.allow_tools else None,
        backend=router,
    )

    # Policy
    risk_map = {r.name: r for r in ToolRisk}
    max_risk = risk_map.get(cfg.policy.max_risk, ToolRisk.READ_ONLY)
    policy = PolicyEngine(
        max_risk=max_risk,
        confirm_destructive=cfg.policy.confirm_destructive,
        confirm_shell=cfg.policy.confirm_shell,
        confirm_write=cfg.policy.confirm_write,
        blocked_patterns=cfg.policy.blocked_patterns,
        redaction_patterns=cfg.policy.redaction_patterns,
        audit_log_path=cfg.policy.audit_log_path,
        audit_max_size_mb=cfg.policy.audit_max_size_mb,
        audit_keep_files=cfg.policy.audit_keep_files,
    )

    # LLM Router - try to set up provider from config
    router = LLMRouter()
    try:
        from workbench.llm.providers.openai_compat import OpenAICompatProvider
        import os

        api_key = os.environ.get(cfg.llm.api_key_env, "not-needed")
        llm_provider = OpenAICompatProvider(
            url=cfg.llm.api_base or "http://localhost:3333/v1",
            model=cfg.llm.model,
            api_key=api_key,
            timeout=float(cfg.llm.timeout_seconds),
        )
        router.register_provider(cfg.llm.name, llm_provider)
    except Exception as e:
        console.print(f"[yellow]Warning:[/yellow] Could not set up LLM provider: {e}")
        console.print("[dim]Chat will not work without a configured LLM provider.[/dim]")

    if provider and provider != cfg.llm.name:
        try:
            router.set_active(provider)
        except KeyError:
            console.print(f"[yellow]Warning:[/yellow] Provider '{provider}' not found, using default.")

    # System prompt
    system_prompt = build_system_prompt(tools=registry.list())

    # Orchestrator
    chat_handler = ChatHandler(orchestrator=None, console=console)

    orchestrator = Orchestrator(
        session=session,
        registry=registry,
        router=router,
        policy=policy,
        system_prompt=system_prompt,
        tool_timeout=float(cfg.llm.timeout_seconds),
        max_turns=cfg.session.max_turns,
        confirmation_callback=chat_handler.confirm_tool,
    )

    chat_handler.orchestrator = orchestrator

    return chat_handler, store


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------

@app.command()
def chat(
    provider: Optional[str] = typer.Option(None, help="LLM provider name"),
    profile: Optional[str] = typer.Option(None, help="Config profile name"),
    session: Optional[str] = typer.Option(None, "--session", help="Resume session ID"),
):
    """Start an interactive chat session."""

    async def _run():
        handler, store = await _setup_stack(provider, profile, session)
        try:
            await handler.run_loop()
        finally:
            await store.close()

    asyncio.run(_run())


@sessions_app.command("list")
def sessions_list():
    """List all sessions."""

    async def _run():
        from workbench.cli.output import OutputFormatter
        from workbench.session.store import SessionStore

        cfg = load_config(_get_config_path())
        store = SessionStore(cfg.session.history_db)
        await store.init()
        sessions = await store.list_sessions()
        formatter = OutputFormatter(console)
        formatter.format_session_list(sessions)
        await store.close()

    asyncio.run(_run())


@sessions_app.command("show")
def sessions_show(session_id: str = typer.Argument(..., help="Session ID")):
    """Show session events."""

    async def _run():
        from workbench.cli.output import OutputFormatter
        from workbench.session.store import SessionStore

        cfg = load_config(_get_config_path())
        store = SessionStore(cfg.session.history_db)
        await store.init()
        events = await store.get_events(session_id)
        formatter = OutputFormatter(console)
        formatter.format_session_events(events)
        await store.close()

    asyncio.run(_run())


@sessions_app.command("delete")
def sessions_delete(session_id: str = typer.Argument(..., help="Session ID")):
    """Delete a session."""

    async def _run():
        from workbench.session.store import SessionStore

        cfg = load_config(_get_config_path())
        store = SessionStore(cfg.session.history_db)
        await store.init()
        await store.delete_session(session_id)
        console.print(f"Deleted session: {session_id}")
        await store.close()

    asyncio.run(_run())


@sessions_app.command("export")
def sessions_export(
    session_id: str = typer.Argument(..., help="Session ID"),
    fmt: str = typer.Option("markdown", "--format", "-f", help="Export format: runbook, markdown, json"),
):
    """Export session as runbook/markdown/json."""

    async def _run():
        from workbench.cli.output import OutputFormatter
        from workbench.session.store import SessionStore

        cfg = load_config(_get_config_path())
        store = SessionStore(cfg.session.history_db)
        await store.init()
        events = await store.get_events(session_id)
        formatter = OutputFormatter(console)
        output = formatter.export_session(events, fmt)
        console.print(output)
        await store.close()

    asyncio.run(_run())


@tools_app.command("list")
def tools_list(
    max_risk: Optional[str] = typer.Option(None, help="Max risk level filter"),
):
    """List registered tools."""
    from workbench.cli.output import OutputFormatter
    from workbench.tools.base import ToolRisk
    from workbench.tools.registry import ToolRegistry

    registry = ToolRegistry()

    # Load tools
    try:
        from workbench.backends.bridge import (
            ListDiagnosticsTool,
            ResolveTargetTool,
            RunDiagnosticTool,
            RunShellTool,
            SummarizeArtifactTool,
        )
        from workbench.backends.demo import DemoBackend
        from workbench.backends.local import LocalBackend
        from workbench.session.artifacts import ArtifactStore
        import tempfile

        backend = DemoBackend()
        registry.register(ResolveTargetTool(backend))
        registry.register(ListDiagnosticsTool(backend))
        registry.register(RunDiagnosticTool(backend))
        registry.register(SummarizeArtifactTool(ArtifactStore(tempfile.mkdtemp())))

        local_backend = LocalBackend()
        registry.register(RunShellTool(local_backend))
    except Exception:
        pass

    risk_filter = None
    if max_risk:
        risk_map = {r.name: r for r in ToolRisk}
        risk_filter = risk_map.get(max_risk.upper())

    tools = registry.list(max_risk=risk_filter)
    formatter = OutputFormatter(console)
    formatter.format_tool_list(tools)


@tools_app.command("info")
def tools_info(tool_name: str = typer.Argument(..., help="Tool name")):
    """Show tool details and schema."""
    from workbench.cli.output import OutputFormatter
    from workbench.tools.registry import ToolRegistry

    registry = ToolRegistry()

    try:
        from workbench.backends.bridge import (
            ListDiagnosticsTool,
            ResolveTargetTool,
            RunDiagnosticTool,
            RunShellTool,
            SummarizeArtifactTool,
        )
        from workbench.backends.demo import DemoBackend
        from workbench.backends.local import LocalBackend
        from workbench.session.artifacts import ArtifactStore
        import tempfile

        backend = DemoBackend()
        registry.register(ResolveTargetTool(backend))
        registry.register(ListDiagnosticsTool(backend))
        registry.register(RunDiagnosticTool(backend))
        registry.register(SummarizeArtifactTool(ArtifactStore(tempfile.mkdtemp())))

        local_backend = LocalBackend()
        registry.register(RunShellTool(local_backend))
    except Exception:
        pass

    tool = registry.get(tool_name)
    if not tool:
        console.print(f"[red]Tool not found:[/red] {tool_name}")
        raise typer.Exit(1)

    formatter = OutputFormatter(console)
    formatter.format_tool_info(tool)


@config_app.command("show")
def config_show():
    """Show effective config."""
    from workbench.cli.output import OutputFormatter

    cfg = load_config(_get_config_path())
    formatter = OutputFormatter(console)
    formatter.format_config(cfg.to_dict())


@config_app.command("validate")
def config_validate():
    """Validate config and show any type issues."""
    config_path = _get_config_path()
    try:
        cfg = load_config(config_path)
        console.print("[green]Config is valid.[/green]")
        if config_path:
            console.print(f"  Loaded from: {config_path}")
        else:
            console.print("  [dim]No config file found, using defaults.[/dim]")
        console.print(f"  LLM provider: {cfg.llm.name} ({cfg.llm.model})")
        console.print(f"  Policy max risk: {cfg.policy.max_risk}")
        console.print(f"  Plugins enabled: {cfg.plugins.enabled}")
    except Exception as e:
        console.print(f"[red]Config validation failed:[/red] {e}")
        raise typer.Exit(1)


@app.command()
def tui(
    provider: Optional[str] = typer.Option(None, help="LLM provider name"),
    profile: Optional[str] = typer.Option(None, help="Config profile name"),
    session: Optional[str] = typer.Option(None, "--session", help="Resume session ID"),
):
    """Launch the split-pane TUI interface."""
    from workbench.tui.app import launch_tui

    asyncio.run(launch_tui(provider, profile, session))


@app.command()
def version():
    """Show version."""
    console.print("workbench-core v0.1.0")


def main():
    app()


if __name__ == "__main__":
    main()
