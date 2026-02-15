# TODO

## Web UI — MVP (Priority)

These are the minimum viable features to get the Agent Manager web UI functional.

### Wire LLM (Critical Path)
- [ ] Connect orchestrator to web UI chat — send messages, receive streaming responses
- [ ] Display streaming LLM output in conversation view
- [ ] Handle tool calls inline (show tool name, args, result)
- [ ] Error handling for LLM failures / timeouts

### Conversation History
- [ ] Persist conversations to session store (SQLite)
- [ ] Load/display past conversations in sidebar
- [ ] Add top-bar menu option to switch between conversations
- [ ] Create new conversation from menu or sidebar
- [ ] Delete/archive conversations

### Follow Along (Live Streaming)
- [ ] Add "Follow Along" toggle button to conversation view
- [ ] Stream real-time updates (SSE or WebSocket) for active agent work
- [ ] Auto-scroll when following, manual scroll when not
- [ ] Visual indicator when follow mode is active

### Command Allowlist
- [ ] Create allowlist config for always-allowed commands (no confirmation needed)
- [ ] UI to view/edit allowlist in settings
- [ ] Policy engine integration — skip confirmation for allowlisted commands

### Memory
- [ ] Local memory view (per-workspace memory files)
- [ ] Remote memory view (agent context from remote backends)
- [ ] Memory panel in sidebar or dedicated view

### Inbox
- [ ] Test "Your Inbox" — verify agent completions land in inbox when agent finishes work
- [ ] When agent finishes a conversation/task, result should appear in inbox automatically
- [ ] Inbox badge count updates in real-time
- [ ] Clicking inbox item opens the completed conversation

## Web UI — Features

### Playground
Ephemeral scratch space for testing ideas without persistence. No directory binding, no config — just quick experiments.
- [ ] Flag playground sessions as ephemeral (separate from project workspace sessions)
- [ ] No directory/path — playground conversations are untethered
- [ ] "Move to Workspace" action — promote a playground conversation to a real project workspace (carries history)
- [ ] Auto-cleanup — playground sessions expire or clear on some policy (manual clear, TTL, or server restart)
- [ ] Each playground is its own isolated conversation (not a shared space)

### Workspace Management
- [ ] Wire "+" button next to Workspaces in sidebar (quick-create)
- [ ] Workspace settings/config editor

### Layout & Windowed Modes
- [ ] Customizable layout system (drag/resize panels)
- [ ] Windowed modes that auto-change orientation based on viewport
- [ ] Layout presets (wide, compact, focused)
- [ ] Save/restore layout preferences per workspace

### Menu Bar
- [ ] Edit menu options: Undo, Redo, Cut, Copy, Paste, Select All
- [ ] View menu: toggle Full Screen
- [ ] Conversation switcher in top bar (after history is implemented)

### Reverse Tunnel / Fuse
- [ ] Investigate: reverse tunnel to Fuse for remote access
- [ ] Determine if needed or if SSH tunnel is sufficient

## TUI Improvements

### Theme & Visual Polish
- [ ] Fine-tune orange color values (brightness, contrast)
- [ ] Verify color accessibility (contrast ratios)
- [ ] Add theme customization (allow users to change colors)
- [ ] Create theme presets (orange, blue, green, etc.)
- [ ] Improve window shadows/depth perception
- [ ] Better visual hierarchy (titles, borders, highlights)
- [ ] Consistent spacing and padding across components
- [ ] Polish hover states and transitions

### Copy/Paste & Text Selection
- [ ] Fix text highlighting in RichLog (chat window responses)
- [ ] Enable proper text selection with mouse
- [ ] Improve copy functionality (currently only Ctrl+Y for last response)
- [ ] Add visual feedback when text is copied
- [ ] Support standard terminal selection (shift+arrows, etc.)

### Text Formatting & Display
- [ ] Fix formatting text in chat responses (code blocks, lists, etc.)
- [ ] Improve markdown rendering in RichLog
- [ ] Add syntax highlighting for code blocks
- [ ] Better visual separation between messages
- [ ] Handle long lines/word wrapping properly

## Multi-Agent System

### Agent Manager Window
- [ ] Create agent registry/management system
- [ ] Build Agents window showing available agents
- [ ] Display agent status (IDLE, RUNNING, COMPLETED, ERROR)
- [ ] Show current task for running agents
- [ ] Add "New Agent" creation flow

### Agent Configuration
- [ ] Define agent config schema (prompt, skills, rules, files, memory)
- [ ] Build agent config editor UI
- [ ] Implement per-agent system prompts
- [ ] Add skills/tools assignment per agent
- [ ] Support per-agent rules files (like CLAUDE.md)
- [ ] Define context files/directories agent can access

### Agent Task Management
- [ ] Create task queue system per agent
- [ ] Show task list in agent detail view (completed + in-progress)
- [ ] Track task history for reproducibility
- [ ] Enable task replay/audit functionality

### Agent Output & Monitoring
- [ ] Stream agent output to agent window
- [ ] Show tool calls as they execute
- [ ] Display agent reasoning/thinking
- [ ] Add agent memory/context viewer
- [ ] Implement agent session persistence

## Future Enhancements
- [ ] Agent collaboration/communication system
- [ ] Shared context between agents
- [ ] Agent performance metrics
- [ ] Export agent configs as reusable templates
