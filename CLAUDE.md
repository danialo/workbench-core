# CLAUDE.md — Project Guide for AI Agents

## What This Is

workbench-core is a portable operations assistant runtime. It provides an LLM orchestrator with tool calling, policy enforcement, session persistence, and both CLI and TUI interfaces. No vendor lock-in — works with any OpenAI-compatible API endpoint.

## Setup

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
cp workbench.yaml.example workbench.yaml   # then edit with your LLM provider
pytest tests/ -v                            # 171 tests, all should pass
```

The `wb` command is installed as an entry point. Config is loaded from:
`workbench.yaml` (repo root) > `~/.config/workbench/config.yaml` > `~/.workbench/config.yaml`

**Important**: `workbench.yaml` is gitignored (contains API keys). The template is `workbench.yaml.example`.

## Architecture

```
User → Interface (CLI/TUI) → Orchestrator → LLM Provider (streaming)
                                  ↕
                           Tool Registry → Policy Engine → Execution Backend
                                  ↕
                           Session Store (SQLite, append-only events)
```

The orchestrator loop: build context → call LLM → if tool calls, validate + enforce policy + execute → feed results back → repeat until LLM responds with text only.

## Key Files

| File | What It Does |
|------|-------------|
| `workbench/orchestrator/core.py` | Main loop — async generator yielding `StreamChunk` |
| `workbench/llm/providers/openai_compat.py` | HTTP streaming to any OpenAI-compatible API |
| `workbench/tools/base.py` | `Tool` ABC, `ToolRisk` IntEnum (READ_ONLY=10, WRITE=20, DESTRUCTIVE=30, SHELL=40) |
| `workbench/tools/policy.py` | `PolicyEngine` — risk gating, confirmation, blocked patterns, audit logging |
| `workbench/tools/registry.py` | `ToolRegistry` — register/lookup tools, plugin loading via entry points |
| `workbench/backends/local.py` | `LocalBackend` — real shell execution via `asyncio.create_subprocess_exec` |
| `workbench/backends/bridge.py` | Bridge tools that wrap backends into the tool interface |
| `workbench/session/store.py` | SQLite session store with `asyncio.Lock` (loop-bound, do NOT use from threads) |
| `workbench/config.py` | Typed dataclass config with YAML + env var loading |
| `workbench/tui/app.py` | Textual windowed TUI with hotkeys |
| `workbench/tui/windows/chat_window.py` | Chat window — streaming LLM responses + tool calls |
| `workbench/cli/app.py` | Typer CLI — entry point for `wb` command |

## Conventions

- **Python 3.12+**, async throughout. Use `asyncio` not threads.
- **Ruff** for linting: `ruff check workbench/ tests/`
- **pytest-asyncio** with `asyncio_mode = "auto"` — async test functions just work.
- **No mocks pretending to be real tests.** Integration tests use `DemoBackend` or `LocalBackend`.
- **ToolRisk is an IntEnum** — comparison is numeric: `READ_ONLY(10) < SHELL(40)`.
- **Target is always explicit** in tool calls, never implicit. Default "localhost".
- **Session store uses asyncio.Lock** — bound to Textual's event loop. Textual workers must use `@work(thread=False)` for async code that touches the session.
- **Streaming**: LLM providers yield `StreamChunk`. The `ToolCallAssembler` accumulates deltas into complete tool calls.
- **`tool_choice: "auto"`** is sent when tools are present — required for some providers to actually use tools.

## Running

```bash
wb tui                    # Windowed TUI (recommended)
wb chat                   # CLI chat
wb tools list             # Show registered tools
wb config show            # Show effective config
wb config validate        # Check for config issues
pytest tests/ -v          # Run all tests
```

## Adding a New Tool

1. Subclass `Tool` from `workbench/tools/base.py`
2. Set `name`, `description`, `risk_level`, `privacy_scope`, `parameters` (JSON Schema)
3. Implement `async execute(self, **kwargs) -> ToolResult`
4. Register in the CLI/TUI startup code (see `workbench/cli/app.py` or `workbench/tui/app.py`)
5. Add tests in `tests/`

## Gotchas

- `workbench.yaml` must have `policy.max_risk: SHELL` to allow shell tool execution. Default is `READ_ONLY`.
- The `api_key_env` config field is the **name of the env var**, not the key itself.
- SSHBackend exists but is a stub — all methods raise `BackendError("not_connected")`.
- TUI logs go to `~/.workbench/tui.log` (append mode).
- Audit logs go to `~/.workbench/audit.jsonl` with rotation.
