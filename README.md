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
python -m workbench.cli.app version
python -m workbench.cli.app tools list
python -m workbench.cli.app config show
```

### Start a chat session

The chat command needs an LLM provider. Create `workbench.yaml` in the repo root:

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

Then:

```bash
python -m workbench.cli.app chat
```

Inside chat you get inline commands: `/tools`, `/history`, `/switch <provider>`, `/quit`.

### Using a remote provider

```yaml
llm:
  name: anthropic
  model: claude-sonnet-4-20250514
  api_key_env: ANTHROPIC_API_KEY
  timeout_seconds: 120
```

Set `ANTHROPIC_API_KEY` in your environment and install the optional SDK:

```bash
pip install -e ".[remote_sdk]"
```

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
│   │   └── bridge.py             # Bridge tools (resolve, list, run, summarize)
│   ├── prompts/
│   │   ├── system.py             # System prompt builder
│   │   ├── tool_discipline.py    # Tool usage instructions
│   │   └── conventions.py        # Output formatting conventions
│   └── cli/
│       ├── app.py                # Typer CLI (chat, sessions, tools, config)
│       ├── chat.py               # Interactive chat handler
│       └── output.py             # Rich output formatting + session export
├── tests/                        # 140 tests
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
│   └── test_e2e.py               # End-to-end with demo backend
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
| `wb chat` | Interactive chat with tool calling |
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

## Testing

```bash
# Run all 140 tests
pytest tests/ -v

# Run specific test modules
pytest tests/test_orchestrator.py -v    # Orchestrator lifecycle
pytest tests/test_e2e.py -v             # End-to-end with demo backend
pytest tests/test_policy.py -v          # Policy enforcement + audit

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

- **TUI** (Phase 7) -- Textual split-pane app
- **VS Code Extension** (Phase 8) -- `wb serve` + chat panel
- **Adapter Pack** -- Separate repo with real backends (SSH, K8s, vendor APIs)

* * * * *
Disclaimer: This project was vibe coded with Claude Code.
