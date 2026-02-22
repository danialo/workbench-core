# TODO

## Inbox Window

### Done
- [x] Inbox view with search and item list
- [x] Conversation view with streaming LLM output
- [x] Tool call cards (name, args, result)
- [x] Streaming indicator and follow-along button
- [x] SSE-based real-time chat streaming
- [x] Session persistence to SQLite
- [x] Sidebar conversation list with load/switch
- [x] Create new conversation from sidebar
- [x] Command allowlist with UI confirmation prompts
- [x] Markdown rendering in chat responses
- [x] Mode selector (planning, execution, diagnostics)
- [x] Model selector dropdown
- [x] Workspace tabs and sidebar workspace list
- [x] Directory browser for workspace folders
- [x] About section / playground prompt

### Remaining
- [ ] Inbox badge count updates in real-time
- [ ] Verify agent completions land in inbox automatically
- [ ] Clicking inbox item opens the completed conversation
- [ ] Delete/archive conversations
- [ ] Playground: flag sessions as ephemeral, auto-cleanup, "Move to Workspace" action
- [ ] **Memory panel** — UI for viewing/editing per-workspace memory entries (SQLite + file-based). Show agent-written notes, CLAUDE.md, README.md. Edit/delete entries inline. Backend is wired (`memory_read`/`memory_write` tools + `build_memory_context()` injection)
- [ ] Workspace settings/config editor

## Triage Window

### Done
- [x] Three-panel layout (list, center detail/chat, hideable intake panel)
- [x] Investigation CRUD (create, read, update, delete)
- [x] Inline intake panel with slide-in animation
- [x] Case query integration — pluggable `integrations.json` config (Glean, Jira, ServiceNow stubs)
- [x] Fetch case data endpoint (agent-driven or API, saves to `~/.workbench/tmp/`)
- [x] Default triage checklist with toggle support
- [x] Severity color coding on investigation cards
- [x] Filter by status (open, investigating, escalated, resolved, all)
- [x] Search/filter investigations by title, account, or system
- [x] Embedded chat via DOM reparenting (no window switching)
- [x] Start investigation chat (auto-creates linked session)
- [x] Escalate / Resolve actions
- [x] `incident_id` → `investigation_id` column migration
- [x] Route ordering fix (`/integrations`, `/fetch-case` before `/{id}`)

### Remaining
- [ ] Add custom checklist items (input field in detail view)
- [ ] Remove checklist items
- [ ] Auto-select newly created investigation after submit
- [x] **Context editor panel** — editable panel showing what gets injected into the LLM as system context for this investigation. User can toggle fields on/off (title, severity, systems, description, case data), edit values before they're sent, add free-form context notes, and reorder priority. Gives full control over what the agent "knows" going in
- [x] Frontload investigation context into chat — when conversation is linked to an investigation, inject the context editor contents as system context so the LLM has full awareness
- [ ] **Context pill menu** — right-click context menu on pills with options: Edit, Toggle, Remove, Move Up/Down (reorder priority), Copy Value. Replace browser prompt() for custom pill creation with inline form
- [ ] Seed first message from case data (pre-populate initial prompt with case summary)
- [ ] Wire real API integrations (Jira, ServiceNow HTTP calls)
- [ ] Investigation hierarchy — subdirectories for notes, evidence, questions, escalations
- [ ] Bulk actions (multi-select, bulk resolve/escalate)
- [ ] Investigation timeline / activity log
- [ ] Export investigation summary

## Editor Window

### Done
- [x] Ace editor (vendored, BSD) with one_dark theme
- [x] File tree sidebar with directory browsing
- [x] Tabbed editing with dirty indicators
- [x] Ctrl+S save with CSRF
- [x] markdown-it live preview (rendered view default for .md)
- [x] Show/Hide Code toggle for split view
- [x] Hidden files toggle (.*) with localStorage persistence
- [x] File API (list, read, write) with path safety
- [x] Resizable sidebar + split panes

### Remaining
- [ ] **Chat integration** — highlight text in rendered markdown, right-click or button to "Ask about this" → sends selection + context to the default LLM in a new or existing conversation. Bridge between editor and inbox chat
- [ ] **Inline Q&A** — highlight in preview, ask question, get answer rendered inline (tooltip or sidebar) without leaving editor
- [ ] Create new file / rename / delete from file tree
- [ ] File search (fuzzy find across workspace)
- [ ] Syntax highlighting for more languages (add Ace modes as needed)
- [ ] Pandoc export (md → docx, md → pdf)
- [ ] Recent files list
- [ ] **Layout persistence** — save per-file or per-workspace editor layout (sidebar width, split ratio, code/preview visibility, open tabs) to a config file. Restore on re-open
- [ ] Multi-cursor editing
- [ ] Find & replace within file

## Evidence Window

### Done
- [x] Tab and window shell (placeholder)
- [x] Audit log writes to `~/.workbench/audit.jsonl` with rotation (backend exists)

### Remaining

#### Evidence / Audit Trail API (core)
- [ ] **Audit query API** — REST endpoints to search/filter audit.jsonl (by session, tool, time range, success/failure)
- [ ] **Audit indexing** — SQLite index over audit entries for fast queries (audit.jsonl is append-only, index is derived)
- [ ] **Evidence linking** — API to tag audit entries / tool results as evidence for an investigation
- [ ] **Evidence CRUD** — create, read, update, delete evidence items with metadata (tags, notes, relevance)

#### Evidence UI
- [ ] Tool call inspection — browse all tool calls from a session
- [ ] Audit trail — chronological log of actions taken, filterable timeline
- [ ] Evidence tagging — mark tool results as relevant to an investigation
- [ ] Link evidence to investigations (cross-reference triage)
- [ ] Artifact viewer — formatted display of files, logs, screenshots
- [ ] Export evidence bundle (zip or PDF)
- [ ] Filter by session, agent, tool type, or time range

## Agent Activity Panel → Agent Management

### Done
- [x] Inline resizable panel (not overlay)
- [x] SSE stream from `/api/agents/stream`
- [x] Color-coded agent status (green=running, yellow=waiting, red=error, gray=completed)
- [x] Resize handle with drag support
- [x] Inbox notifications for agents waiting on user input
- [x] Agent panel user input — click to select agent, textarea + send, Enter to submit
- [x] Copy button on all chat messages (hover-reveal, raw text copy)

### Remaining

#### Agent Config & Persistence (core)
- [ ] **Agent definition schema** — name, description, system prompt, recipes[], tools[], context files[], rules[], workspace scope
- [ ] **Agent persistence** — SQLite storage for agent configs (CRUD). Agents are per-workspace unless created in Global
- [ ] **Agent CRUD API** — REST endpoints for create/read/update/delete agent definitions
- [ ] **Agent lifecycle** — start, stop, pause, resume. Track state transitions
- [ ] **Agent session binding** — each agent run creates a session, history persisted across runs
- [ ] **Agent memory** — per-agent memory namespace (scoped within workspace memory)

#### Recipe System (core)

Recipes are the execution model. Every interaction is recipe-driven:
1. User sends a prompt
2. System builds an **ephemeral recipe** — identifies tools, structures approach, refines prompt
3. Orchestrator executes the recipe
4. User can **"Save as Recipe"** to persist it → becomes discoverable and reusable
5. Next similar request matches the persistent recipe instead of building from scratch

- [x] **Recipe definition schema** — `recipe.yaml`: name, description, trigger (prompt pattern or explicit invocation), prompt template, tools[] (required tools), parameters (user-configurable inputs), output format, version
- [ ] **Ephemeral recipe construction** — orchestrator pre-step that builds a structured recipe from raw user input before execution. Always runs. This IS the prompt refinement layer
- [x] **Recipe executor** — takes a recipe (ephemeral or persistent) and runs it through the orchestrator with the specified tools and prompt template
- [x] **Recipe registry** — discover/list/match recipes in a workspace. Checks `.workbench/recipes/` for persistent recipes that match the user's intent before building ephemeral
- [ ] **Recipe manifest / index** — workspace-level index of available recipes with descriptions, so the LLM can discover and select the right recipe for a task
- [x] **"Save as Recipe" action** — after any execution, offer to persist the ephemeral recipe. Writes `recipe.yaml` + `prompt.md` to `.workbench/recipes/<name>/`, updates index
- [ ] **Recipe builder meta-recipe** — ships with every workspace. A persistent recipe whose prompt teaches the LLM the recipe schema, directory conventions, and how to create + deploy new recipes
- [ ] **Recipe packaging** — recipes as self-contained units (prompt + tools + context) that can be shared, imported, exported

#### Recipe UI & Remote Repository
- [x] **Recipe browser panel** — browse installed recipes per workspace. Card view with name, description, trigger, tool chain. Enable/disable per workspace
- [ ] **Recipe detail view** — read-only view of recipe.yaml + prompt.md. "Edit in chat" button opens a conversation with recipe-builder to modify it
- [x] **"Save as Recipe" button** — appears after every agent response. Persists the ephemeral recipe that was just executed
- [ ] **Remote recipe repository** — hosted registry (GitHub repo or custom API) for publishing and discovering community recipes
- [ ] **Recipe install from remote** — `wb recipe install <name>` CLI + API endpoint. Downloads recipe directory into workspace `.workbench/recipes/`
- [ ] **Recipe publish to remote** — `wb recipe publish <name>` packages and pushes a local recipe to the remote registry
- [ ] **Recipe versioning** — semver in recipe.yaml, remote registry tracks versions, workspace can pin or auto-update
- [ ] **Recipe search** — search remote repository by name, description, tags. API + UI integration
- [ ] **Recipe ratings / usage stats** — track installs, stars, last updated. Surface popular recipes in browser

#### Sub-agents (core)
- [ ] **Sub-agent spawning** — an agent can spin off child agents for parallel or delegated work
- [ ] **Sub-agent communication** — parent/child message passing, result collection, error propagation
- [ ] **Sub-agent lifecycle** — auto-cleanup when parent completes, orphan detection
- [ ] **Shared context** — parent can inject context into sub-agents, sub-agents can write back findings

#### Agent Management UI
- [ ] Agent list view — running agents, offline/disabled agents, agent configs
- [ ] "New Agent" creation flow — wizard or form to define agent from scratch or from recipe template
- [ ] Agent config editor (prompt, recipes, rules, context files)
- [ ] Per-agent task queue and history
- [ ] Stream agent reasoning/thinking
- [ ] Agent memory/context viewer
- [ ] Start/stop/pause controls per agent

## Context Pill Bar

### Done
- [x] Replace agent HUD bar with context pill bar (workspace-scoped)
- [x] Context pill CRUD API (SQLite `context_pills` table, per-workspace isolation)
- [x] Custom pill type (label + value, field-level toggle)
- [x] Timeline pill type (start/end dates, configurable date format)
- [x] "+" dropdown menu with pill type selection
- [x] Inline creation form/popover
- [x] Click to toggle pill on/off, right-click for field-level toggles
- [x] Auto-inject enabled pills into LLM system prompt as `## Workspace Context`
- [x] Pills reload on workspace switch
- [x] Agent panel button moved to top bar

### Remaining

#### Custom Pill Types (extensible schema)
- [ ] **Pill type registry** — define new pill types with a schema: name, icon, fields[] (each with name, type, label, required). Stored as JSON config per workspace or global. The "+" menu auto-populates from the registry
- [ ] **Built-in types**: Case (case_id, source, summary, severity), Jira (ticket_id, summary, priority, assignee, status), Host (hostname, IP, OS, role), Log Window (source, start_time, end_time, filter), Runbook (title, steps[], link)
- [ ] **User-defined types** — UI form to create a new pill type: name it, define fields (text, date, select, multiline), set an icon. Saved to workspace config. Appears in "+" dropdown alongside built-ins
- [ ] **Type templates** — pre-built type packs (e.g. "Incident Response" pack adds Case, Timeline, Host, Log Window types). Importable/exportable
- [ ] **Field types** — support beyond text: date picker, dropdown/select, number, URL (auto-linkable), multiline/textarea, boolean toggle
- [ ] **Integration-backed pills** — pill types that auto-populate fields from an integration source (e.g. "Jira" type fetches ticket data via integrations.json config, populates fields, user can then toggle individual fields on/off)

#### Context Bar UI
- [ ] **Context pill menu** — right-click context menu with: Edit, Toggle, Remove, Move Up/Down (reorder), Copy Value, Duplicate
- [ ] **Drag-to-reorder** pills (update sort_order via PUT)
- [ ] **Pill editing popover** — double-click or right-click Edit to open inline editor for all fields
- [ ] **Pill groups** — visual grouping/separators (e.g. "Environment" group, "Incident" group)
- [ ] **Pill color coding** — user-assignable colors or auto-color by type
- [ ] **Pill search/filter** — when many pills exist, search or filter by type
- [ ] **Pill import/export** — save a set of pills as a template, apply to other workspaces
- [ ] **Pill limit warning** — warn when total context size approaches token limits

#### Context Management
- [ ] **Context budget** — track estimated token count of all enabled pills, show usage bar
- [ ] **Context priority** — pills with higher sort_order get truncated last if context budget is exceeded
- [ ] **Context snapshots** — save current pill state as a named snapshot, restore later
- [ ] **Workspace defaults** — when creating a new workspace, optionally seed it with a set of default pills from a template

## Settings Panel

### Done
- [x] Tabbed overlay (General, LLM, Agents, Integrations, Policy) with Ctrl+, shortcut
- [x] **LLM / Providers tab** — full CRUD: add, edit, delete providers with persistence to `workbench.yaml`
- [x] Provider hot-reload into router (no restart needed)
- [x] Switch active provider from dropdown
- [x] Provider types: OpenAI-compatible, Ollama (no auth, localhost defaults), Claude Code CLI (stub)
- [x] Form validation with error messages
- [x] Env var loading from `~/.bashrc`, `~/.zshrc`, `.env`, `~/.env`
- [x] Slash-safe routes for provider names with `/`
- [x] Ollama model listing endpoint

### Remaining
- [ ] **General tab** — theme selection, layout preferences, workspace defaults
- [ ] **Agents tab** — agent registry, default configs, skill assignments, risk policies
- [ ] **Integrations tab** — case sources (Jira, ServiceNow, Glean), API keys, webhook config
- [ ] **Policy & Security tab** — max risk level, blocked/allowed patterns, confirmation rules, audit settings

## Global / Cross-Window

### Layout & Navigation
- [ ] Customizable layout system (drag/resize panels)
- [ ] Layout presets (wide, compact, focused)
- [ ] Save/restore layout preferences per workspace
- [ ] Menu bar: Edit (undo/redo/copy/paste), View (fullscreen)

### Document Model
- [ ] Define core document model — what is a "document" in the system (investigation notes, runbooks, agent output, knowledge files, evidence artifacts)
- [ ] Document schema: type, title, content, metadata, parent (investigation/session/workspace), timestamps
- [ ] Storage layer — SQLite, filesystem, or hybrid
- [ ] Document CRUD API endpoints
- [ ] Link documents to investigations, sessions, and agents
- [ ] Versioning / revision history
- [ ] Document viewer/editor in the UI

### Workspace Scaffolding & Defaults
- [ ] **Workspace scaffold on create** — when a new workspace is created, generate the default directory structure:
  ```
  .workbench/
    config.yaml          # workspace-level config overrides
    memory.md            # workspace memory (agent-writable)
    recipes/             # persistent recipe definitions
      recipe-builder/    # meta-recipe: teaches LLM to write recipes
        recipe.yaml
        prompt.md
    agents/              # agent definitions
    context/             # shared context files for this workspace
  ```
- [ ] **Default recipe: recipe-builder** — ships with every workspace. A meta-recipe whose prompt teaches the LLM the recipe schema, directory conventions, and how to create + deploy new recipes
- [ ] **Default config template** — `config.yaml` with commented-out options (tools_enabled, tools_disabled, policy overrides, LLM overrides)
- [ ] **Scaffold CLI command** — `wb workspace init [path]` to create structure in an existing directory
- [ ] **Scaffold API** — POST `/api/workspaces` already creates the workspace record; extend to also create the directory structure if `path` is set

### Infrastructure
- [ ] **MCP (Model Context Protocol) server** — expose workbench tools, sessions, and workspaces via MCP so external clients (Claude Desktop, VS Code extensions, other agents) can connect and use them natively
- [ ] Reverse tunnel for remote access
- [ ] Agent collaboration/communication system
- [ ] Shared context between agents
- [ ] Agent performance metrics
- [ ] Export agent configs as reusable templates

## TUI Improvements

### Theme & Visual Polish
- [ ] Fine-tune orange color values (brightness, contrast)
- [ ] Verify color accessibility (contrast ratios)
- [ ] Theme customization and presets
- [ ] Improve window shadows/depth, hover states, spacing

### Copy/Paste & Text
- [ ] Fix text highlighting in RichLog
- [ ] Proper mouse text selection
- [ ] Improve copy functionality beyond Ctrl+Y
- [ ] Fix markdown/code block rendering
- [ ] Syntax highlighting for code blocks
- [ ] Handle long lines/word wrapping
