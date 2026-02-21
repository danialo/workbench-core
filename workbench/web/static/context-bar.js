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
        this._contextMenu = null;
        this._editPopover = null;
        this._dragState = null;
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
            this._updateBudget();
            return;
        }

        container.innerHTML = '';
        for (const pill of this.pills) {
            container.appendChild(this._renderPill(pill));
        }
        this._updateBudget();
    }

    _renderPill(pill) {
        const el = document.createElement('span');
        const stateClass = pill.enabled ? 'ctx-pill--on' : 'ctx-pill--off';
        el.className = `ctx-pill ${stateClass}`;
        el.dataset.pillId = pill.pill_id;
        el.draggable = true;

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

        // Double-click → edit popover
        el.addEventListener('dblclick', (e) => {
            e.preventDefault();
            e.stopPropagation();
            this._showEditPopover(pill, e.clientX, e.clientY);
        });

        // Right-click → context menu
        el.addEventListener('contextmenu', (e) => {
            e.preventDefault();
            this._showContextMenu(pill, e.clientX, e.clientY);
        });

        // Drag events
        el.addEventListener('dragstart', (e) => this._onDragStart(e, pill));
        el.addEventListener('dragover', (e) => this._onDragOver(e, pill));
        el.addEventListener('dragenter', (e) => this._onDragEnter(e));
        el.addEventListener('dragleave', (e) => this._onDragLeave(e));
        el.addEventListener('drop', (e) => this._onDrop(e, pill));
        el.addEventListener('dragend', () => this._onDragEnd());

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

    async updatePill(pillId, updates) {
        const wsId = this.app.activeWorkspaceId || 'global';
        try {
            await this.app.apiFetch(`/api/workspaces/${wsId}/context/${pillId}`, {
                method: 'PUT',
                body: JSON.stringify(updates),
            });
            await this.loadPills();
        } catch (e) {
            console.error('Failed to update pill:', e);
        }
    }

    async createPill(type, label, fields) {
        const wsId = this.app.activeWorkspaceId || 'global';
        try {
            const result = await this.app.apiFetch(`/api/workspaces/${wsId}/context`, {
                method: 'POST',
                body: JSON.stringify({ pill_type: type, label, fields }),
            });
            await this.loadPills();
            return result;
        } catch (e) {
            console.error('Failed to create pill:', e);
            return null;
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

    async _reorderPill(pillId, newOrder) {
        const wsId = this.app.activeWorkspaceId || 'global';
        try {
            await this.app.apiFetch(`/api/workspaces/${wsId}/context/${pillId}`, {
                method: 'PUT',
                body: JSON.stringify({ sort_order: newOrder }),
            });
        } catch (e) {
            console.error('Failed to reorder pill:', e);
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
            if (this._contextMenu && !e.target.closest('.context-bar__ctx-menu')) {
                this._hideContextMenu();
            }
            if (this._editPopover && !e.target.closest('.context-bar__edit-popover')) {
                this._hideEditPopover();
            }
        });

        // Escape key closes everything
        document.addEventListener('keydown', (e) => {
            if (e.key === 'Escape') {
                this._hideMenu();
                this._hidePopover();
                this._hideFieldPopover();
                this._hideContextMenu();
                this._hideEditPopover();
            }
        });
    }

    // ------------------------------------------------------------------
    // Context menu (right-click)
    // ------------------------------------------------------------------

    _showContextMenu(pill, x, y) {
        this._hideContextMenu();
        this._hideFieldPopover();
        this._hideEditPopover();
        this._hideMenu();
        this._hidePopover();

        const menu = document.createElement('div');
        menu.className = 'context-bar__ctx-menu';
        menu.style.left = `${x}px`;
        menu.style.top = `${y}px`;

        const idx = this.pills.findIndex(p => p.pill_id === pill.pill_id);

        const items = [
            { label: 'Edit', icon: '✏️', action: () => this._showEditPopover(pill, x, y) },
            { label: pill.enabled ? 'Disable' : 'Enable', icon: pill.enabled ? '⬜' : '✅', action: () => this.togglePill(pill.pill_id, !pill.enabled) },
            { label: 'Fields…', icon: '🔧', action: () => this._showFieldPopover(pill, x, y) },
            null, // separator
            { label: 'Move Left', icon: '←', action: () => this._movePill(idx, -1), disabled: idx <= 0 },
            { label: 'Move Right', icon: '→', action: () => this._movePill(idx, 1), disabled: idx >= this.pills.length - 1 },
            null,
            { label: 'Copy Value', icon: '📋', action: () => this._copyPillValue(pill) },
            { label: 'Duplicate', icon: '⧉', action: () => this._duplicatePill(pill) },
            null,
            { label: 'Remove', icon: '🗑', action: () => this.deletePill(pill.pill_id), danger: true },
        ];

        for (const item of items) {
            if (item === null) {
                const sep = document.createElement('div');
                sep.className = 'context-bar__ctx-menu-sep';
                menu.appendChild(sep);
                continue;
            }
            const btn = document.createElement('button');
            btn.className = 'context-bar__ctx-menu-item';
            if (item.danger) btn.classList.add('context-bar__ctx-menu-item--danger');
            if (item.disabled) btn.classList.add('context-bar__ctx-menu-item--disabled');
            btn.innerHTML = `<span class="context-bar__ctx-menu-icon">${item.icon}</span>${item.label}`;
            if (!item.disabled) {
                btn.addEventListener('click', (e) => {
                    e.stopPropagation();
                    this._hideContextMenu();
                    item.action();
                });
            }
            menu.appendChild(btn);
        }

        document.body.appendChild(menu);
        this._contextMenu = menu;

        // Keep within viewport
        requestAnimationFrame(() => {
            const rect = menu.getBoundingClientRect();
            if (rect.right > window.innerWidth) {
                menu.style.left = `${window.innerWidth - rect.width - 8}px`;
            }
            if (rect.bottom > window.innerHeight) {
                menu.style.top = `${window.innerHeight - rect.height - 8}px`;
            }
        });
    }

    _hideContextMenu() {
        if (this._contextMenu) {
            this._contextMenu.remove();
            this._contextMenu = null;
        }
    }

    async _movePill(fromIdx, direction) {
        const toIdx = fromIdx + direction;
        if (toIdx < 0 || toIdx >= this.pills.length) return;

        // Swap in local array
        const temp = this.pills[fromIdx];
        this.pills[fromIdx] = this.pills[toIdx];
        this.pills[toIdx] = temp;

        // Assign sequential sort_order and update both
        this.pills[fromIdx].sort_order = fromIdx;
        this.pills[toIdx].sort_order = toIdx;
        this.render();

        await Promise.all([
            this._reorderPill(this.pills[fromIdx].pill_id, fromIdx),
            this._reorderPill(this.pills[toIdx].pill_id, toIdx),
        ]);
    }

    _copyPillValue(pill) {
        let text = pill.label;
        const fields = pill.fields || {};
        const parts = [];
        for (const [key, field] of Object.entries(fields)) {
            if (typeof field === 'object' && field.value && field.enabled !== false) {
                parts.push(`${this._fieldDisplayName(key)}: ${field.value}`);
            }
        }
        if (parts.length) text += '\n' + parts.join('\n');

        navigator.clipboard.writeText(text).catch(() => {
            // Fallback
            const ta = document.createElement('textarea');
            ta.value = text;
            document.body.appendChild(ta);
            ta.select();
            document.execCommand('copy');
            ta.remove();
        });
    }

    async _duplicatePill(pill) {
        const newFields = JSON.parse(JSON.stringify(pill.fields || {}));
        await this.createPill(pill.pill_type, pill.label + ' (copy)', newFields);
    }

    // ------------------------------------------------------------------
    // Edit popover (double-click)
    // ------------------------------------------------------------------

    _showEditPopover(pill, x, y) {
        this._hideEditPopover();
        this._hideContextMenu();
        this._hideFieldPopover();

        const pop = document.createElement('div');
        pop.className = 'context-bar__edit-popover';
        pop.style.left = `${x}px`;
        pop.style.top = `${y}px`;

        const title = document.createElement('div');
        title.className = 'context-bar__edit-popover-title';
        title.textContent = `Edit ${pill.pill_type === 'custom' ? 'Custom' : pill.pill_type === 'timeline' ? 'Timeline' : pill.pill_type.charAt(0).toUpperCase() + pill.pill_type.slice(1)} Pill`;
        pop.appendChild(title);

        // Label field (always)
        const labelGroup = this._createEditField('Label', pill.label, 'edit-pill-label');
        pop.appendChild(labelGroup);

        // Type-specific fields
        const fields = pill.fields || {};

        if (pill.pill_type === 'custom') {
            const valField = fields.value || {};
            const valGroup = this._createEditField('Value', valField.value || '', 'edit-pill-value');
            pop.appendChild(valGroup);
        } else if (pill.pill_type === 'timeline') {
            const startField = fields.start_date || {};
            const endField = fields.end_date || {};
            const startGroup = this._createEditField('Start Date', this._isoToLocal(startField.value || ''), 'edit-pill-start', 'datetime-local');
            const endGroup = this._createEditField('End Date', this._isoToLocal(endField.value || ''), 'edit-pill-end', 'datetime-local');
            pop.appendChild(startGroup);
            pop.appendChild(endGroup);
        } else if (pill.pill_type === 'case' || pill.pill_type === 'jira') {
            for (const [key, field] of Object.entries(fields)) {
                if (typeof field !== 'object' || key === 'source') continue;
                const group = this._createEditField(
                    this._fieldDisplayName(key),
                    field.value || '',
                    `edit-pill-${key}`
                );
                pop.appendChild(group);
            }
        }

        // Actions
        const actions = document.createElement('div');
        actions.className = 'context-bar__edit-popover-actions';
        actions.innerHTML = `
            <button class="context-bar__popover-btn context-bar__popover-btn--cancel" id="editPillCancel">Cancel</button>
            <button class="context-bar__popover-btn context-bar__popover-btn--save" id="editPillSave">Save</button>
        `;
        pop.appendChild(actions);

        document.body.appendChild(pop);
        this._editPopover = pop;

        // Focus first editable input
        const firstInput = pop.querySelector('input');
        if (firstInput) setTimeout(() => firstInput.focus(), 50);

        // Keep within viewport
        requestAnimationFrame(() => {
            const rect = pop.getBoundingClientRect();
            if (rect.right > window.innerWidth) pop.style.left = `${window.innerWidth - rect.width - 8}px`;
            if (rect.bottom > window.innerHeight) pop.style.top = `${window.innerHeight - rect.height - 8}px`;
        });

        // Bind actions
        pop.querySelector('#editPillCancel').addEventListener('click', () => this._hideEditPopover());
        pop.querySelector('#editPillSave').addEventListener('click', () => this._submitEdit(pill));
        pop.addEventListener('keydown', (e) => {
            if (e.key === 'Enter' && !e.shiftKey) {
                e.preventDefault();
                this._submitEdit(pill);
            }
        });
    }

    _createEditField(label, value, id, type = 'text') {
        const group = document.createElement('div');
        group.className = 'context-bar__edit-field';
        group.innerHTML = `
            <label class="context-bar__edit-field-label">${label}</label>
            <input class="context-bar__edit-field-input" id="${id}" type="${type}" value="${this._escapeAttr(value)}" />
        `;
        return group;
    }

    _escapeAttr(str) {
        return str.replace(/"/g, '&quot;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
    }

    _isoToLocal(iso) {
        if (!iso) return '';
        try {
            const d = new Date(iso);
            // Format as YYYY-MM-DDTHH:mm for datetime-local input
            const pad = n => String(n).padStart(2, '0');
            return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}`;
        } catch {
            return '';
        }
    }

    async _submitEdit(pill) {
        const labelInput = this._editPopover?.querySelector('#edit-pill-label');
        const newLabel = labelInput?.value?.trim();
        if (!newLabel) return;

        const updates = { label: newLabel };
        const newFields = JSON.parse(JSON.stringify(pill.fields || {}));

        if (pill.pill_type === 'custom') {
            const valInput = this._editPopover.querySelector('#edit-pill-value');
            if (valInput) newFields.value = { ...newFields.value, value: valInput.value.trim() };
        } else if (pill.pill_type === 'timeline') {
            const startInput = this._editPopover.querySelector('#edit-pill-start');
            const endInput = this._editPopover.querySelector('#edit-pill-end');
            if (startInput && startInput.value) {
                newFields.start_date = { ...newFields.start_date, value: new Date(startInput.value).toISOString() };
            }
            if (endInput && endInput.value) {
                newFields.end_date = { ...newFields.end_date, value: new Date(endInput.value).toISOString() };
            }
        } else if (pill.pill_type === 'case' || pill.pill_type === 'jira') {
            for (const key of Object.keys(newFields)) {
                if (key === 'source') continue;
                const input = this._editPopover.querySelector(`#edit-pill-${key}`);
                if (input) newFields[key] = { ...newFields[key], value: input.value.trim() };
            }
        }

        updates.fields = newFields;
        this._hideEditPopover();
        await this.updatePill(pill.pill_id, updates);
    }

    _hideEditPopover() {
        if (this._editPopover) {
            this._editPopover.remove();
            this._editPopover = null;
        }
    }

    // ------------------------------------------------------------------
    // Drag-to-reorder
    // ------------------------------------------------------------------

    _onDragStart(e, pill) {
        this._dragState = { pillId: pill.pill_id };
        e.dataTransfer.effectAllowed = 'move';
        e.dataTransfer.setData('text/plain', pill.pill_id);
        e.target.classList.add('ctx-pill--dragging');
    }

    _onDragOver(e, pill) {
        e.preventDefault();
        e.dataTransfer.dropEffect = 'move';
    }

    _onDragEnter(e) {
        const pillEl = e.target.closest('.ctx-pill');
        if (pillEl) pillEl.classList.add('ctx-pill--drag-over');
    }

    _onDragLeave(e) {
        const pillEl = e.target.closest('.ctx-pill');
        if (pillEl) pillEl.classList.remove('ctx-pill--drag-over');
    }

    async _onDrop(e, targetPill) {
        e.preventDefault();
        const pillEl = e.target.closest('.ctx-pill');
        if (pillEl) pillEl.classList.remove('ctx-pill--drag-over');

        if (!this._dragState) return;
        const sourcePillId = this._dragState.pillId;
        if (sourcePillId === targetPill.pill_id) return;

        const fromIdx = this.pills.findIndex(p => p.pill_id === sourcePillId);
        const toIdx = this.pills.findIndex(p => p.pill_id === targetPill.pill_id);
        if (fromIdx === -1 || toIdx === -1) return;

        // Move pill in array
        const [moved] = this.pills.splice(fromIdx, 1);
        this.pills.splice(toIdx, 0, moved);

        // Update sort_order for all pills
        const updates = [];
        for (let i = 0; i < this.pills.length; i++) {
            this.pills[i].sort_order = i;
            updates.push(this._reorderPill(this.pills[i].pill_id, i));
        }
        this.render();
        await Promise.all(updates);
    }

    _onDragEnd() {
        this._dragState = null;
        document.querySelectorAll('.ctx-pill--dragging').forEach(el => el.classList.remove('ctx-pill--dragging'));
        document.querySelectorAll('.ctx-pill--drag-over').forEach(el => el.classList.remove('ctx-pill--drag-over'));
    }

    // ------------------------------------------------------------------
    // Auto-normalize timeline on case/jira pill creation
    // ------------------------------------------------------------------

    async createPillWithAutoTimeline(type, label, fields) {
        const result = await this.createPill(type, label, fields);

        // Auto-create companion timeline pill for case/jira
        if ((type === 'case' || type === 'jira') && result) {
            const now = new Date().toISOString();
            // Default timeline: 24h window ending now
            const start = new Date(Date.now() - 24 * 60 * 60 * 1000).toISOString();
            const caseId = fields.case_id?.value || label;
            await this.createPill('timeline', `${caseId} Timeline`, {
                start_date: { value: start, enabled: true },
                end_date: { value: now, enabled: true },
            });
        }
    }

    // ------------------------------------------------------------------
    // Context budget (token estimate)
    // ------------------------------------------------------------------

    _updateBudget() {
        const budgetEl = document.getElementById('contextBudget');
        if (!budgetEl) return;

        const enabledPills = this.pills.filter(p => p.enabled);
        if (enabledPills.length === 0) {
            budgetEl.textContent = '';
            budgetEl.title = '';
            return;
        }

        // Rough token estimate: ~4 chars per token
        let charCount = 0;
        charCount += '## Workspace Context\n\n'.length;
        for (const pill of enabledPills) {
            const fields = pill.fields || {};
            for (const [key, field] of Object.entries(fields)) {
                if (typeof field === 'object' && field.enabled !== false && field.value) {
                    charCount += pill.label.length + key.length + field.value.length + 6; // overhead
                }
            }
        }
        const estimatedTokens = Math.ceil(charCount / 4);

        budgetEl.textContent = `~${estimatedTokens} tok`;
        budgetEl.title = `${enabledPills.length} pill${enabledPills.length !== 1 ? 's' : ''} enabled, ~${estimatedTokens} tokens estimated`;

        // Color coding based on token count
        budgetEl.className = 'context-bar__budget';
        if (estimatedTokens > 2000) {
            budgetEl.classList.add('context-bar__budget--high');
        } else if (estimatedTokens > 1000) {
            budgetEl.classList.add('context-bar__budget--medium');
        }
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
        this._hideContextMenu();
        this._hideEditPopover();

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
            // Use auto-timeline creation for case/jira pills
            this.createPillWithAutoTimeline(type, label, fields);
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
    // Field-level popover (right-click → Fields…)
    // ------------------------------------------------------------------

    _showFieldPopover(pill, x, y) {
        this._hideFieldPopover();
        this._hideMenu();
        this._hidePopover();
        this._hideContextMenu();

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
