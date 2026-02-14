# TODO

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
