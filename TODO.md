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
- [ ] Memory panel (per-workspace memory files, agent context)
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
- [ ] Frontload investigation context into chat — when conversation is linked to an investigation, inject title, severity, systems, description, and any fetched case data as system context so the LLM has full awareness
- [ ] Seed first message from case data (pre-populate initial prompt with case summary)
- [ ] Wire real API integrations (Jira, ServiceNow HTTP calls)
- [ ] Investigation hierarchy — subdirectories for notes, evidence, questions, escalations
- [ ] Bulk actions (multi-select, bulk resolve/escalate)
- [ ] Investigation timeline / activity log
- [ ] Export investigation summary

## Evidence Window

### Done
- [x] Tab and window shell (placeholder)

### Remaining
- [ ] Tool call inspection — browse all tool calls from a session
- [ ] Audit trail — chronological log of actions taken
- [ ] Evidence tagging — mark tool results as relevant to an investigation
- [ ] Link evidence to investigations (cross-reference triage)
- [ ] Artifact viewer — formatted display of files, logs, screenshots
- [ ] Export evidence bundle (zip or PDF)
- [ ] Filter by session, agent, tool type, or time range

## Agent Activity Panel

### Done
- [x] Inline resizable panel (not overlay)
- [x] SSE stream from `/api/agents/stream`
- [x] Color-coded agent status (green=running, yellow=waiting, red=error, gray=completed)
- [x] Resize handle with drag support
- [x] Inbox notifications for agents waiting on user input

### Remaining
- [ ] Agent registry/management system
- [ ] "New Agent" creation flow
- [ ] Agent config editor (prompt, skills, rules, context files)
- [ ] Per-agent task queue and history
- [ ] Stream agent reasoning/thinking
- [ ] Agent memory/context viewer
- [ ] Agent session persistence

## Global / Cross-Window

### Layout & Navigation
- [ ] Customizable layout system (drag/resize panels)
- [ ] Layout presets (wide, compact, focused)
- [ ] Save/restore layout preferences per workspace
- [ ] Menu bar: Edit (undo/redo/copy/paste), View (fullscreen)

### Infrastructure
- [ ] Reverse tunnel / Fuse for remote access
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
