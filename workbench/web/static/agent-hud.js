/**
 * Agent Activity — SSE stream for agent status updates.
 *
 * Connects to /api/agents/stream for real-time updates.
 * Renders agent details in the inline Agent Activity Panel (right drawer).
 * Also manages inbox waiting/completion notifications.
 */

class AgentHud {
    constructor(app) {
        this.app = app;
        this.agents = {};
        this.stream = null;
        this.panelOpen = false;
        this.selectedAgentId = null;
        this._resizing = false;
        this._resizeStartX = 0;
        this._resizeStartWidth = 0;
        // Track which agents have their activity log expanded
        this._expanded = {};
        // Track which completed agents have already sent an inbox notice
        this._completedNotified = new Set();
    }

    start() {
        if (this.stream) return;

        // Bind panel buttons
        const expandBtn = document.getElementById('btnExpandAgentPanel');
        if (expandBtn) {
            expandBtn.addEventListener('click', () => this.togglePanel());
        }
        const closeBtn = document.getElementById('btnCloseAgentPanel');
        if (closeBtn) {
            closeBtn.addEventListener('click', () => this.closePanel());
        }

        // Resize handle
        const handle = document.getElementById('agentPanelResizeHandle');
        if (handle) {
            handle.addEventListener('mousedown', (e) => this._startResize(e));
        }

        // Agent panel input
        const sendBtn = document.getElementById('agentPanelSend');
        const textarea = document.getElementById('agentPanelTextarea');
        if (sendBtn) {
            sendBtn.addEventListener('click', () => this.sendToAgent());
        }
        if (textarea) {
            textarea.addEventListener('keydown', (e) => {
                if (e.key === 'Enter' && !e.shiftKey) {
                    e.preventDefault();
                    this.sendToAgent();
                }
            });
        }

        try {
            const es = new EventSource('/api/agents/stream');
            this.stream = es;

            es.addEventListener('agent_update', (e) => {
                try {
                    const data = JSON.parse(e.data);
                    this.handleUpdate(data);
                } catch (err) {
                    console.warn('Agent HUD parse error:', err);
                }
            });

            es.onerror = () => {
                es.close();
                this.stream = null;
                setTimeout(() => this.start(), 5000);
            };
        } catch (e) {
            console.warn('Agent HUD stream not available:', e);
        }
    }

    stop() {
        if (this.stream) {
            this.stream.close();
            this.stream = null;
        }
    }

    handleUpdate(data) {
        const prevAgent = this.agents[data.session_id];
        this.agents[data.session_id] = data;

        // Send a completion inbox notice the first time an agent finishes
        const justCompleted = (data.status === 'completed' || data.status === 'error')
            && (!prevAgent || (prevAgent.status !== 'completed' && prevAgent.status !== 'error'));
        if (justCompleted && !this._completedNotified.has(data.session_id)) {
            this._completedNotified.add(data.session_id);
        }

        if (data.status === 'completed' || data.status === 'error') {
            // Keep for 30s then remove
            setTimeout(() => {
                delete this.agents[data.session_id];
                if (this.selectedAgentId === data.session_id) {
                    this.selectedAgentId = null;
                    const inputArea = document.getElementById('agentPanelInput');
                    if (inputArea) inputArea.style.display = 'none';
                }
                this.renderPanel();
                this.renderInboxNotifications();
            }, 30000);
        }

        this.renderPanel();
        this.renderInboxNotifications();
    }

    // ---- Agent Activity Panel (inline) ----

    togglePanel() {
        if (this.panelOpen) {
            this.closePanel();
        } else {
            this.openPanel();
        }
    }

    openPanel() {
        const panel = document.getElementById('agentPanel');
        if (!panel) return;

        this.panelOpen = true;
        panel.style.display = 'flex';
        this.renderPanel();
        this.app.saveUIState();
    }

    closePanel() {
        const panel = document.getElementById('agentPanel');
        if (!panel) return;

        this.panelOpen = false;
        this.selectedAgentId = null;
        panel.style.display = 'none';
        const inputArea = document.getElementById('agentPanelInput');
        if (inputArea) inputArea.style.display = 'none';
        this.app.saveUIState();
    }

    // ---- Resize handle ----

    _startResize(e) {
        e.preventDefault();
        const panel = document.getElementById('agentPanel');
        if (!panel) return;

        this._resizing = true;
        this._resizeStartX = e.clientX;
        this._resizeStartWidth = panel.offsetWidth;

        const handle = document.getElementById('agentPanelResizeHandle');
        if (handle) handle.classList.add('agent-panel__resize-handle--active');

        const onMove = (ev) => {
            if (!this._resizing) return;
            const delta = this._resizeStartX - ev.clientX; // dragging left increases width
            const newWidth = Math.max(200, Math.min(500, this._resizeStartWidth + delta));
            panel.style.width = newWidth + 'px';
        };

        const onUp = () => {
            this._resizing = false;
            if (handle) handle.classList.remove('agent-panel__resize-handle--active');
            document.removeEventListener('mousemove', onMove);
            document.removeEventListener('mouseup', onUp);
            this.app.saveUIState();
        };

        document.addEventListener('mousemove', onMove);
        document.addEventListener('mouseup', onUp);
    }

    // ---- Panel rendering ----

    renderPanel() {
        const body = document.getElementById('agentPanelBody');
        if (!body) return;
        if (!this.panelOpen) return;

        const entries = Object.values(this.agents);
        if (entries.length === 0) {
            body.innerHTML = '<div class="agent-panel__empty">No active agents</div>';
            return;
        }

        // Sort: active first, then waiting, then completed/error
        const statusOrder = { running: 0, waiting: 1, error: 2, completed: 3 };
        entries.sort((a, b) => {
            const as = a.pending_confirmation ? 1 : (statusOrder[a.status] ?? 99);
            const bs = b.pending_confirmation ? 1 : (statusOrder[b.status] ?? 99);
            return as - bs;
        });

        // Group agents by workspace
        const groups = {};
        for (const agent of entries) {
            const wsName = agent.workspace_name || 'Unknown';
            const wsId = agent.workspace_id || 'unknown';
            if (!groups[wsId]) {
                groups[wsId] = { name: wsName, agents: [] };
            }
            groups[wsId].agents.push(agent);
        }

        body.innerHTML = '';

        for (const [wsId, group] of Object.entries(groups)) {
            const section = document.createElement('div');
            section.className = 'agent-panel__group';

            const header = document.createElement('div');
            header.className = 'agent-panel__group-header';
            header.innerHTML = `
                <span class="agent-panel__group-icon">📁</span>
                <span class="agent-panel__group-name">${this.app.escapeHtml(group.name)}</span>
                <span class="agent-panel__group-count">${group.agents.length}</span>
            `;
            section.appendChild(header);

            for (const agent of group.agents) {
                section.appendChild(this._buildAgentItem(agent));
            }

            body.appendChild(section);
        }
    }

    _buildAgentItem(agent) {
        const wrapper = document.createElement('div');
        wrapper.className = 'agent-panel__agent-wrapper';

        const item = document.createElement('div');
        item.className = 'agent-panel__agent';

        const status = agent.pending_confirmation ? 'waiting' : (agent.status || 'running');
        const statusClass = `agent-panel__status-dot--${status}`;

        // Label: prefer explicit label, fall back to short session ID
        const label = agent.label
            ? this.app.escapeHtml(agent.label)
            : `<span class="agent-panel__agent-id">${(agent.session_id || '').substring(0, 8)}…</span>`;

        const action = agent.current_action || (status === 'completed' ? 'Finished' : status === 'error' ? 'Error' : 'Idle');
        const age = this.formatAge(agent.started_at);
        const hasHistory = (agent.action_history || []).length > 0;
        const isExpanded = this._expanded[agent.session_id];

        let approveHtml = '';
        if (agent.pending_confirmation) {
            approveHtml = `<button class="agent-panel__approve-btn" data-session="${agent.session_id}" data-tcid="${agent.pending_confirmation.tool_call_id}">Approve</button>`;
        }

        const isStoppable = status === 'running' || status === 'waiting';
        const stopHtml = isStoppable
            ? `<button class="agent-panel__stop-btn" data-session="${agent.session_id}" title="Stop agent">■</button>`
            : '';

        const dropdownHtml = hasHistory
            ? `<button class="agent-panel__dropdown-btn ${isExpanded ? 'agent-panel__dropdown-btn--open' : ''}" data-session="${agent.session_id}" title="${isExpanded ? 'Collapse' : 'Expand'} activity log">▾</button>`
            : '';

        item.innerHTML = `
            <span class="agent-panel__status-dot ${statusClass}"></span>
            <div class="agent-panel__agent-info">
                <div class="agent-panel__agent-label">${label}</div>
                <div class="agent-panel__agent-action">${this.app.escapeHtml(action)}</div>
            </div>
            <span class="agent-panel__agent-age">${age}</span>
            ${approveHtml}
            ${stopHtml}
            ${dropdownHtml}
        `;

        // Click navigates to conversation
        item.addEventListener('click', (e) => {
            if (e.target.classList.contains('agent-panel__approve-btn')) return;
            if (e.target.classList.contains('agent-panel__stop-btn')) return;
            if (e.target.classList.contains('agent-panel__dropdown-btn')) return;
            this.app.switchWindow('inbox');
            this.app.selectSession(agent.session_id);
        });

        // Wire approve button
        const btn = item.querySelector('.agent-panel__approve-btn');
        if (btn) {
            btn.addEventListener('click', (e) => {
                e.stopPropagation();
                this.approve(agent.session_id, agent.pending_confirmation.tool_call_id);
            });
        }

        // Wire stop button
        const stopBtn = item.querySelector('.agent-panel__stop-btn');
        if (stopBtn) {
            stopBtn.addEventListener('click', (e) => {
                e.stopPropagation();
                this.stopAgent(agent.session_id);
            });
        }

        // Wire dropdown toggle
        const dropBtn = item.querySelector('.agent-panel__dropdown-btn');
        if (dropBtn) {
            dropBtn.addEventListener('click', (e) => {
                e.stopPropagation();
                this._expanded[agent.session_id] = !this._expanded[agent.session_id];
                this.renderPanel();
            });
        }

        wrapper.appendChild(item);

        // Inline activity log (expandable)
        if (hasHistory && isExpanded) {
            const log = document.createElement('div');
            log.className = 'agent-panel__log';
            const history = agent.action_history || [];
            for (const entry of history) {
                const row = document.createElement('div');
                row.className = 'agent-panel__log-entry';
                row.textContent = entry;
                log.appendChild(row);
            }
            wrapper.appendChild(log);
        }

        return wrapper;
    }

    // ---- Inbox waiting + completion notifications ----

    renderInboxNotifications() {
        let container = document.getElementById('inboxWaitingNotices');
        if (!container) {
            const inboxList = document.getElementById('inboxList');
            if (!inboxList) return;
            const notices = document.createElement('div');
            notices.id = 'inboxWaitingNotices';
            inboxList.parentNode.insertBefore(notices, inboxList);
        }

        container = document.getElementById('inboxWaitingNotices');
        if (!container) return;

        const waitingAgents = Object.values(this.agents).filter(a => a.pending_confirmation);
        const completedAgents = Object.values(this.agents).filter(
            a => (a.status === 'completed' || a.status === 'error') && this._completedNotified.has(a.session_id)
        );

        if (waitingAgents.length === 0 && completedAgents.length === 0) {
            container.innerHTML = '';
            return;
        }

        container.innerHTML = '';

        // Waiting notices
        for (const agent of waitingAgents) {
            const notice = document.createElement('div');
            notice.className = 'inbox-waiting-notice';

            const wsName = agent.workspace_name || 'Unknown';
            const toolName = agent.pending_confirmation?.tool_name || 'action';
            const label = agent.label || `${(agent.session_id || '').substring(0, 8)}…`;

            notice.innerHTML = `
                <span class="inbox-waiting-notice__dot"></span>
                <span class="inbox-waiting-notice__text">
                    <strong>${this.app.escapeHtml(label)}</strong> is waiting for approval on <strong>${this.app.escapeHtml(toolName)}</strong>
                </span>
                <span class="inbox-waiting-notice__action">View →</span>
            `;

            notice.addEventListener('click', () => {
                this.app.switchWindow('inbox');
                this.app.selectSession(agent.session_id);
            });

            container.appendChild(notice);
        }

        // Completion notices
        for (const agent of completedAgents) {
            const notice = document.createElement('div');
            notice.className = 'inbox-completed-notice';
            const label = agent.label || `${(agent.session_id || '').substring(0, 8)}…`;
            const isError = agent.status === 'error';

            notice.innerHTML = `
                <span class="inbox-completed-notice__dot ${isError ? 'inbox-completed-notice__dot--error' : ''}"></span>
                <span class="inbox-completed-notice__text">
                    <strong>${this.app.escapeHtml(label)}</strong> ${isError ? 'encountered an error' : 'finished'}
                </span>
                <span class="inbox-completed-notice__dismiss" data-session="${agent.session_id}" title="Dismiss">✕</span>
            `;

            notice.querySelector('.inbox-completed-notice__dismiss').addEventListener('click', (e) => {
                e.stopPropagation();
                this._completedNotified.delete(agent.session_id);
                this.renderInboxNotifications();
            });

            notice.addEventListener('click', (e) => {
                if (e.target.classList.contains('inbox-completed-notice__dismiss')) return;
                this.app.switchWindow('inbox');
                this.app.selectSession(agent.session_id);
            });

            container.appendChild(notice);
        }
    }

    formatAge(startedAt) {
        if (!startedAt) return '';
        try {
            const now = Date.now();
            const then = new Date(startedAt).getTime();
            const diff = now - then;
            const secs = Math.floor(diff / 1000);
            if (secs < 60) return `${secs}s`;
            const mins = Math.floor(secs / 60);
            if (mins < 60) return `${mins}m`;
            const hours = Math.floor(mins / 60);
            return `${hours}h`;
        } catch {
            return '';
        }
    }

    selectAgent(sessionId) {
        this.selectedAgentId = sessionId;
        // Show input area
        const inputArea = document.getElementById('agentPanelInput');
        if (inputArea) inputArea.style.display = 'flex';
        // Focus textarea
        const textarea = document.getElementById('agentPanelTextarea');
        if (textarea) textarea.focus();
        // Re-render to update selection highlight
        this.renderPanel();
    }

    async sendToAgent() {
        if (!this.selectedAgentId) return;
        const textarea = document.getElementById('agentPanelTextarea');
        if (!textarea) return;
        const content = textarea.value.trim();
        if (!content) return;

        textarea.value = '';
        try {
            await this.app.apiFetch(`/api/sessions/${this.selectedAgentId}/stream`, {
                method: 'POST',
                body: JSON.stringify({ content }),
            });
        } catch (e) {
            console.error('Failed to send to agent:', e);
        }
    }

    async stopAgent(sessionId) {
        try {
            await this.app.apiFetch(`/api/agents/${sessionId}/stop`, { method: 'POST' });
        } catch (e) {
            console.error('HUD stop failed:', e);
        }
    }

    async approve(sessionId, toolCallId) {
        try {
            await this.app.apiFetch(`/api/sessions/${sessionId}/confirm`, {
                method: 'POST',
                body: JSON.stringify({ tool_call_id: toolCallId, confirmed: true }),
            });
        } catch (e) {
            console.error('HUD approve failed:', e);
        }
    }
}
