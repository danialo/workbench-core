/**
 * Triage Window — investigation management with embedded chat.
 *
 * Three-panel layout:
 *   Left:   Investigation list (always visible)
 *   Center: Detail view (always visible when selected)
 *   Right:  Intake panel OR conversation panel (hideable)
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
        this.centerView = 'empty'; // 'empty' | 'detail'
        this.intakePanelOpen = false;
        this.chatPanelOpen = false;
    }

    activate() {
        this.fetchInvestigations();
    }

    deactivate() {
        // Return chat to inbox if we have it open
        if (this.chatPanelOpen) {
            this.closeChatPanel();
        }
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

        // Chat panel close
        const btnCloseChat = document.getElementById('btnCloseTriageChat');
        if (btnCloseChat) btnCloseChat.addEventListener('click', () => this.closeChatPanel());

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

        // Escape closes open panels
        document.addEventListener('keydown', (e) => {
            if (e.key === 'Escape') {
                if (this.intakePanelOpen) this.closeIntakePanel();
                else if (this.chatPanelOpen) this.closeChatPanel();
            }
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

        this.centerView = 'detail';

        // Toggle visibility
        document.getElementById('triageEmptyState').style.display = 'none';
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
            ${this.renderContextPills(inv)}
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

        // Wire context pills
        this.wireContextPills(inv);
    }

    // ---- Embedded chat (right panel) ----

    async showChatView(sessionId, investigationTitle) {
        // Close intake panel if open (they share the right slot)
        if (this.intakePanelOpen) this.closeIntakePanel();

        const panel = document.getElementById('triageChatPanel');
        const body = document.getElementById('triageBody');
        const titleEl = document.getElementById('triageChatTitle');

        if (panel) panel.style.display = 'flex';
        if (body) body.classList.add('triage-window__body--chat-open');
        if (titleEl) titleEl.textContent = investigationTitle || 'Investigation Chat';

        // Reparent the conversation view into triage chat panel
        this.app.reparentChat('triageChatContainer');

        // Load the session and show it
        await this.app.selectSession(sessionId);
        const conv = document.getElementById('conversationView');
        if (conv) conv.style.display = 'flex';

        this.chatPanelOpen = true;
    }

    closeChatPanel() {
        const panel = document.getElementById('triageChatPanel');
        const body = document.getElementById('triageBody');
        if (panel) panel.style.display = 'none';
        if (body) body.classList.remove('triage-window__body--chat-open');
        this.app.returnChat();
        this.chatPanelOpen = false;
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
        // Close chat panel if open (they share the right slot)
        if (this.chatPanelOpen) this.closeChatPanel();

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

    // ---- Context pill bar ----

    renderContextPills(inv) {
        const meta = (typeof inv.metadata === 'string' ? JSON.parse(inv.metadata || '{}') : inv.metadata) || {};
        const saved = meta.context || {};
        const fields = saved.fields || {};
        const customPills = saved.custom || [];

        const systemsStr = Array.isArray(inv.affected_systems) ? inv.affected_systems.join(', ') : '';

        const fieldDefs = [
            { key: 'title', label: 'Title', value: inv.title || '' },
            { key: 'severity', label: 'Severity', value: inv.severity || '' },
            { key: 'systems', label: 'Systems', value: systemsStr },
            { key: 'description', label: 'Description', value: inv.description || '' },
            { key: 'case_data', label: 'Case Data', value: '' },
        ];

        const pillsHtml = fieldDefs.map(f => {
            const savedField = fields[f.key];
            const enabled = savedField ? savedField.enabled : (f.key !== 'case_data');
            const value = savedField ? savedField.value : f.value;
            if (!value && !savedField) return ''; // skip empty fields with no saved state
            const truncated = value.length > 30 ? value.substring(0, 30) + '...' : value;
            return `<span class="ctx-pill ${enabled ? 'ctx-pill--on' : 'ctx-pill--off'}"
                          data-ctx-key="${f.key}" data-ctx-type="field"
                          title="${this.app.escapeHtml(value)}">
                        <span class="ctx-pill__label">${f.label}</span>
                        ${truncated ? `<span class="ctx-pill__value">${this.app.escapeHtml(truncated)}</span>` : ''}
                    </span>`;
        }).filter(Boolean).join('');

        const customHtml = customPills.map((c, i) => {
            const truncated = c.value.length > 30 ? c.value.substring(0, 30) + '...' : c.value;
            return `<span class="ctx-pill ${c.enabled !== false ? 'ctx-pill--on' : 'ctx-pill--off'} ctx-pill--custom"
                          data-ctx-idx="${i}" data-ctx-type="custom"
                          title="${this.app.escapeHtml(c.value)}">
                        <span class="ctx-pill__label">${this.app.escapeHtml(c.label || 'Note')}</span>
                        ${truncated ? `<span class="ctx-pill__value">${this.app.escapeHtml(truncated)}</span>` : ''}
                        <span class="ctx-pill__remove" data-ctx-remove="${i}">&times;</span>
                    </span>`;
        }).join('');

        const notesVal = this.app.escapeHtml(saved.notes || '');

        return `
            <div class="ctx-bar" id="ctxBar">
                <span class="ctx-bar__label">Context</span>
                <div class="ctx-bar__pills">
                    ${pillsHtml}
                    ${customHtml}
                    <span class="ctx-pill ctx-pill--add" id="btnAddPill" title="Add custom context">+</span>
                </div>
            </div>
            <div class="ctx-popover" id="ctxPopover" style="display:none">
                <div class="ctx-popover__header">
                    <span class="ctx-popover__title" id="ctxPopoverTitle">Edit</span>
                    <button class="ctx-popover__close" id="btnClosePopover">&times;</button>
                </div>
                <div class="ctx-popover__body">
                    <textarea class="ctx-popover__input" id="ctxPopoverInput" rows="3"></textarea>
                </div>
                <div class="ctx-popover__footer">
                    <button class="ctx-popover__save" id="btnPopoverSave">Save</button>
                </div>
            </div>
            <div class="ctx-sidebar" id="ctxSidebar" style="display:none">
                <div class="ctx-sidebar__header">
                    <span class="ctx-sidebar__title">All Context</span>
                    <button class="ctx-sidebar__close" id="btnCloseSidebar">&times;</button>
                </div>
                <div class="ctx-sidebar__body" id="ctxSidebarBody"></div>
                <div class="ctx-sidebar__footer">
                    <textarea class="ctx-sidebar__notes" id="ctxSidebarNotes" rows="2" placeholder="Free-form notes...">${notesVal}</textarea>
                    <button class="ctx-sidebar__save" id="btnSidebarSave">Save All</button>
                </div>
            </div>
        `;
    }

    wireContextPills(inv) {
        const bar = document.getElementById('ctxBar');
        if (!bar) return;

        // Single click on pill: toggle enabled/disabled
        bar.querySelectorAll('.ctx-pill[data-ctx-type]').forEach(pill => {
            pill.addEventListener('click', (e) => {
                // Don't toggle if clicking remove button
                if (e.target.classList.contains('ctx-pill__remove')) return;
                e.stopPropagation();
                pill.classList.toggle('ctx-pill--on');
                pill.classList.toggle('ctx-pill--off');
                this.saveContextFromPills(inv.investigation_id);
            });

            // Double-click: open full sidebar
            pill.addEventListener('dblclick', (e) => {
                e.preventDefault();
                e.stopPropagation();
                this.openContextSidebar(inv);
            });

            // Right-click: open popover for quick edit
            pill.addEventListener('contextmenu', (e) => {
                e.preventDefault();
                e.stopPropagation();
                this.openPillPopover(pill, inv);
            });
        });

        // Remove buttons on custom pills
        bar.querySelectorAll('.ctx-pill__remove').forEach(btn => {
            btn.addEventListener('click', (e) => {
                e.stopPropagation();
                const idx = parseInt(btn.dataset.ctxRemove);
                this.removeCustomPill(inv.investigation_id, idx);
            });
        });

        // Add custom pill button
        const btnAdd = document.getElementById('btnAddPill');
        if (btnAdd) {
            btnAdd.addEventListener('click', () => this.addCustomPill(inv));
        }

        // Popover close/save
        const btnClosePopover = document.getElementById('btnClosePopover');
        if (btnClosePopover) btnClosePopover.addEventListener('click', () => this.closePopover());
        const btnPopoverSave = document.getElementById('btnPopoverSave');
        if (btnPopoverSave) btnPopoverSave.addEventListener('click', () => this.savePopover(inv.investigation_id));

        // Sidebar close/save
        const btnCloseSidebar = document.getElementById('btnCloseSidebar');
        if (btnCloseSidebar) btnCloseSidebar.addEventListener('click', () => this.closeContextSidebar());
        const btnSidebarSave = document.getElementById('btnSidebarSave');
        if (btnSidebarSave) btnSidebarSave.addEventListener('click', () => this.saveContextSidebar(inv.investigation_id));
    }

    openPillPopover(pill, inv) {
        const popover = document.getElementById('ctxPopover');
        if (!popover) return;

        const type = pill.dataset.ctxType;
        const key = pill.dataset.ctxKey;
        const idx = pill.dataset.ctxIdx;

        // Get current value
        const meta = (typeof inv.metadata === 'string' ? JSON.parse(inv.metadata || '{}') : inv.metadata) || {};
        const ctx = meta.context || {};
        let title, value;

        if (type === 'field') {
            const labels = { title: 'Title', severity: 'Severity', systems: 'Systems', description: 'Description', case_data: 'Case Data' };
            title = labels[key] || key;
            const field = (ctx.fields || {})[key];
            value = field ? field.value : (pill.title || '');
        } else {
            const custom = (ctx.custom || [])[parseInt(idx)];
            title = custom ? custom.label : 'Custom';
            value = custom ? custom.value : '';
        }

        const titleEl = document.getElementById('ctxPopoverTitle');
        const inputEl = document.getElementById('ctxPopoverInput');
        if (titleEl) titleEl.textContent = title;
        if (inputEl) inputEl.value = value;

        // Store what we're editing
        popover.dataset.editType = type;
        popover.dataset.editKey = key || '';
        popover.dataset.editIdx = idx || '';

        // Position near the pill
        const rect = pill.getBoundingClientRect();
        const barRect = pill.closest('.ctx-bar').getBoundingClientRect();
        popover.style.display = 'block';
        popover.style.top = (rect.bottom - barRect.top + 6) + 'px';
        popover.style.left = Math.max(0, rect.left - barRect.left) + 'px';

        if (inputEl) inputEl.focus();
    }

    closePopover() {
        const popover = document.getElementById('ctxPopover');
        if (popover) popover.style.display = 'none';
    }

    async savePopover(investigationId) {
        const popover = document.getElementById('ctxPopover');
        const inputEl = document.getElementById('ctxPopoverInput');
        if (!popover || !inputEl) return;

        const type = popover.dataset.editType;
        const key = popover.dataset.editKey;
        const idx = popover.dataset.editIdx;
        const value = inputEl.value;

        const inv = this.investigations.find(i => i.investigation_id === investigationId);
        if (!inv) return;
        const meta = (typeof inv.metadata === 'string' ? JSON.parse(inv.metadata || '{}') : inv.metadata) || {};
        if (!meta.context) meta.context = { fields: {}, custom: [], notes: '' };

        if (type === 'field') {
            if (!meta.context.fields[key]) meta.context.fields[key] = { enabled: true, value: '' };
            meta.context.fields[key].value = value;

            // Also update the actual investigation field
            const fieldMap = { title: 'title', severity: 'severity', description: 'description' };
            const updatePayload = {};
            if (fieldMap[key]) updatePayload[fieldMap[key]] = value;
            if (key === 'systems') updatePayload.affected_systems = value.split(',').map(s => s.trim()).filter(Boolean);

            if (Object.keys(updatePayload).length > 0) {
                updatePayload.metadata = meta;
                await this.app.apiFetch(`/api/investigations/${investigationId}`, {
                    method: 'PUT', body: JSON.stringify(updatePayload),
                });
                Object.assign(inv, updatePayload);
                inv.metadata = meta;
                this.fetchInvestigations(); // refresh list
                this.renderDetail(inv); // re-render
                return;
            }
        } else if (type === 'custom') {
            const i = parseInt(idx);
            if (meta.context.custom[i]) meta.context.custom[i].value = value;
        }

        meta.context = meta.context;
        await this.app.apiFetch(`/api/investigations/${investigationId}`, {
            method: 'PUT', body: JSON.stringify({ metadata: meta }),
        });
        inv.metadata = meta;
        this.renderDetail(inv);
    }

    openContextSidebar(inv) {
        this.closePopover();
        const sidebar = document.getElementById('ctxSidebar');
        const body = document.getElementById('ctxSidebarBody');
        if (!sidebar || !body) return;

        const meta = (typeof inv.metadata === 'string' ? JSON.parse(inv.metadata || '{}') : inv.metadata) || {};
        const ctx = meta.context || {};
        const fields = ctx.fields || {};
        const systemsStr = Array.isArray(inv.affected_systems) ? inv.affected_systems.join(', ') : '';

        const fieldDefs = [
            { key: 'title', label: 'Title', value: inv.title || '' },
            { key: 'severity', label: 'Severity', value: inv.severity || '' },
            { key: 'systems', label: 'Systems', value: systemsStr },
            { key: 'description', label: 'Description', value: inv.description || '' },
            { key: 'case_data', label: 'Case Data', value: '' },
        ];

        const fieldsHtml = fieldDefs.map(f => {
            const saved = fields[f.key];
            const enabled = saved ? saved.enabled : (f.key !== 'case_data');
            const value = saved ? saved.value : f.value;
            return `
                <div class="ctx-sidebar__field">
                    <label class="ctx-sidebar__field-label">
                        <input type="checkbox" class="ctx-sidebar__checkbox" data-sb-key="${f.key}" ${enabled ? 'checked' : ''}>
                        <span>${f.label}</span>
                    </label>
                    <textarea class="ctx-sidebar__input" data-sb-field="${f.key}" rows="${f.key === 'description' || f.key === 'case_data' ? 3 : 1}">${this.app.escapeHtml(value)}</textarea>
                </div>
            `;
        }).join('');

        const customHtml = (ctx.custom || []).map((c, i) => `
            <div class="ctx-sidebar__field ctx-sidebar__field--custom">
                <label class="ctx-sidebar__field-label">
                    <input type="checkbox" class="ctx-sidebar__checkbox" data-sb-custom-toggle="${i}" ${c.enabled !== false ? 'checked' : ''}>
                    <input type="text" class="ctx-sidebar__custom-label" data-sb-custom-label="${i}" value="${this.app.escapeHtml(c.label || '')}">
                    <span class="ctx-sidebar__remove-custom" data-sb-remove="${i}">&times;</span>
                </label>
                <textarea class="ctx-sidebar__input" data-sb-custom="${i}" rows="2">${this.app.escapeHtml(c.value)}</textarea>
            </div>
        `).join('');

        body.innerHTML = fieldsHtml + customHtml;

        const notesEl = document.getElementById('ctxSidebarNotes');
        if (notesEl) notesEl.value = ctx.notes || '';

        sidebar.style.display = 'flex';
    }

    closeContextSidebar() {
        const sidebar = document.getElementById('ctxSidebar');
        if (sidebar) sidebar.style.display = 'none';
    }

    async saveContextSidebar(investigationId) {
        const inv = this.investigations.find(i => i.investigation_id === investigationId);
        if (!inv) return;

        const meta = (typeof inv.metadata === 'string' ? JSON.parse(inv.metadata || '{}') : inv.metadata) || {};
        if (!meta.context) meta.context = { fields: {}, custom: [], notes: '' };

        const updatePayload = { metadata: meta };

        // Read field values from sidebar
        document.querySelectorAll('[data-sb-field]').forEach(el => {
            const key = el.dataset.sbField;
            const toggle = document.querySelector(`[data-sb-key="${key}"]`);
            if (!meta.context.fields[key]) meta.context.fields[key] = {};
            meta.context.fields[key].value = el.value;
            meta.context.fields[key].enabled = toggle ? toggle.checked : true;

            // Sync back to investigation fields
            const fieldMap = { title: 'title', severity: 'severity', description: 'description' };
            if (fieldMap[key]) updatePayload[fieldMap[key]] = el.value;
            if (key === 'systems') updatePayload.affected_systems = el.value.split(',').map(s => s.trim()).filter(Boolean);
        });

        // Read custom pills from sidebar
        const customs = [];
        document.querySelectorAll('[data-sb-custom]').forEach(el => {
            const i = parseInt(el.dataset.sbCustom);
            const labelEl = document.querySelector(`[data-sb-custom-label="${i}"]`);
            const toggleEl = document.querySelector(`[data-sb-custom-toggle="${i}"]`);
            customs.push({
                label: labelEl ? labelEl.value : '',
                value: el.value,
                enabled: toggleEl ? toggleEl.checked : true,
            });
        });
        meta.context.custom = customs;

        // Notes
        const notesEl = document.getElementById('ctxSidebarNotes');
        meta.context.notes = notesEl ? notesEl.value : '';

        try {
            await this.app.apiFetch(`/api/investigations/${investigationId}`, {
                method: 'PUT', body: JSON.stringify(updatePayload),
            });
            inv.metadata = meta;
            Object.keys(updatePayload).forEach(k => { if (k !== 'metadata') inv[k] = updatePayload[k]; });
            this.closeContextSidebar();
            this.fetchInvestigations();
            this.renderDetail(inv);
        } catch (e) {
            console.error('Failed to save context:', e);
        }
    }

    async addCustomPill(inv) {
        const label = prompt('Label for custom context:');
        if (!label) return;
        const value = prompt('Value:');
        if (value === null) return;

        const meta = (typeof inv.metadata === 'string' ? JSON.parse(inv.metadata || '{}') : inv.metadata) || {};
        if (!meta.context) meta.context = { fields: {}, custom: [], notes: '' };
        if (!meta.context.custom) meta.context.custom = [];
        meta.context.custom.push({ label, value, enabled: true });

        await this.app.apiFetch(`/api/investigations/${inv.investigation_id}`, {
            method: 'PUT', body: JSON.stringify({ metadata: meta }),
        });
        inv.metadata = meta;
        this.renderDetail(inv);
    }

    async removeCustomPill(investigationId, idx) {
        const inv = this.investigations.find(i => i.investigation_id === investigationId);
        if (!inv) return;
        const meta = (typeof inv.metadata === 'string' ? JSON.parse(inv.metadata || '{}') : inv.metadata) || {};
        if (!meta.context?.custom) return;
        meta.context.custom.splice(idx, 1);

        await this.app.apiFetch(`/api/investigations/${inv.investigation_id}`, {
            method: 'PUT', body: JSON.stringify({ metadata: meta }),
        });
        inv.metadata = meta;
        this.renderDetail(inv);
    }

    saveContextFromPills(investigationId) {
        const inv = this.investigations.find(i => i.investigation_id === investigationId);
        if (!inv) return;

        const meta = (typeof inv.metadata === 'string' ? JSON.parse(inv.metadata || '{}') : inv.metadata) || {};
        if (!meta.context) meta.context = { fields: {}, custom: [], notes: '' };

        // Read pill states from DOM
        document.querySelectorAll('.ctx-pill[data-ctx-type="field"]').forEach(pill => {
            const key = pill.dataset.ctxKey;
            const enabled = pill.classList.contains('ctx-pill--on');
            if (!meta.context.fields[key]) {
                meta.context.fields[key] = { enabled, value: pill.title || '' };
            } else {
                meta.context.fields[key].enabled = enabled;
            }
        });

        document.querySelectorAll('.ctx-pill[data-ctx-type="custom"]').forEach(pill => {
            const idx = parseInt(pill.dataset.ctxIdx);
            if (meta.context.custom[idx]) {
                meta.context.custom[idx].enabled = pill.classList.contains('ctx-pill--on');
            }
        });

        // Fire and forget save
        this.app.apiFetch(`/api/investigations/${investigationId}`, {
            method: 'PUT', body: JSON.stringify({ metadata: meta }),
        }).then(() => { inv.metadata = meta; }).catch(e => console.error('Failed to save context:', e));
    }

    getContextFromPills() {
        const fields = {};
        document.querySelectorAll('.ctx-pill[data-ctx-type="field"]').forEach(pill => {
            const key = pill.dataset.ctxKey;
            fields[key] = {
                enabled: pill.classList.contains('ctx-pill--on'),
                value: pill.title || '',
            };
        });
        const custom = [];
        document.querySelectorAll('.ctx-pill[data-ctx-type="custom"]').forEach(pill => {
            custom.push({
                label: pill.querySelector('.ctx-pill__label')?.textContent || '',
                value: pill.title || '',
                enabled: pill.classList.contains('ctx-pill--on'),
            });
        });
        return { fields, custom, notes: '' };
    }

    buildContextPrompt(context) {
        if (!context || !context.fields) return '';
        const parts = [];
        const labels = { title: 'Title', severity: 'Severity', systems: 'Affected Systems', description: 'Description', case_data: 'Case Data' };
        for (const [key, field] of Object.entries(context.fields)) {
            if (field.enabled && field.value && field.value.trim()) {
                parts.push(`${labels[key] || key}: ${field.value.trim()}`);
            }
        }
        if (context.custom) {
            for (const c of context.custom) {
                if (c.enabled !== false && c.value && c.value.trim()) {
                    parts.push(`${c.label || 'Note'}: ${c.value.trim()}`);
                }
            }
        }
        if (context.notes && context.notes.trim()) {
            parts.push(`Notes: ${context.notes.trim()}`);
        }
        if (parts.length === 0) return '';
        return `## Investigation Context\n\n${parts.join('\n')}\n\n`;
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
