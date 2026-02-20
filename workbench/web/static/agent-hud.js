/**
 * Agent Activity — SSE stream for agent status updates.
 *
 * Connects to /api/agents/stream for real-time updates.
 * Renders agent details in the inline Agent Activity Panel (right drawer).
 * Also manages inbox waiting notifications.
 */

class AgentHud {
    constructor(app) {
        this.app = app;
        this.agents = {};
        this.stream = null;
        this.panelOpen = false;
        this._resizing = false;
        this._resizeStartX = 0;
        this._resizeStartWidth = 0;
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

        if (data.status === 'completed' || data.status === 'error') {
            // Keep for 30s then remove
            setTimeout(() => {
                delete this.agents[data.session_id];
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
    }

    closePanel() {
        const panel = document.getElementById('agentPanel');
        if (!panel) return;

        this.panelOpen = false;
        panel.style.display = 'none';
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
                const item = document.createElement('div');
                item.className = 'agent-panel__agent';

                const status = agent.pending_confirmation ? 'waiting' : (agent.status || 'running');
                const statusClass = `agent-panel__status-dot--${status}`;
                const sessionShort = (agent.session_id || '').substring(0, 8);
                const action = agent.current_action || 'Idle';
                const age = this.formatAge(agent.started_at);

                let approveHtml = '';
                if (agent.pending_confirmation) {
                    approveHtml = `<button class="agent-panel__approve-btn" data-session="${agent.session_id}" data-tcid="${agent.pending_confirmation.tool_call_id}">Approve</button>`;
                }

                item.innerHTML = `
                    <span class="agent-panel__status-dot ${statusClass}"></span>
                    <div class="agent-panel__agent-info">
                        <div class="agent-panel__agent-session">${sessionShort}...</div>
                        <div class="agent-panel__agent-action">${this.app.escapeHtml(action)}</div>
                    </div>
                    <span class="agent-panel__agent-age">${age}</span>
                    ${approveHtml}
                `;

                // Click navigates to conversation
                item.addEventListener('click', (e) => {
                    if (e.target.classList.contains('agent-panel__approve-btn')) return;
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

                section.appendChild(item);
            }

            body.appendChild(section);
        }
    }

    // ---- Inbox waiting notifications ----

    renderInboxNotifications() {
        const container = document.getElementById('inboxWaitingNotices');
        if (!container) {
            // Create container if it doesn't exist yet
            const inboxList = document.getElementById('inboxList');
            if (!inboxList) return;
            const notices = document.createElement('div');
            notices.id = 'inboxWaitingNotices';
            inboxList.parentNode.insertBefore(notices, inboxList);
        }

        const noticeContainer = document.getElementById('inboxWaitingNotices');
        if (!noticeContainer) return;

        // Find agents that are waiting for user input
        const waitingAgents = Object.values(this.agents).filter(a => a.pending_confirmation);

        if (waitingAgents.length === 0) {
            noticeContainer.innerHTML = '';
            return;
        }

        noticeContainer.innerHTML = '';
        for (const agent of waitingAgents) {
            const notice = document.createElement('div');
            notice.className = 'inbox-waiting-notice';

            const wsName = agent.workspace_name || 'Unknown';
            const toolName = agent.pending_confirmation?.tool_name || 'action';

            notice.innerHTML = `
                <span class="inbox-waiting-notice__dot"></span>
                <span class="inbox-waiting-notice__text">
                    <strong>${this.app.escapeHtml(wsName)}</strong> is waiting for approval on <strong>${this.app.escapeHtml(toolName)}</strong>
                </span>
                <span class="inbox-waiting-notice__action">View →</span>
            `;

            notice.addEventListener('click', () => {
                this.app.switchWindow('inbox');
                this.app.selectSession(agent.session_id);
            });

            noticeContainer.appendChild(notice);
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
