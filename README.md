# Workbench Core

Portable support and diagnostics workbench runtime.

- Orchestrator loop (LLM + tools + events)
- Tool contract, registry, validation, policy
- Provider-agnostic LLM router with streaming and tool-call assembly
- Session event log, artifact store, replay, runbook export
- Interfaces: CLI first, then TUI, then VS Code, then optional Web

Adapters (SSH, K8s, vendor APIs, ticketing systems) plug in later via entry points.

* * * * *
Disclaimer: This project was vibe coded with Claude Code.
