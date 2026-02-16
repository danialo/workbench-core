/**
 * Triage Window — investigation management with embedded chat.
 *
 * Three-panel layout:
 *   Left:   Investigation list (always visible)
 *   Center: Detail view OR embedded chat (switchable)
 *   Right:  Intake panel for new investigations (hideable)
 *
 * Case query integration reads from ~/.workbench/integrations.json
 * to populate investigations from external systems (Jira, ServiceNow, Glean, etc.)
 */

class TriageWindow {
    constructor(app) {
        this.app = app;
        this.investigations = [];
        this.activeInvestigationId = null;
        this.activeInvestigation = null;
        this.centerView = 'empty'; // 'empty' | 'detail' | 'chat'
        this.intakePanelOpen = false;
    }

    activate() {
        this.fetchInvestigations();
    }

    deactivate() {
        // Return chat to inbox if we reparented it
        if (this.centerView === 'chat') {
            this.app.returnChat();
            const conv = document.getElementById('conversationView');
            if (conv) conv.style.display = 'none';
        }
        this.centerView = 'empty';
    }

    bindEvents() {
        const btnNew = document.getElementById('btnNewInvestigation');
        if (btnNew) btnNew.addEventListener('click', () => this.openIntakePanel());

        const filter = document.getElementById('investigationFilter');
        if (filter) filter.addEventListener('change', () => this.fetchInvestigations());

        // Intake panel controls
        const btnClose = document.getElementById('btnCloseIntake');
        if (btnClose) btnClose.addEventListener('click', () => this.closeIntakePanel());

        const btnCancel = document.getElementById('btnCancelIntake');
        if (btnCancel) btnCancel.addEventListener('click', () => this.closeIntakePanel());

        const btnFetch = document.getElementById('btnFetchCase');
        if (btnFetch) btnFetch.addEventListener('click', () => this.fetchCaseData());

        const btnSubmit = document.getElementById('btnSubmitIntake');
        if (btnSubmit) btnSubmit.addEventListener('click', () => this.submitInvestigation());

        // Chat back button
        const btnBack = document.getElementById('btnTriageBackToDetail');
        if (btnBack) btnBack.addEventListener('click', () => this.showDetailView());

        // Enter key on case ID input triggers fetch
        const caseInput = document.getElementById('intakeCaseId');
        if (caseInput) {
            caseInput.addEventListener('keydown', (e) => {
                if (e.key === 'Enter') this.fetchCaseData();
            });
        }

        // Search input
        const search = document.getElementById('investigationSearch');
        if (search) {
            search.addEventListener('input', () => this.renderList());
        }

        // Escape closes intake panel
        document.addEventListener('keydown', (e) => {
            if (e.key === 'Escape' && this.intakePanelOpen) this.closeIntakePanel();
        });
    }

    // ---- Data fetching ----

    async fetchInvestigations() {
        const filter = document.getElementById('investigationFilter');
        const status = filter ? filter.value : 'open';
        const query = status === 'all' ? '' : `?status=${status}`;

        try {
            const data = await this.app.apiFetch(`/api/investigations${query}`);
            this.investigations = data.investigations || [];
            this.renderList();
        } catch (e) {
            console.warn('Could not fetch investigations:', e);
            this.investigations = [];
            this.renderList();
        }
    }

    // ---- Investigation list ----

    renderList() {
        const container = document.getElementById('investigationList');
        if (!container) return;

        if (this.investigations.length === 0) {
            container.innerHTML = '<div class="investigation-list__empty">No investigations. Click "+ New Investigation" to create one.</div>';
            return;
        }

        // Filter by search term
        const searchEl = document.getElementById('investigationSearch');
        const query = (searchEl ? searchEl.value : '').trim().toLowerCase();
        const filtered = query
            ? this.investigations.filter(inv => {
                const systems = Array.isArray(inv.affected_systems) ? inv.affected_systems.join(' ') : '';
                const haystack = `${inv.title} ${inv.description || ''} ${systems} ${inv.severity}`.toLowerCase();
                return haystack.includes(query);
            })
            : this.investigations;

        if (filtered.length === 0) {
            container.innerHTML = `<div class="investigation-list__empty">No matches for "${this.app.escapeHtml(query)}"</div>`;
            return;
        }

        container.innerHTML = '';
        for (const inv of filtered) {
            const card = document.createElement('div');
            card.className = `investigation-card investigation-card--${inv.severity}`;
            if (inv.investigation_id === this.activeInvestigationId) {
                card.classList.add('investigation-card--active');
            }

            const age = this.timeAgo(inv.created_at);
            const systems = Array.isArray(inv.affected_systems) ? inv.affected_systems.join(', ') : '';
            const hasSession = !!inv.session_id;

            card.innerHTML = `
                <span class="investigation-card__severity">${this.app.escapeHtml(inv.severity)}</span>
                <div class="investigation-card__content">
                    <div class="investigation-card__title">${this.app.escapeHtml(inv.title)}</div>
                    <div class="investigation-card__meta">${age}${systems ? ' \u00b7 ' + this.app.escapeHtml(systems) : ''}</div>
                </div>
                <div class="investigation-card__agent-status">
                    <span class="agent-hud__dot agent-hud__dot--${inv.status === 'resolved' ? 'completed' : 'running'}"></span>
                    <span>${this.app.escapeHtml(inv.status)}</span>
                    ${hasSession ? '<span class="investigation-card__chat-icon" title="Has conversation">💬</span>' : ''}
                </div>
            `;

            card.addEventListener('click', () => this.selectInvestigation(inv.investigation_id));
            container.appendChild(card);
        }
    }

    // ---- Investigation selection & center view ----

    async selectInvestigation(investigationId) {
        this.activeInvestigationId = investigationId;
        this.renderList();

        try {
            const data = await this.app.apiFetch(`/api/investigations/${investigationId}`);
            this.activeInvestigation = data;
            this.showDetailView(data);
        } catch (e) {
            console.error('Failed to fetch investigation:', e);
        }
    }

    showDetailView(investigation) {
        const inv = investigation || this.activeInvestigation;
        if (!inv) return;

        // If we were in chat mode, return the conversation DOM
        if (this.centerView === 'chat') {
            this.app.returnChat();
            const conv = document.getElementById('conversationView');
            if (conv) conv.style.display = 'none';
        }

        this.centerView = 'detail';

        // Toggle visibility
        document.getElementById('triageEmptyState').style.display = 'none';
        document.getElementById('triageChatView').style.display = 'none';
        document.getElementById('triageDetailView').style.display = 'flex';

        this.renderDetail(inv);
    }

    renderDetail(inv) {
        const detail = document.getElementById('investigationDetail');
        if (!detail) return;

        const checklist = Array.isArray(inv.checklist) ? inv.checklist : [];
        const checklistHtml = checklist.map((item, i) => `
            <li class="${item.checked ? 'checked' : ''}" data-idx="${i}">
                <span class="investigation-detail__checklist-box">${item.checked ? '&#10003;' : ''}</span>
                ${this.app.escapeHtml(item.label)}
            </li>
        `).join('');

        const systems = Array.isArray(inv.affected_systems) ? inv.affected_systems.join(', ') : '';
        const hasSession = !!inv.session_id;

        detail.innerHTML = `
            <div class="investigation-detail__header">
                <h2 class="investigation-detail__title">${this.app.escapeHtml(inv.title)}</h2>
                <div class="investigation-detail__fields">
                    <div class="investigation-detail__field">
                        <span class="investigation-detail__field-label">Severity:</span>
                        <span class="investigation-card__severity investigation-card--${inv.severity}"
                              style="display:inline-block">${this.app.escapeHtml(inv.severity.toUpperCase())}</span>
                    </div>
                    <div class="investigation-detail__field">
                        <span class="investigation-detail__field-label">Status:</span>
                        <span class="investigation-detail__field-value">${this.app.escapeHtml(inv.status)}</span>
                    </div>
                    <div class="investigation-detail__field">
                        <span class="investigation-detail__field-label">Age:</span>
                        <span class="investigation-detail__field-value">${this.timeAgo(inv.created_at)}</span>
                    </div>
                    ${systems ? `
                    <div class="investigation-detail__field">
                        <span class="investigation-detail__field-label">Systems:</span>
                        <span class="investigation-detail__field-value">${this.app.escapeHtml(systems)}</span>
                    </div>` : ''}
                </div>
            </div>
            ${inv.description ? `<div class="investigation-detail__description">${this.app.escapeHtml(inv.description)}</div>` : ''}
            ${checklist.length > 0 ? `
                <div class="investigation-detail__section-title">Checklist</div>
                <ul class="investigation-detail__checklist">${checklistHtml}</ul>
            ` : ''}
            <div class="investigation-detail__chat-section">
                ${hasSession ? `
                    <button class="investigation-detail__chat-btn" id="btnOpenChat">
                        💬 Open Conversation
                    </button>
                ` : `
                    <button class="investigation-detail__chat-btn investigation-detail__chat-btn--start" id="btnStartChat">
                        + Start Investigation Chat
                    </button>
                `}
            </div>
            <div class="investigation-detail__actions">
                <button class="investigation-detail__action-btn investigation-detail__action-btn--escalate" id="btnEscalate">Escalate</button>
                <button class="investigation-detail__action-btn investigation-detail__action-btn--resolve" id="btnResolve">Resolve</button>
            </div>
        `;

        // Wire checklist toggles
        detail.querySelectorAll('.investigation-detail__checklist li').forEach(li => {
            li.addEventListener('click', () => {
                const idx = parseInt(li.dataset.idx);
                this.toggleChecklistItem(inv.investigation_id, checklist, idx);
            });
        });

        // Wire chat button
        const btnOpen = detail.querySelector('#btnOpenChat');
        if (btnOpen) {
            btnOpen.addEventListener('click', () => this.showChatView(inv.session_id, inv.title));
        }
        const btnStart = detail.querySelector('#btnStartChat');
        if (btnStart) {
            btnStart.addEventListener('click', () => this.startChat(inv));
        }

        // Wire action buttons
        const btnEscalate = detail.querySelector('#btnEscalate');
        if (btnEscalate) btnEscalate.addEventListener('click', () => this.escalateInvestigation(inv.investigation_id));
        const btnResolve = detail.querySelector('#btnResolve');
        if (btnResolve) btnResolve.addEventListener('click', () => this.resolveInvestigation(inv.investigation_id));
    }

    // ---- Embedded chat ----

    async showChatView(sessionId, investigationTitle) {
        this.centerView = 'chat';

        // Toggle visibility
        document.getElementById('triageEmptyState').style.display = 'none';
        document.getElementById('triageDetailView').style.display = 'none';
        document.getElementById('triageChatView').style.display = 'flex';

        // Update title
        const titleEl = document.getElementById('triageChatTitle');
        if (titleEl) titleEl.textContent = investigationTitle || 'Investigation';

        // Reparent the conversation view into triage
        this.app.reparentChat('triageChatContainer');

        // Load the session and show it
        await this.app.selectSession(sessionId);
        const conv = document.getElementById('conversationView');
        if (conv) conv.style.display = 'flex';
    }

    async startChat(investigation) {
        try {
            // Create a new session linked to this investigation
            const session = await this.app.apiFetch('/api/sessions', {
                method: 'POST',
                body: JSON.stringify({
                    workspace_id: investigation.workspace_id || this.app.activeWorkspaceId,
                    metadata: { investigation_id: investigation.investigation_id },
                }),
            });

            // Link session to investigation
            await this.app.apiFetch(`/api/investigations/${investigation.investigation_id}`, {
                method: 'PUT',
                body: JSON.stringify({ session_id: session.session_id }),
            });

            // Update local state
            investigation.session_id = session.session_id;
            this.activeInvestigation = investigation;

            // Switch to chat view
            await this.showChatView(session.session_id, investigation.title);
        } catch (e) {
            console.error('Failed to start investigation chat:', e);
        }
    }

    // ---- Intake panel ----

    openIntakePanel() {
        this.intakePanelOpen = true;
        const panel = document.getElementById('triageIntakePanel');
        const body = document.getElementById('triageBody');
        if (panel) panel.style.display = 'flex';
        if (body) body.classList.add('triage-window__body--intake-open');

        // Clear form
        ['intakeCaseId', 'intakeTitle', 'intakeSystems', 'intakeDescription'].forEach(id => {
            const el = document.getElementById(id);
            if (el) el.value = '';
        });
        const sev = document.getElementById('intakeSeverity');
        if (sev) sev.value = 'medium';
        const status = document.getElementById('intakeFetchStatus');
        if (status) status.innerHTML = '';

        // Focus case ID
        setTimeout(() => {
            const el = document.getElementById('intakeCaseId');
            if (el) el.focus();
        }, 100);
    }

    closeIntakePanel() {
        this.intakePanelOpen = false;
        const panel = document.getElementById('triageIntakePanel');
        const body = document.getElementById('triageBody');
        if (panel) panel.style.display = 'none';
        if (body) body.classList.remove('triage-window__body--intake-open');
    }

    async fetchCaseData() {
        const caseId = document.getElementById('intakeCaseId')?.value.trim();
        if (!caseId) return;

        const btn = document.getElementById('btnFetchCase');
        const status = document.getElementById('intakeFetchStatus');

        if (btn) { btn.textContent = 'Fetching...'; btn.disabled = true; }
        if (status) status.innerHTML = '<span class="intake-panel__fetch-loading">Querying sources...</span>';

        try {
            const data = await this.app.apiFetch('/api/investigations/fetch-case', {
                method: 'POST',
                body: JSON.stringify({ case_id: caseId }),
            });

            // Populate form fields
            const titleEl = document.getElementById('intakeTitle');
            const sevEl = document.getElementById('intakeSeverity');
            const sysEl = document.getElementById('intakeSystems');
            const descEl = document.getElementById('intakeDescription');

            if (titleEl && data.title) titleEl.value = data.title;
            if (sevEl && data.severity) sevEl.value = data.severity;
            if (sysEl && data.affected_systems) sysEl.value = (data.affected_systems || []).join(', ');
            if (descEl && data.description) descEl.value = data.description;

            const source = data.source || data._source || 'unknown';
            if (status) status.innerHTML = `<span class="intake-panel__fetch-success">Populated from ${this.app.escapeHtml(source)}</span>`;

        } catch (e) {
            if (status) status.innerHTML = `<span class="intake-panel__fetch-error">Failed: ${this.app.escapeHtml(e.message)}</span>`;
        } finally {
            if (btn) { btn.textContent = 'Fetch'; btn.disabled = false; }
        }
    }

    async submitInvestigation() {
        const title = document.getElementById('intakeTitle')?.value.trim();
        if (!title) {
            document.getElementById('intakeTitle')?.focus();
            return;
        }

        const severity = document.getElementById('intakeSeverity')?.value || 'medium';
        const systems = (document.getElementById('intakeSystems')?.value || '')
            .split(',').map(s => s.trim()).filter(Boolean);
        const description = document.getElementById('intakeDescription')?.value.trim() || '';

        try {
            await this.app.apiFetch('/api/investigations', {
                method: 'POST',
                body: JSON.stringify({
                    title, severity, affected_systems: systems, description,
                    workspace_id: this.app.activeWorkspaceId,
                }),
            });
            this.closeIntakePanel();
            this.fetchInvestigations();
        } catch (e) {
            console.error('Failed to create investigation:', e);
        }
    }

    // ---- Investigation actions ----

    async toggleChecklistItem(investigationId, checklist, idx) {
        checklist[idx].checked = !checklist[idx].checked;
        try {
            await this.app.apiFetch(`/api/investigations/${investigationId}`, {
                method: 'PUT',
                body: JSON.stringify({ checklist }),
            });
            this.selectInvestigation(investigationId);
        } catch (e) {
            console.error('Failed to update checklist:', e);
        }
    }

    async escalateInvestigation(investigationId) {
        try {
            await this.app.apiFetch(`/api/investigations/${investigationId}/escalate`, { method: 'POST' });
            this.fetchInvestigations();
            this.selectInvestigation(investigationId);
        } catch (e) {
            console.error('Failed to escalate:', e);
        }
    }

    async resolveInvestigation(investigationId) {
        try {
            await this.app.apiFetch(`/api/investigations/${investigationId}/resolve`, { method: 'POST' });
            this.fetchInvestigations();
            document.getElementById('triageDetailView').style.display = 'none';
            document.getElementById('triageEmptyState').style.display = 'flex';
            this.activeInvestigationId = null;
            this.activeInvestigation = null;
            this.centerView = 'empty';
        } catch (e) {
            console.error('Failed to resolve:', e);
        }
    }

    // ---- Utilities ----

    timeAgo(dateStr) {
        if (!dateStr) return '';
        try {
            const now = Date.now();
            const then = new Date(dateStr).getTime();
            const diff = now - then;
            const mins = Math.floor(diff / 60000);
            if (mins < 1) return 'just now';
            if (mins < 60) return `${mins}m ago`;
            const hours = Math.floor(mins / 60);
            if (hours < 24) return `${hours}h ago`;
            const days = Math.floor(hours / 24);
            return `${days}d ago`;
        } catch {
            return '';
        }
    }
}
