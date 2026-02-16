# Multi-Agent System Architecture

## Overview

The agent system enables running multiple specialized AI agents simultaneously, each with their own configuration, tools, tasks, and output streams. This provides transparency, reproducibility, and parallel execution of complex workflows.

## Core Concepts

### Agent
An agent is an independent AI worker with:
- **Identity**: Unique ID, name, description
- **Configuration**: System prompt, skills, rules, context
- **State**: IDLE, RUNNING, PAUSED, COMPLETED, ERROR
- **Execution**: Own orchestrator instance, session, task queue
- **Output**: Streaming logs, tool calls, results

### Agent Types
1. **Built-in Agents**: Pre-configured specialists (data-engineer, frontend-developer, etc.)
2. **Custom Agents**: User-created with custom prompts/configs
3. **Template Agents**: Reusable configs that can be instantiated

## Data Schema

### Agent Configuration
```python
@dataclass
class AgentConfig:
    """Complete agent configuration."""

    # Identity
    id: str                           # Unique identifier (e.g., "agent-001")
    name: str                         # Display name (e.g., "Frontend Developer")
    description: str                  # What this agent does
    agent_type: str                   # "builtin" | "custom" | "template"

    # Behavior
    system_prompt: str                # Full system prompt defining role
    model: str | None = None          # Override LLM model (or use default)
    temperature: float = 0.7          # LLM temperature

    # Capabilities
    skills: list[str]                 # Tool names agent can use
    max_risk: str = "READ_ONLY"       # Maximum tool risk level allowed

    # Context
    rules: str | None = None          # Agent-specific rules (like CLAUDE.md)
    context_files: list[str] = []     # Files/dirs agent should read on init
    memory_enabled: bool = True       # Enable cross-session memory

    # Limits
    max_turns: int = 50               # Max orchestrator turns per task
    timeout_seconds: int = 300        # Task timeout

    # Metadata
    created_at: str                   # ISO timestamp
    updated_at: str                   # ISO timestamp
    tags: list[str] = []              # Searchable tags
```

### Agent Instance
```python
@dataclass
class AgentInstance:
    """Runtime agent instance."""

    config: AgentConfig
    state: AgentState                 # Current state
    session_id: str                   # Active session ID
    orchestrator: Orchestrator        # LLM orchestrator
    task_queue: TaskQueue             # Pending/active tasks
    output_buffer: list[str]          # Recent output lines

    # Statistics
    tasks_completed: int = 0
    tasks_failed: int = 0
    total_tokens: int = 0
    runtime_seconds: float = 0.0

    # Control
    started_at: str | None = None
    stopped_at: str | None = None
```

### Agent State
```python
class AgentState(Enum):
    """Possible agent states."""
    IDLE = "idle"                     # Created but not running
    RUNNING = "running"               # Actively executing task
    PAUSED = "paused"                 # Paused (can resume)
    WAITING = "waiting"               # Waiting for user input/confirmation
    COMPLETED = "completed"           # All tasks done
    ERROR = "error"                   # Error state (needs intervention)
```

### Agent Task
```python
@dataclass
class AgentTask:
    """A task assigned to an agent."""

    id: str                           # Task ID
    agent_id: str                     # Agent this belongs to
    description: str                  # What to do
    status: TaskStatus                # pending, in_progress, completed, failed

    # Execution
    created_at: str
    started_at: str | None = None
    completed_at: str | None = None

    # Results
    output: str | None = None         # Task output/result
    artifacts: list[str] = []         # Created artifacts
    error: str | None = None          # Error message if failed

    # Dependencies
    depends_on: list[str] = []        # Task IDs this depends on
    blocks: list[str] = []            # Task IDs blocked by this
```

## Architecture Components

### 1. Agent Manager (`workbench/agents/manager.py`)

**Responsibilities:**
- Load/save agent configurations
- Create/delete agent instances
- Start/stop agents
- Track all running agents
- Route tasks to agents

```python
class AgentManager:
    """Central registry and lifecycle manager for agents."""

    def __init__(self, config_dir: Path):
        self.config_dir = config_dir
        self.agents: dict[str, AgentInstance] = {}
        self.builtin_configs: dict[str, AgentConfig] = {}

    async def load_builtin_agents(self) -> None:
        """Load pre-configured agents from builtin/."""

    async def load_user_agents(self) -> None:
        """Load user-created agents from config_dir."""

    async def create_agent(self, config: AgentConfig) -> AgentInstance:
        """Create new agent instance."""

    async def start_agent(self, agent_id: str, task: str) -> None:
        """Start agent with initial task."""

    async def stop_agent(self, agent_id: str) -> None:
        """Gracefully stop agent."""

    async def get_agent(self, agent_id: str) -> AgentInstance | None:
        """Lookup agent by ID."""

    def list_agents(self, state: AgentState | None = None) -> list[AgentInstance]:
        """List all agents, optionally filtered by state."""
```

### 2. Agent Runtime (`workbench/agents/runtime.py`)

**Responsibilities:**
- Execute agent's orchestrator loop
- Manage agent's task queue
- Stream output to agent's window
- Handle errors and state transitions

```python
class AgentRuntime:
    """Executes an agent's tasks."""

    def __init__(
        self,
        agent: AgentInstance,
        orchestrator: Orchestrator,
        session: Session,
    ):
        self.agent = agent
        self.orchestrator = orchestrator
        self.session = session
        self._running = False

    async def run_task(self, task: AgentTask) -> None:
        """Execute a single task."""

    async def run_loop(self) -> None:
        """Main agent loop - process task queue until empty."""

    async def pause(self) -> None:
        """Pause agent execution."""

    async def resume(self) -> None:
        """Resume paused agent."""

    async def stream_output(self) -> AsyncIterator[str]:
        """Stream agent output as it runs."""
```

### 3. Agent Store (`workbench/agents/store.py`)

**Responsibilities:**
- Persist agent configs to disk
- Load agent configs from disk
- Save agent sessions/history
- Export/import agent templates

```python
class AgentStore:
    """Persistence layer for agent configs and history."""

    def __init__(self, base_dir: Path):
        self.base_dir = base_dir
        self.configs_dir = base_dir / "configs"
        self.sessions_dir = base_dir / "sessions"

    async def save_config(self, config: AgentConfig) -> None:
        """Save agent config to YAML."""

    async def load_config(self, agent_id: str) -> AgentConfig:
        """Load agent config from YAML."""

    async def list_configs(self) -> list[AgentConfig]:
        """List all saved agent configs."""

    async def delete_config(self, agent_id: str) -> None:
        """Delete agent config."""

    async def export_template(self, agent_id: str, path: Path) -> None:
        """Export agent as reusable template."""
```

### 4. Built-in Agents (`workbench/agents/builtin/`)

Pre-configured specialist agents:

**`builtin/data_engineer.yaml`**
```yaml
name: Data Engineer
description: ETL/ELT pipelines, data warehouses, analytics infrastructure
system_prompt: |
  You are a data engineering specialist...
skills:
  - execute_sql
  - run_shell
  - read_file
  - write_file
max_risk: SHELL
rules: |
  - Always validate SQL queries before execution
  - Never drop production tables without confirmation
  - Document data transformations
```

**`builtin/frontend_developer.yaml`**
```yaml
name: Frontend Developer
description: React, TypeScript, UI components, state management
system_prompt: |
  You are a frontend development specialist...
skills:
  - read_file
  - write_file
  - run_shell
  - execute_tests
max_risk: WRITE
context_files:
  - src/components/
  - package.json
  - tsconfig.json
```

*Similar configs for: test-engineer, ai-engineer, ml-engineer, etc.*

## Web UI — Agent Activity Panel (Implemented)

The web Operations Center includes an inline resizable agent activity panel (`agent-hud.js`) that provides real-time agent monitoring:

- **SSE Stream**: `GET /api/agents/stream` delivers agent state updates
- **Color-coded status**: Green (running), Yellow (waiting for user), Red (error), Gray (completed)
- **Inline layout**: Panel sits inside the main flexbox layout, not as an overlay
- **Resize handle**: Drag left edge to resize, width persists during session
- **Inbox notifications**: Agents waiting for user input trigger badges in the sidebar

### Key Files
| File | What It Does |
|------|-------------|
| `workbench/web/static/agent-hud.js` | `AgentHud` class — SSE stream, resize, status rendering |
| `workbench/web/static/agent-hud.css` | Panel styles, color coding, resize handle |
| `workbench/web/routes/agents.py` | Agent SSE stream and status API endpoints |

This is the current monitoring implementation. The full agent management system (creation, config, task queues) described below is planned but not yet built.

## TUI Integration (Planned)

### Agent Windows

**1. Agents List Window (`workbench/tui/windows/agents_window.py`)**

Shows all agents with:
- Agent name and type
- Current state (IDLE, RUNNING, etc.)
- Current task (if running)
- Quick actions (start, stop, configure)

```
┌─ Agents ──────────────────────────────────┐
│ ⚪ Data Engineer          [IDLE]           │
│ 🟢 Frontend Developer    [RUNNING]        │
│    └─ Task: Build dashboard               │
│ ⚪ Test Engineer          [IDLE]           │
│ 🟢 AI Engineer            [RUNNING]        │
│    └─ Task: Implement RAG                 │
│                                            │
│ [+ New Agent]  [⚙ Settings]               │
└────────────────────────────────────────────┘
```

**2. Agent Detail Window (`workbench/tui/windows/agent_detail_window.py`)**

Tabbed interface showing:

**[Config Tab]**
- Agent name, description
- System prompt editor
- Skills selector
- Rules editor
- Context files list

**[Tasks Tab]**
- Task queue (pending/in-progress/completed)
- Task history
- Dependencies visualization

**[Output Tab]**
- Live streaming output
- Tool call logs
- Error messages
- Copyable text

**[Memory Tab]**
- Agent's learned patterns
- Session history
- Context summary

```
┌─ Agent: Frontend Developer ───────────────┐
│ [Config] [Tasks] [Output] [Memory]        │
├────────────────────────────────────────────┤
│ ▼ Configuration                            │
│   Name: Frontend Developer                 │
│   Prompt: [Edit System Prompt]             │
│   Skills: react, typescript, testing       │
│   Rules:  [Edit rules.md]                  │
│   Files:  src/components/, package.json    │
│                                            │
│ ▼ Current Task                             │
│   Build user dashboard                     │
│   ✓ Read component requirements            │
│   ⧗ Create base component                  │
│   ⧗ Add state management                   │
│                                            │
│ [Start] [Pause] [Stop] [Save Config]       │
└────────────────────────────────────────────┘
```

## Execution Flow

### Creating & Starting an Agent

1. User clicks "New Agent" in Agents window
2. Config editor opens (or select template)
3. User configures prompt, skills, rules, context
4. User saves → `AgentManager.create_agent()`
5. Agent appears in list with state=IDLE
6. User assigns task → `AgentManager.start_agent(agent_id, task)`
7. Runtime creates orchestrator with agent's config
8. Agent state → RUNNING
9. Output streams to agent detail window
10. On completion, state → COMPLETED

### Task Processing

```
Agent Runtime Loop:
1. Check task queue
2. If task available:
   a. Set task.status = in_progress
   b. Build context (system_prompt + rules + context_files)
   c. Run orchestrator.run(task.description)
   d. Stream chunks to output window
   e. On completion:
      - task.status = completed
      - Save artifacts
      - Update agent stats
3. If queue empty:
   a. Set agent state = IDLE
   b. Wait for new task
```

## Implementation Phases

### Phase 1: Core Infrastructure
- [ ] Agent data models (`AgentConfig`, `AgentInstance`, `AgentTask`)
- [ ] `AgentManager` basic CRUD
- [ ] `AgentStore` persistence (YAML configs)
- [ ] Built-in agent configs (5-6 specialists)

### Phase 2: Runtime Execution
- [ ] `AgentRuntime` orchestrator integration
- [ ] Task queue management
- [ ] Output streaming
- [ ] State management & transitions

### Phase 3: TUI Integration
- [ ] `AgentsWindow` - list view
- [ ] `AgentDetailWindow` - config/tasks/output tabs
- [ ] Agent creation flow
- [ ] Start/stop/pause controls

### Phase 4: Advanced Features
- [ ] Agent memory/learning
- [ ] Template export/import
- [ ] Agent collaboration (shared context)
- [ ] Performance metrics & monitoring

## File Structure

```
workbench/
├── agents/
│   ├── __init__.py
│   ├── manager.py          # AgentManager
│   ├── runtime.py          # AgentRuntime
│   ├── store.py            # AgentStore
│   ├── models.py           # Data classes
│   └── builtin/
│       ├── data_engineer.yaml
│       ├── frontend_developer.yaml
│       ├── test_engineer.yaml
│       ├── ai_engineer.yaml
│       └── ...
├── tui/
│   └── windows/
│       ├── agents_window.py        # Agent list
│       └── agent_detail_window.py  # Agent details
└── ...

~/.workbench/
├── agents/
│   ├── configs/           # User agent configs
│   │   ├── agent-001.yaml
│   │   └── agent-002.yaml
│   └── sessions/          # Agent session history
│       ├── agent-001/
│       └── agent-002/
└── ...
```

## Benefits

✅ **Transparency**: See exactly what each agent is doing
✅ **Reproducibility**: Full audit trail of agent actions
✅ **Parallelism**: Run multiple agents simultaneously
✅ **Specialization**: Each agent optimized for specific tasks
✅ **Isolation**: Agents can't interfere with each other
✅ **Reusability**: Save agent configs as templates
✅ **Control**: Start/stop/pause agents on demand
