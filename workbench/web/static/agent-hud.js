/**
 * Agent HUD — persistent overlay showing all active agents across workspaces.
 *
 * Connects to /api/agents/stream (SSE) for real-time updates.
 * Renders agent items with status dots and inline approve buttons.
 *
 * Also manages the dockable Agent Activity Panel which expands from the HUD.
 */

class AgentHud {
    constructor(app) {
        this.app = app;
        this.agents = {};
        this.stream = null;
        this.panelOpen = false;
        this.panelDock = 'right'; // 'right' or 'left'
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
        const dockBtn = document.getElementById('btnDockPanel');
        if (dockBtn) {
            dockBtn.addEventListener('click', () => this.toggleDock());
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
        if (data.status === 'completed' || data.status === 'error') {
            // Keep for 30s then remove
            this.agents[data.session_id] = data;
            setTimeout(() => {
                delete this.agents[data.session_id];
                this.render();
                if (this.panelOpen) this.renderPanel();
            }, 30000);
        } else {
            this.agents[data.session_id] = data;
        }
        this.render();
        if (this.panelOpen) this.renderPanel();
    }

    render() {
        const list = document.getElementById('agentHudList');
        if (!list) return;

        const entries = Object.values(this.agents);
        if (entries.length === 0) {
            list.innerHTML = '<div class="agent-hud__empty">No active agents</div>';
            return;
        }

        list.innerHTML = '';
        for (const agent of entries) {
            const item = document.createElement('div');
            item.className = 'agent-hud__item';

            const statusClass = `agent-hud__dot--${agent.status || 'running'}`;
            const action = agent.current_action || '';
            const wsName = agent.workspace_name || 'Unknown';

            let approveBtn = '';
            if (agent.pending_confirmation) {
                approveBtn = `<button class="agent-hud__approve-btn" data-session="${agent.session_id}" data-tcid="${agent.pending_confirmation.tool_call_id}">Approve</button>`;
            }

            item.innerHTML = `
                <span class="agent-hud__dot ${statusClass}"></span>
                <span class="agent-hud__name">${this.app.escapeHtml(wsName)}</span>
                <span class="agent-hud__action">${this.app.escapeHtml(action)}</span>
                ${approveBtn}
            `;

            // Click to jump to that session in Inbox
            item.addEventListener('click', (e) => {
                if (e.target.classList.contains('agent-hud__approve-btn')) return;
                this.app.switchWindow('inbox');
                this.app.selectSession(agent.session_id);
            });

            // Wire approve button
            const btn = item.querySelector('.agent-hud__approve-btn');
            if (btn) {
                btn.addEventListener('click', (e) => {
                    e.stopPropagation();
                    this.approve(agent.session_id, agent.pending_confirmation.tool_call_id);
                });
            }

            list.appendChild(item);
        }
    }

    // ---- Agent Activity Panel ----

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
        panel.className = `agent-panel agent-panel--${this.panelDock}`;
        this.renderPanel();

        // Animate in
        requestAnimationFrame(() => {
            panel.classList.add('agent-panel--open');
        });
    }

    closePanel() {
        const panel = document.getElementById('agentPanel');
        if (!panel) return;

        this.panelOpen = false;
        panel.classList.remove('agent-panel--open');
        setTimeout(() => {
            panel.style.display = 'none';
        }, 200);
    }

    toggleDock() {
        this.panelDock = this.panelDock === 'right' ? 'left' : 'right';
        const panel = document.getElementById('agentPanel');
        if (panel && this.panelOpen) {
            panel.className = `agent-panel agent-panel--${this.panelDock} agent-panel--open`;
        }
    }

    renderPanel() {
        const body = document.getElementById('agentPanelBody');
        if (!body) return;

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
            const key = wsId;
            if (!groups[key]) {
                groups[key] = { name: wsName, agents: [] };
            }
            groups[key].agents.push(agent);
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

                const statusClass = `agent-panel__status-dot--${agent.status || 'running'}`;
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
                    this.closePanel();
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
