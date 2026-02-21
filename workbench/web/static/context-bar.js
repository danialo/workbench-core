/**
 * Context Bar — workspace-scoped context pills injected into every LLM turn.
 *
 * Renders in the bar between workspace tabs and window tabs.
 * Each pill is an object with typed fields that can be individually toggled.
 * Pill types: case (fetched case data), jira (fetched Jira ticket),
 * custom (key-value pair), timeline (start_date + end_date).
 */

class ContextBar {
    constructor(app) {
        this.app = app;
        this.pills = [];
        this._menuOpen = false;
        this._popoverOpen = false;
        this._fieldPopover = null;
    }

    async init() {
        this._bindEvents();
        await this.loadPills();
    }

    // ------------------------------------------------------------------
    // Data
    // ------------------------------------------------------------------

    async loadPills() {
        const wsId = this.app.activeWorkspaceId || 'global';
        try {
            const data = await this.app.apiFetch(`/api/workspaces/${wsId}/context`);
            this.pills = data.pills || [];
        } catch (e) {
            console.warn('Failed to load context pills:', e);
            this.pills = [];
        }
        this.render();
    }

    // ------------------------------------------------------------------
    // Render
    // ------------------------------------------------------------------

    render() {
        const container = document.getElementById('contextBarPills');
        if (!container) return;

        if (this.pills.length === 0) {
            container.innerHTML = '<span class="context-bar__empty">No context pills</span>';
            return;
        }

        container.innerHTML = '';
        for (const pill of this.pills) {
            container.appendChild(this._renderPill(pill));
        }
    }

    _renderPill(pill) {
        const el = document.createElement('span');
        const stateClass = pill.enabled ? 'ctx-pill--on' : 'ctx-pill--off';
        el.className = `ctx-pill ${stateClass}`;
        el.dataset.pillId = pill.pill_id;

        // Type indicator
        const typeEl = document.createElement('span');
        typeEl.className = 'ctx-pill__type';
        const typeIcons = { case: '📋', jira: '🎫', timeline: '⏱', custom: '●' };
        typeEl.textContent = typeIcons[pill.pill_type] || '●';
        el.appendChild(typeEl);

        // Label
        const labelEl = document.createElement('span');
        labelEl.className = 'ctx-pill__label';
        labelEl.textContent = pill.label;
        el.appendChild(labelEl);

        // Value preview
        const valueEl = document.createElement('span');
        valueEl.className = 'ctx-pill__value';
        valueEl.textContent = this._pillValuePreview(pill);
        el.appendChild(valueEl);

        // Remove button
        const removeEl = document.createElement('span');
        removeEl.className = 'ctx-pill__remove';
        removeEl.textContent = '×';
        removeEl.addEventListener('click', (e) => {
            e.stopPropagation();
            this.deletePill(pill.pill_id);
        });
        el.appendChild(removeEl);

        // Click → toggle enabled
        el.addEventListener('click', (e) => {
            if (e.target.classList.contains('ctx-pill__remove')) return;
            this.togglePill(pill.pill_id, !pill.enabled);
        });

        // Right-click → field-level popover
        el.addEventListener('contextmenu', (e) => {
            e.preventDefault();
            this._showFieldPopover(pill, e.clientX, e.clientY);
        });

        return el;
    }

    _pillValuePreview(pill) {
        const fields = pill.fields || {};
        if (pill.pill_type === 'case' || pill.pill_type === 'jira') {
            const title = fields.title;
            const val = (title && typeof title === 'object') ? title.value : '';
            return val ? `: ${this._truncate(val, 40)}` : '';
        }
        if (pill.pill_type === 'custom') {
            const v = fields.value;
            const val = (v && typeof v === 'object') ? v.value : '';
            return val ? `: ${this._truncate(val, 40)}` : '';
        }
        if (pill.pill_type === 'timeline') {
            const parts = [];
            const s = fields.start_date;
            const e = fields.end_date;
            if (s && s.value) parts.push(this._shortDate(s.value));
            if (e && e.value) parts.push(this._shortDate(e.value));
            return parts.length ? `: ${parts.join(' – ')}` : '';
        }
        return '';
    }

    _shortDate(iso) {
        try {
            const d = new Date(iso);
            return d.toLocaleDateString('en-US', { month: 'short', day: 'numeric' });
        } catch {
            return iso;
        }
    }

    _truncate(str, max) {
        return str.length > max ? str.slice(0, max) + '…' : str;
    }

    // ------------------------------------------------------------------
    // CRUD
    // ------------------------------------------------------------------

    async togglePill(pillId, enabled) {
        // Optimistic UI
        const pill = this.pills.find(p => p.pill_id === pillId);
        if (pill) {
            pill.enabled = enabled;
            this.render();
        }

        const wsId = this.app.activeWorkspaceId || 'global';
        try {
            await this.app.apiFetch(`/api/workspaces/${wsId}/context/${pillId}`, {
                method: 'PUT',
                body: JSON.stringify({ enabled }),
            });
        } catch (e) {
            console.error('Failed to toggle pill:', e);
            await this.loadPills(); // revert on failure
        }
    }

    async toggleField(pillId, fieldName, enabled) {
        const pill = this.pills.find(p => p.pill_id === pillId);
        if (!pill) return;

        const fields = { ...pill.fields };
        if (fields[fieldName] && typeof fields[fieldName] === 'object') {
            fields[fieldName] = { ...fields[fieldName], enabled };
        }
        pill.fields = fields;
        this.render();

        const wsId = this.app.activeWorkspaceId || 'global';
        try {
            await this.app.apiFetch(`/api/workspaces/${wsId}/context/${pillId}`, {
                method: 'PUT',
                body: JSON.stringify({ fields }),
            });
        } catch (e) {
            console.error('Failed to toggle field:', e);
            await this.loadPills();
        }
    }

    async createPill(type, label, fields) {
        const wsId = this.app.activeWorkspaceId || 'global';
        try {
            await this.app.apiFetch(`/api/workspaces/${wsId}/context`, {
                method: 'POST',
                body: JSON.stringify({ pill_type: type, label, fields }),
            });
            await this.loadPills();
        } catch (e) {
            console.error('Failed to create pill:', e);
        }
    }

    async deletePill(pillId) {
        const wsId = this.app.activeWorkspaceId || 'global';
        // Optimistic removal
        this.pills = this.pills.filter(p => p.pill_id !== pillId);
        this.render();

        try {
            await this.app.apiFetch(`/api/workspaces/${wsId}/context/${pillId}`, {
                method: 'DELETE',
            });
        } catch (e) {
            console.error('Failed to delete pill:', e);
            await this.loadPills();
        }
    }

    // ------------------------------------------------------------------
    // Events
    // ------------------------------------------------------------------

    _bindEvents() {
        const addBtn = document.getElementById('btnAddContextPill');
        if (addBtn) {
            addBtn.addEventListener('click', (e) => {
                e.stopPropagation();
                this._toggleMenu();
            });
        }

        // Close menus/popovers on outside click
        document.addEventListener('click', (e) => {
            if (this._menuOpen && !e.target.closest('.context-bar__menu') && !e.target.closest('.context-bar__add-btn')) {
                this._hideMenu();
            }
            if (this._popoverOpen && !e.target.closest('.context-bar__popover')) {
                this._hidePopover();
            }
            if (this._fieldPopover && !e.target.closest('.context-bar__field-popover')) {
                this._hideFieldPopover();
            }
        });

        // Escape key closes everything
        document.addEventListener('keydown', (e) => {
            if (e.key === 'Escape') {
                this._hideMenu();
                this._hidePopover();
                this._hideFieldPopover();
            }
        });
    }

    // ------------------------------------------------------------------
    // Type selection menu
    // ------------------------------------------------------------------

    _toggleMenu() {
        if (this._menuOpen) {
            this._hideMenu();
        } else {
            this._showMenu();
        }
    }

    _showMenu() {
        this._hidePopover();
        this._hideFieldPopover();

        // Remove existing menu
        const existing = document.querySelector('.context-bar__menu');
        if (existing) existing.remove();

        const menu = document.createElement('div');
        menu.className = 'context-bar__menu';

        const types = [
            { type: 'case', icon: '📋', label: 'Case' },
            { type: 'jira', icon: '🎫', label: 'Jira' },
            { type: 'custom', icon: '●', label: 'Custom' },
            { type: 'timeline', icon: '⏱', label: 'Timeline' },
        ];

        for (const t of types) {
            const item = document.createElement('button');
            item.className = 'context-bar__menu-item';
            item.innerHTML = `<span class="context-bar__menu-item__icon">${t.icon}</span>${t.label}`;
            item.addEventListener('click', (e) => {
                e.stopPropagation();
                this._hideMenu();
                this._showCreateForm(t.type);
            });
            menu.appendChild(item);
        }

        const bar = document.getElementById('contextBar');
        if (bar) bar.appendChild(menu);
        this._menuOpen = true;
    }

    _hideMenu() {
        const menu = document.querySelector('.context-bar__menu');
        if (menu) menu.remove();
        this._menuOpen = false;
    }

    // ------------------------------------------------------------------
    // Creation form (popover)
    // ------------------------------------------------------------------

    _showCreateForm(type) {
        this._hidePopover();

        const popover = document.createElement('div');
        popover.className = 'context-bar__popover';

        if (type === 'case' || type === 'jira') {
            const sourceLabel = type === 'jira' ? 'Jira Ticket ID' : 'Case / Ticket ID';
            const placeholder = type === 'jira' ? 'e.g. PROJ-123' : 'e.g. INC-12345';
            popover.innerHTML = `
                <div class="context-bar__popover-title">Add ${type === 'jira' ? 'Jira' : 'Case'}</div>
                <div class="context-bar__popover-field">
                    <label class="context-bar__popover-label">${sourceLabel}</label>
                    <div class="context-bar__popover-fetch-row">
                        <input class="context-bar__popover-input" id="ctxNewCaseId" type="text" placeholder="${placeholder}" />
                        <button class="context-bar__popover-fetch-btn" id="ctxFetchBtn">Fetch</button>
                    </div>
                </div>
                <div class="context-bar__popover-fetch-status" id="ctxFetchStatus"></div>
                <div class="context-bar__popover-results" id="ctxFetchResults"></div>
                <div class="context-bar__popover-actions">
                    <button class="context-bar__popover-btn context-bar__popover-btn--cancel" id="ctxCreateCancel">Cancel</button>
                </div>
            `;
        } else if (type === 'custom') {
            popover.innerHTML = `
                <div class="context-bar__popover-title">New Custom Pill</div>
                <div class="context-bar__popover-field">
                    <label class="context-bar__popover-label">Label</label>
                    <input class="context-bar__popover-input" id="ctxNewLabel" type="text" placeholder="e.g. Environment" />
                </div>
                <div class="context-bar__popover-field">
                    <label class="context-bar__popover-label">Value</label>
                    <input class="context-bar__popover-input" id="ctxNewValue" type="text" placeholder="e.g. production" />
                </div>
                <div class="context-bar__popover-actions">
                    <button class="context-bar__popover-btn context-bar__popover-btn--cancel" id="ctxCreateCancel">Cancel</button>
                    <button class="context-bar__popover-btn context-bar__popover-btn--save" id="ctxCreateSave">Add</button>
                </div>
            `;
        } else if (type === 'timeline') {
            popover.innerHTML = `
                <div class="context-bar__popover-title">New Timeline Pill</div>
                <div class="context-bar__popover-field">
                    <label class="context-bar__popover-label">Label</label>
                    <input class="context-bar__popover-input" id="ctxNewLabel" type="text" placeholder="e.g. Incident Window" />
                </div>
                <div class="context-bar__popover-field">
                    <label class="context-bar__popover-label">Start Date</label>
                    <input class="context-bar__popover-input" id="ctxNewStart" type="datetime-local" />
                </div>
                <div class="context-bar__popover-field">
                    <label class="context-bar__popover-label">End Date</label>
                    <input class="context-bar__popover-input" id="ctxNewEnd" type="datetime-local" />
                </div>
                <div class="context-bar__popover-actions">
                    <button class="context-bar__popover-btn context-bar__popover-btn--cancel" id="ctxCreateCancel">Cancel</button>
                    <button class="context-bar__popover-btn context-bar__popover-btn--save" id="ctxCreateSave">Add</button>
                </div>
            `;
        }

        const bar = document.getElementById('contextBar');
        if (bar) bar.appendChild(popover);
        this._popoverOpen = true;

        // Focus first input
        const firstInput = popover.querySelector('input');
        if (firstInput) setTimeout(() => firstInput.focus(), 50);

        // Bind actions
        const cancelBtn = popover.querySelector('#ctxCreateCancel');
        if (cancelBtn) cancelBtn.addEventListener('click', () => this._hidePopover());

        const saveBtn = popover.querySelector('#ctxCreateSave');
        if (saveBtn) {
            saveBtn.addEventListener('click', () => this._submitCreateForm(type));
        }

        // Case/Jira: wire fetch button
        const fetchBtn = popover.querySelector('#ctxFetchBtn');
        if (fetchBtn) {
            fetchBtn.addEventListener('click', () => this._fetchCaseForPill(type));
            // Enter on case ID input triggers fetch
            const caseInput = popover.querySelector('#ctxNewCaseId');
            if (caseInput) {
                caseInput.addEventListener('keydown', (e) => {
                    if (e.key === 'Enter') {
                        e.preventDefault();
                        this._fetchCaseForPill(type);
                    }
                });
            }
        }

        // Enter key submits (for non-case types)
        if (type !== 'case' && type !== 'jira') {
            popover.addEventListener('keydown', (e) => {
                if (e.key === 'Enter') {
                    e.preventDefault();
                    this._submitCreateForm(type);
                }
            });
        }
    }

    async _fetchCaseForPill(type) {
        const caseId = document.getElementById('ctxNewCaseId')?.value.trim();
        if (!caseId) return;

        const btn = document.getElementById('ctxFetchBtn');
        const statusEl = document.getElementById('ctxFetchStatus');
        const resultsEl = document.getElementById('ctxFetchResults');

        if (btn) { btn.textContent = 'Fetching...'; btn.disabled = true; }
        if (statusEl) statusEl.innerHTML = '<span class="context-bar__fetch-loading">Querying sources...</span>';
        if (resultsEl) resultsEl.innerHTML = '';

        try {
            const data = await this.app.apiFetch('/api/investigations/fetch-case', {
                method: 'POST',
                body: JSON.stringify({ case_id: caseId, source: type === 'jira' ? 'jira' : undefined }),
            });

            // Check if sources returned real data or just an unfetched marker
            if (data.unfetched) {
                if (statusEl) statusEl.innerHTML = `<span class="context-bar__fetch-error">${data.message || 'No sources configured'}</span>`;
                if (resultsEl) {
                    resultsEl.innerHTML = '';
                    resultsEl.appendChild(this._renderUnfetchedCard(type, caseId, data.message));
                }
            } else {
                const source = data.source || data._source || type;
                if (statusEl) statusEl.innerHTML = `<span class="context-bar__fetch-success">Fetched from ${this._truncate(source, 20)}</span>`;

                // Render result as clickable card — future: loop over multiple results
                if (resultsEl) {
                    const results = Array.isArray(data.results) ? data.results : [data];
                    resultsEl.innerHTML = '';
                    for (const result of results) {
                        resultsEl.appendChild(this._renderResultCard(type, caseId, result));
                    }
                }
            }
        } catch (e) {
            if (statusEl) statusEl.innerHTML = `<span class="context-bar__fetch-error">Failed: ${e.message}</span>`;

            // Show unfetched fallback card
            if (resultsEl) {
                resultsEl.innerHTML = '';
                resultsEl.appendChild(this._renderUnfetchedCard(type, caseId, e.message));
            }
        } finally {
            if (btn) { btn.textContent = 'Fetch'; btn.disabled = false; }
        }
    }

    _renderResultCard(type, caseId, data) {
        const card = document.createElement('div');
        card.className = 'context-bar__result-card';

        const title = data.title || caseId;
        const severity = data.severity ? `<span class="context-bar__result-severity">${data.severity}</span>` : '';
        const desc = data.description ? `<div class="context-bar__result-desc">${this._truncate(data.description, 120)}</div>` : '';
        const systems = (data.affected_systems || []).join(', ');
        const systemsHtml = systems ? `<div class="context-bar__result-systems">${this._truncate(systems, 60)}</div>` : '';

        card.innerHTML = `
            <div class="context-bar__result-header">
                <span class="context-bar__result-title">${this._truncate(title, 60)}</span>
                ${severity}
            </div>
            ${desc}
            ${systemsHtml}
            <button class="context-bar__result-add-btn">+ Add as pill</button>
        `;

        card.querySelector('.context-bar__result-add-btn').addEventListener('click', () => {
            const label = data.title ? `${caseId}: ${data.title}` : caseId;
            const fields = {
                case_id: { value: caseId, enabled: true },
                title: { value: data.title || '', enabled: true },
                severity: { value: data.severity || '', enabled: !!data.severity },
                systems: { value: (data.affected_systems || []).join(', '), enabled: !!(data.affected_systems && data.affected_systems.length) },
                description: { value: data.description || '', enabled: !!data.description },
                source: { value: data.source || data._source || type, enabled: false },
            };
            this._hidePopover();
            this.createPill(type, label, fields);
        });

        return card;
    }

    _renderUnfetchedCard(type, caseId, message) {
        const card = document.createElement('div');
        card.className = 'context-bar__result-card context-bar__result-card--unfetched';

        card.innerHTML = `
            <div class="context-bar__result-header">
                <span class="context-bar__result-warn">!</span>
                <span class="context-bar__result-title">${this._truncate(caseId, 60)}</span>
            </div>
            <div class="context-bar__result-desc">${this._truncate(message || 'Could not fetch case data', 120)}</div>
            <button class="context-bar__result-add-btn context-bar__result-add-btn--unfetched">+ Add as text entry</button>
        `;

        card.querySelector('.context-bar__result-add-btn').addEventListener('click', () => {
            const fields = {
                case_id: { value: caseId, enabled: true },
                title: { value: '', enabled: false },
                severity: { value: '', enabled: false },
                systems: { value: '', enabled: false },
                description: { value: '', enabled: false },
                source: { value: 'unfetched', enabled: false },
            };
            this._hidePopover();
            this.createPill(type, caseId, fields);
        });

        return card;
    }

    _submitCreateForm(type) {
        const label = document.getElementById('ctxNewLabel')?.value?.trim();
        if (!label) return;

        let fields = {};
        if (type === 'custom') {
            const value = document.getElementById('ctxNewValue')?.value?.trim() || '';
            fields = { value: { value, enabled: true } };
        } else if (type === 'timeline') {
            const start = document.getElementById('ctxNewStart')?.value || '';
            const end = document.getElementById('ctxNewEnd')?.value || '';
            fields = {
                start_date: { value: start ? new Date(start).toISOString() : '', enabled: true },
                end_date: { value: end ? new Date(end).toISOString() : '', enabled: true },
            };
        }

        this._hidePopover();
        this.createPill(type, label, fields);
    }

    _hidePopover() {
        const popover = document.querySelector('.context-bar__popover');
        if (popover) popover.remove();
        this._popoverOpen = false;
    }

    // ------------------------------------------------------------------
    // Field-level popover (right-click on pill)
    // ------------------------------------------------------------------

    _showFieldPopover(pill, x, y) {
        this._hideFieldPopover();
        this._hideMenu();
        this._hidePopover();

        const pop = document.createElement('div');
        pop.className = 'context-bar__field-popover';
        pop.style.left = `${x}px`;
        pop.style.top = `${y}px`;

        const title = document.createElement('div');
        title.className = 'context-bar__field-popover-title';
        title.textContent = pill.label;
        pop.appendChild(title);

        const fields = pill.fields || {};
        for (const [key, field] of Object.entries(fields)) {
            if (typeof field !== 'object') continue;

            const row = document.createElement('div');
            row.className = 'context-bar__field-row';

            const cb = document.createElement('input');
            cb.type = 'checkbox';
            cb.checked = field.enabled !== false;
            cb.addEventListener('change', () => {
                this.toggleField(pill.pill_id, key, cb.checked);
            });

            const lbl = document.createElement('label');
            lbl.textContent = this._fieldDisplayName(key);
            lbl.addEventListener('click', () => { cb.checked = !cb.checked; cb.dispatchEvent(new Event('change')); });

            const val = document.createElement('span');
            val.className = 'context-bar__field-value';
            val.textContent = this._truncate(field.value || '', 30);

            row.appendChild(cb);
            row.appendChild(lbl);
            row.appendChild(val);
            pop.appendChild(row);
        }

        document.body.appendChild(pop);
        this._fieldPopover = pop;

        // Keep within viewport
        const rect = pop.getBoundingClientRect();
        if (rect.right > window.innerWidth) {
            pop.style.left = `${window.innerWidth - rect.width - 8}px`;
        }
        if (rect.bottom > window.innerHeight) {
            pop.style.top = `${window.innerHeight - rect.height - 8}px`;
        }
    }

    _hideFieldPopover() {
        if (this._fieldPopover) {
            this._fieldPopover.remove();
            this._fieldPopover = null;
        }
    }

    _fieldDisplayName(key) {
        const names = {
            value: 'Value',
            start_date: 'Start Date',
            end_date: 'End Date',
            case_id: 'Case ID',
            title: 'Title',
            severity: 'Severity',
            systems: 'Systems',
            description: 'Description',
            source: 'Source',
        };
        return names[key] || key.replace(/_/g, ' ').replace(/\b\w/g, c => c.toUpperCase());
    }
}
