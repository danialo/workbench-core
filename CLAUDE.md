# CLAUDE.md — Project Guide for AI Agents

## What This Is

workbench-core is a portable operations assistant runtime. It provides an LLM orchestrator with tool calling, policy enforcement, session persistence, and both CLI and TUI interfaces. No vendor lock-in — works with any OpenAI-compatible API endpoint.

## Setup

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
cp workbench.yaml.example workbench.yaml   # then edit with your LLM provider
pytest tests/ -v                            # 198 tests, all should pass
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

### Recipe System Files

| File | What It Does |
|------|-------------|
| `workbench/recipes/schema.py` | `Recipe` dataclass, `RecipeParameter`, YAML loader, validation |
| `workbench/recipes/registry.py` | `RecipeRegistry` — discover, register/unregister, trigger matching |
| `workbench/recipes/executor.py` | `RecipeExecutor` — runs recipes through the orchestrator |
| `workbench/tools/recipe_tool.py` | `SaveRecipeTool` — LLM-callable tool for saving recipes |

### Web UI Files

| File | What It Does |
|------|-------------|
| `workbench/web/server.py` | FastAPI factory — SSE streaming, session/workspace mgmt, CSRF |
| `workbench/web/middleware.py` | Auth, CSRF token validation, rate limiting |
| `workbench/web/streaming.py` | SSE stream helpers for chat and agent output |
| `workbench/web/routes/investigations.py` | Investigation CRUD, case fetch, integrations config |
| `workbench/web/routes/agents.py` | Agent SSE stream and status endpoints |
| `workbench/web/integrations.json.example` | Pluggable case source config (Glean, Jira, ServiceNow) |
| `workbench/web/static/index.html` | Operations Center SPA — Inbox, Triage, Evidence tabs |
| `workbench/web/static/app.js` | Core app class — routing, SSE chat, session mgmt, tool call cards, settings/overlay panels, sidebar resize |
| `workbench/web/static/index.css` | Global styles, flexbox layout, tool call groups, settings panel, overlay styles |
| `workbench/web/static/triage.js` | `TriageWindow` class — investigations, intake panel, search, embedded chat |
| `workbench/web/static/triage.css` | Three-panel grid layout for triage |
| `workbench/web/static/agent-hud.js` | `AgentHud` class — SSE stream, resize, color-coded status, notifications |
| `workbench/web/static/agent-hud.css` | Inline agent panel styles |
| `workbench/web/static/context-bar.js` | Context pill bar — workspace-scoped pills injected into LLM context |
| `workbench/web/static/recipes.js` | `RecipeWindow` class — browser, chat, editor, save scope dialog |
| `workbench/web/static/recipes.css` | Recipe layout, editor form, scope dialog, delete button styles |
| `workbench/web/routes/recipes.py` | Recipe CRUD, execution SSE, scope-aware save, delete endpoint |

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
wb tui                              # Windowed TUI
wb web --host 0.0.0.0 --port 8080  # Web UI (Operations Center)
wb chat                             # CLI chat
wb tools list                       # Show registered tools
wb config show                      # Show effective config
wb config validate                  # Check for config issues
pytest tests/ -v                    # Run all tests
```

## Adding a New Tool

1. Subclass `Tool` from `workbench/tools/base.py`
2. Set `name`, `description`, `risk_level`, `privacy_scope`, `parameters` (JSON Schema)
3. Implement `async execute(self, **kwargs) -> ToolResult`
4. Register in the CLI/TUI startup code (see `workbench/cli/app.py` or `workbench/tui/app.py`)
5. Add tests in `tests/`

## Web UI Conventions

- **Start with `wb web`**, not `uvicorn module:app` — uses factory pattern.
- **Window system**: Tabs switch `.window` containers via `switchWindow(name)`. Each window (Inbox, Triage, Evidence, Recipes) is a `<div>` toggled by display.
- **Triage layout**: CSS grid — `280px 1fr` default, `280px 1fr 380px` with intake panel open.
- **DOM reparenting**: Embedded chat uses `reparentChat(targetId)` / `returnChat()` to move `#conversationView` between windows without duplicating logic.
- **Tool call groups**: Consecutive tool calls in a message are grouped into a collapsible summary bar.
- **Agent status colors**: Green (`--status-connected`) = running, Yellow (`--accent-primary`) = waiting, Red (`--status-error`) = stopped/error, Gray (`--text-tertiary`) = completed.
- **Integrations config**: User copies `integrations.json.example` to `~/.workbench/integrations.json`. Sources can be `"type": "agent"` (orchestrator-driven) or `"type": "api"` (direct HTTP).
- **Route ordering matters**: Static path routes (`/integrations`, `/fetch-case`) must be registered before parameterized routes (`/{investigation_id}`) in FastAPI.
- **CSRF protection**: All POST/PUT/DELETE require `X-CSRF-Token` header. Token fetched from `GET /api/csrf-token`.
- **Settings panel**: Gear icon (top bar) or sidebar Settings button opens a tabbed overlay (General, LLM, Agents, Integrations, Policy). Ctrl+, shortcut. Escape or backdrop click to close.
- **Sidebar overlays**: Bottom nav buttons (Knowledge, Browser, Feedback) open generic `panel-overlay` modals. All use `data-close-overlay` attribute pattern and `openOverlay(id)` / `closeOverlay(id)` methods.
- **Sidebar resize**: Drag handle between sidebar and main content. Min 160px, max 480px. Uses `initSidebarResize()` in app.js.
- **Recipe window**: Two-panel layout (list + detail). Own independent chat session (not reparented from Inbox). Input box uses same CSS classes as Inbox for visual consistency but separate IDs (`recipeMessageInput`, `recipeBtnSend`, `recipeModelSelect`).
- **Recipe storage**: Global recipes in `~/.workbench/recipes/{name}/recipe.yaml`. Workspace recipes in `{workspace}/.workbench/recipes/{name}/recipe.yaml`. Save scope (global vs workspace) chosen via modal dialog.
- **Recipe chat sessions**: Persisted to SQLite with `{ recipe_builder: true }` metadata. Own SSE streaming, independent from Inbox.
- **Context pill bar**: Workspace-scoped pills (`context_pills` SQLite table). Auto-injected into LLM system prompt as `## Workspace Context`. Pills reload on workspace switch.

## Gotchas

- `workbench.yaml` must have `policy.max_risk: SHELL` to allow shell tool execution. Default is `READ_ONLY`.
- The `api_key_env` config field is the **name of the env var**, not the key itself.
- SSHBackend requires `connect()` before use — methods raise `BackendError("not_connected")` until connected.
- `BackendRouter` dispatches by target name — localhost goes to `LocalBackend`, named hosts to their `SSHBackend`.
- Plugin tools that declare `backend` in `__init__` get the router injected automatically by `load_plugins()`.
- TUI logs go to `~/.workbench/tui.log` (append mode).
- Audit logs go to `~/.workbench/audit.jsonl` with rotation.
