# Workbench Core

Portable support and diagnostics workbench runtime. No employer IP, no vendor lock-in -- just the engine.

- Orchestrator loop (LLM + tools + events)
- Tool contract, registry, validation, policy enforcement
- Provider-agnostic LLM router with streaming and tool-call assembly
- Session event log, artifact store, replay, runbook export
- Interfaces: CLI first, then TUI, then VS Code, then optional Web

Adapters (SSH, K8s, vendor APIs, ticketing systems) plug in later via entry points in a separate repo.

## Quickstart

```bash
# Clone
git clone git@github.com:danialo/workbench-core.git
cd workbench-core

# Create venv and install
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"

# Run tests
pytest tests/ -v

# Try the CLI
wb version
wb tools list
wb config show
```

### Configuration

Copy the example config and edit for your environment:

```bash
cp workbench.yaml.example workbench.yaml
```

Config is also loaded from `~/.config/workbench/config.yaml` and `~/.workbench/config.yaml`. See `workbench.yaml.example` for all options with comments.

Example for a local model:

```yaml
llm:
  name: local
  model: qwen3-coder
  api_base: http://localhost:3333/v1
  api_key_env: NOT_NEEDED
  timeout_seconds: 30

policy:
  max_risk: SHELL
  confirm_destructive: true
  confirm_shell: true
```

Example for a remote provider (OpenRouter, OpenAI, Anthropic, etc.):

```yaml
llm:
  name: openrouter
  model: qwen/qwen3-coder-next
  api_base: https://openrouter.ai/api/v1
  api_key_env: OPENROUTER_API_KEY
  timeout_seconds: 120

policy:
  max_risk: SHELL
```

Set your API key in the environment, then start a session:

```bash
export OPENROUTER_API_KEY=sk-or-...
wb chat          # CLI chat
wb tui           # Windowed TUI (recommended)
```

Inside chat you get inline commands: `/tools`, `/history`, `/switch <provider>`, `/quit`.

## Architecture

```
┌──────────────────────────────────────────────────────────────────┐
│ Interfaces                                                       │
│  CLI  |  TUI  |  VS Code  |  Web                                │
│                 │                                                │
│            Orchestrator                                          │
│     tool dispatch . policy . validation . events . context       │
│                 │                                                │
│   ┌─────────────┼─────────────┐                                  │
│   │             │             │                                  │
│ Tool Registry  LLM Router   Session Store                        │
│ schemas        streaming    SQLite + replay + runbook             │
│ plugins        assembler    artifacts refs                        │
│               token count                                        │
│                 │                                                │
│        Execution Backend Interface (abstract)                    │
│ resolve() . run_diagnostic() . run_shell(optional)               │
│                 │                                                │
│    Adapters (separate repo, loaded via entry points)             │
└──────────────────────────────────────────────────────────────────┘
```

## Project Structure

```
workbench-core/
├── workbench/
│   ├── types.py                  # ToolResult, ArtifactRef, ErrorCode, PolicyDecision
│   ├── config.py                 # Typed config + precedence loader + profiles
│   ├── tools/
│   │   ├── base.py               # Tool ABC, ToolRisk IntEnum, PrivacyScope
│   │   ├── registry.py           # ToolRegistry with plugin loading
│   │   ├── validation.py         # JSON Schema argument validation
│   │   └── policy.py             # PolicyEngine + audit writer + redaction
│   ├── llm/
│   │   ├── types.py              # Message, ToolCall, StreamChunk, AssembledAssistant
│   │   ├── router.py             # Multi-provider router with assembly
│   │   ├── tool_call_assembler.py
│   │   ├── token_counter.py
│   │   └── providers/
│   │       ├── base.py           # Provider ABC
│   │       ├── openai_compat.py  # Any OpenAI-compatible API (local or remote)
│   │       ├── ollama.py         # Ollama native API
│   │       └── generic_sdk.py    # Anthropic/OpenAI SDK wrapper
│   ├── session/
│   │   ├── events.py             # Append-only event model with factories
│   │   ├── store.py              # SQLite + schema versioning + migrations
│   │   ├── artifacts.py          # Content-addressed artifact store (SHA-256)
│   │   ├── context.py            # Token-budgeted context packing
│   │   └── session.py            # High-level session manager
│   ├── orchestrator/
│   │   └── core.py               # Main loop with full tool-call lifecycle
│   ├── backends/
│   │   ├── base.py               # ExecutionBackend ABC
│   │   ├── catalog.py            # Diagnostics catalog
│   │   ├── demo.py               # Demo backend (fake targets + results)
│   │   ├── local.py              # LocalBackend — real shell via asyncio subprocess
│   │   ├── ssh.py                # SSHBackend — stub for future remote execution
│   │   └── bridge.py             # Bridge tools (resolve, list, run_diagnostic, run_shell)
│   ├── prompts/
│   │   ├── system.py             # System prompt builder
│   │   ├── tool_discipline.py    # Tool usage instructions
│   │   └── conventions.py        # Output formatting conventions
│   ├── tui/
│   │   ├── app.py                # Textual app — windowed desktop with hotkeys
│   │   ├── window.py             # Draggable window widget (min/max/restore)
│   │   ├── window_manager.py     # Layout: cascade, tile, snap-to-grid
│   │   ├── menu_bar.py           # Top menu bar
│   │   ├── context_menu.py       # Right-click context menus
│   │   └── windows/
│   │       ├── chat_window.py    # LLM chat with streaming + tool calling
│   │       ├── tools_window.py   # Tool registry browser
│   │       ├── events_window.py  # Session event log viewer
│   │       ├── config_window.py  # Live config viewer
│   │       └── artifacts_window.py  # Artifact store browser
│   └── cli/
│       ├── app.py                # Typer CLI (chat, sessions, tools, config, tui)
│       ├── chat.py               # Interactive chat handler
│       └── output.py             # Rich output formatting + session export
├── tests/                        # 171 tests
│   ├── mock_tools.py             # 5 mock tools at different risk/privacy levels
│   ├── mock_providers.py         # Mock LLM providers for testing
│   ├── test_validation.py        # Schema validation + unknown key rejection
│   ├── test_policy.py            # Risk gating, confirmation, redaction, audit
│   ├── test_registry.py          # Registry CRUD, filtering, plugin loading
│   ├── test_audit_rotation.py    # Audit log rotation + atomicity
│   ├── test_tool_call_assembler.py  # Streaming assembly + malformed JSON
│   ├── test_session.py           # Events, store, context packing, session mgr
│   ├── test_artifacts.py         # Content-addressing, permissions, traversal
│   ├── test_orchestrator.py      # Full lifecycle, error paths, max turns
│   ├── test_backends.py          # LocalBackend + SSHBackend tests
│   └── test_e2e.py               # End-to-end with demo backend
├── workbench.yaml.example        # Config template — copy to workbench.yaml
└── pyproject.toml
```

## Key Design Decisions

- **ToolRisk is an IntEnum** -- deterministic comparisons: `READ_ONLY(10) < WRITE(20) < DESTRUCTIVE(30) < SHELL(40)`
- **Target is always explicit** -- every tool call includes the target, never implicit
- **Session is append-only events** -- derived views generate messages, runbooks, and replay
- **Unknown args rejected by default** -- schemas normalized to `additionalProperties: false`
- **Plugins are opt-in** -- disabled by default, allowlist support for safety
- **Streaming assembly has explicit failure modes** -- malformed tool calls become protocol errors, no silent retries
- **Artifacts are content-addressed** -- SHA-256, deduplication, 0o700/0o600 permissions
- **Config is typed with defined precedence** -- `defaults < config file < env vars < CLI flags < per-session overrides`

## CLI Commands

| Command | Description |
|---------|-------------|
| `wb tui` | **Windowed TUI** — recommended interface |
| `wb chat` | Interactive CLI chat with tool calling |
| `wb chat --provider NAME` | Chat using a specific LLM provider |
| `wb chat --profile NAME` | Chat using a config profile |
| `wb chat --session ID` | Resume an existing session |
| `wb sessions list` | List all sessions |
| `wb sessions show ID` | Show session events |
| `wb sessions delete ID` | Delete a session |
| `wb sessions export ID --format runbook` | Export as runbook |
| `wb tools list` | List registered tools |
| `wb tools list --max-risk WRITE` | Filter tools by risk level |
| `wb tools info NAME` | Show tool details and schema |
| `wb config show` | Show effective configuration |
| `wb config validate` | Validate config and show issues |
| `wb version` | Show version |

## TUI

`wb tui` launches a desktop-style windowed interface built on Textual. Each panel (chat, tools, events, config, artifacts) is an independent window with minimize/maximize/restore.

### TUI Hotkeys

| Key | Action |
|-----|--------|
| Ctrl+N | New chat window |
| Ctrl+W | Close current window |
| Ctrl+L | Clear chat |
| Ctrl+T | Open tools window |
| Ctrl+Y | Copy last LLM response to `~/.workbench/last_response.txt` |
| Ctrl+S | Save full chat history to file |
| F2 | Copy selected text |
| F5 | Cascade windows |
| F6 | Tile grid |
| F7 | Cycle to next window |
| F10 | Context menu |

### TUI Chat Commands

| Command | Description |
|---------|-------------|
| `/tools` | List registered tools |
| `/history` | Show session history |
| `/copy` | Save last response to file |
| `/save [path]` | Save full chat to file |
| `/quit` | Exit |

### Registered Tools

The TUI and CLI register these tools at startup:

| Tool | Risk | Description |
|------|------|-------------|
| `resolve_target` | READ_ONLY | Resolve and describe a target system |
| `list_diagnostics` | READ_ONLY | List available diagnostic actions |
| `run_diagnostic` | DESTRUCTIVE | Run a structured diagnostic action |
| `summarize_result` | READ_ONLY | Summarize a previous tool result |
| `run_shell` | SHELL | Execute a shell command on a target |

The LLM calls these tools autonomously. Policy enforcement (`max_risk`, `confirm_shell`, `blocked_patterns`) gates every call.

## Testing

```bash
# Run all 171 tests
pytest tests/ -v

# Run specific test modules
pytest tests/test_orchestrator.py -v    # Orchestrator lifecycle
pytest tests/test_e2e.py -v             # End-to-end with demo backend
pytest tests/test_policy.py -v          # Policy enforcement + audit
pytest tests/test_backends.py -v        # Local + SSH backends

# With coverage
pytest tests/ --cov=workbench --cov-report=term-missing
```

## Dependencies

**Core (default install):**

| Dependency | Purpose |
|------------|---------|
| Python 3.12+ | Runtime |
| jsonschema | Tool argument validation |
| aiosqlite | Async session persistence |
| httpx | HTTP client for LLM providers |
| rich | CLI output formatting |
| typer | CLI framework |
| textual | TUI framework (Phase 7) |
| pyyaml | Config file parsing |

**Optional extras:**

```bash
pip install -e ".[openai]"      # tiktoken for accurate token counting
pip install -e ".[remote_sdk]"  # anthropic + openai SDKs
pip install -e ".[providers]"   # all provider dependencies
pip install -e ".[dev]"         # pytest, ruff, coverage
```

## What's Next

- **VS Code Extension** -- `wb serve` + chat panel
- **SSH Backend** -- Wire `asyncssh` into the existing `SSHBackend` stub
- **Adapter Pack** -- Separate repo with real backends (K8s, vendor APIs, ticketing)

* * * * *
Disclaimer: This project was built with Claude Code.
