/**
 * Triage Window — investigation management.
 *
 * Create, view, escalate, and resolve investigations.
 * Each investigation auto-links to a conversation session.
 */

class TriageWindow {
    constructor(app) {
        this.app = app;
        this.investigations = [];
        this.activeInvestigationId = null;
    }

    activate() {
        this.fetchInvestigations();
    }

    deactivate() {
        // Nothing to clean up
    }

    bindEvents() {
        const btnNewInvestigation = document.getElementById('btnNewInvestigation');
        if (btnNewInvestigation) {
            btnNewInvestigation.addEventListener('click', () => this.openNewInvestigationDialog());
        }

        const investigationFilter = document.getElementById('investigationFilter');
        if (investigationFilter) {
            investigationFilter.addEventListener('change', () => this.fetchInvestigations());
        }
    }

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

    renderList() {
        const container = document.getElementById('investigationList');
        if (!container) return;

        if (this.investigations.length === 0) {
            container.innerHTML = '<div class="investigation-list__empty">No investigations. Click "+ New Investigation" to create one.</div>';
            return;
        }

        container.innerHTML = '';
        for (const inv of this.investigations) {
            const card = document.createElement('div');
            card.className = `investigation-card investigation-card--${inv.severity}`;
            if (inv.investigation_id === this.activeInvestigationId) {
                card.classList.add('investigation-card--active');
            }

            const age = this.timeAgo(inv.created_at);
            const systems = Array.isArray(inv.affected_systems) ? inv.affected_systems.join(', ') : '';

            card.innerHTML = `
                <span class="investigation-card__severity">${this.app.escapeHtml(inv.severity)}</span>
                <div class="investigation-card__content">
                    <div class="investigation-card__title">${this.app.escapeHtml(inv.title)}</div>
                    <div class="investigation-card__meta">${age}${systems ? ' \u00b7 ' + this.app.escapeHtml(systems) : ''}</div>
                </div>
                <div class="investigation-card__agent-status">
                    <span class="agent-hud__dot agent-hud__dot--${inv.status === 'resolved' ? 'completed' : 'running'}"></span>
                    <span>${this.app.escapeHtml(inv.status)}</span>
                </div>
            `;

            card.addEventListener('click', () => this.selectInvestigation(inv.investigation_id));
            container.appendChild(card);
        }
    }

    async selectInvestigation(investigationId) {
        this.activeInvestigationId = investigationId;
        this.renderList();

        try {
            const data = await this.app.apiFetch(`/api/investigations/${investigationId}`);
            this.renderDetail(data);
        } catch (e) {
            console.error('Failed to fetch investigation:', e);
        }
    }

    renderDetail(inv) {
        const detail = document.getElementById('investigationDetail');
        if (!detail) return;

        detail.style.display = 'block';

        const checklist = Array.isArray(inv.checklist) ? inv.checklist : [];
        const checklistHtml = checklist.map((item, i) => `
            <li class="${item.checked ? 'checked' : ''}" data-idx="${i}">
                <span class="investigation-detail__checklist-box">${item.checked ? '&#10003;' : ''}</span>
                ${this.app.escapeHtml(item.label)}
            </li>
        `).join('');

        const systems = Array.isArray(inv.affected_systems) ? inv.affected_systems.join(', ') : '';

        detail.innerHTML = `
            <div class="investigation-detail__header">
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
            ${inv.description ? `<div class="investigation-detail__description">${this.app.escapeHtml(inv.description)}</div>` : ''}
            ${checklist.length > 0 ? `
                <div class="investigation-detail__section-title">Checklist</div>
                <ul class="investigation-detail__checklist">${checklistHtml}</ul>
            ` : ''}
            ${inv.session_id ? `
                <div class="investigation-detail__agent-summary">
                    Agent linked: session ${inv.session_id.substring(0, 8)}...
                    <a href="#" class="investigation-detail__open-conversation" style="margin-left:8px; color:var(--accent-primary)">Open conversation</a>
                </div>
            ` : ''}
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

        // Wire open conversation link
        const convLink = detail.querySelector('.investigation-detail__open-conversation');
        if (convLink && inv.session_id) {
            convLink.addEventListener('click', (e) => {
                e.preventDefault();
                this.app.switchWindow('inbox');
                this.app.selectSession(inv.session_id);
            });
        }

        // Wire action buttons
        const btnEscalate = detail.querySelector('#btnEscalate');
        if (btnEscalate) {
            btnEscalate.addEventListener('click', () => this.escalateInvestigation(inv.investigation_id));
        }
        const btnResolve = detail.querySelector('#btnResolve');
        if (btnResolve) {
            btnResolve.addEventListener('click', () => this.resolveInvestigation(inv.investigation_id));
        }
    }

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
            document.getElementById('investigationDetail').style.display = 'none';
            this.activeInvestigationId = null;
        } catch (e) {
            console.error('Failed to resolve:', e);
        }
    }

    openNewInvestigationDialog() {
        const overlay = document.createElement('div');
        overlay.className = 'investigation-dialog-overlay';
        overlay.innerHTML = `
            <div class="dialog">
                <div class="dialog__header">
                    <h2 class="dialog__title">New Investigation</h2>
                    <button class="dialog__close" id="btnCloseInvestigation">&#10005;</button>
                </div>
                <div class="dialog__body">
                    <label class="dialog__label">Title
                        <input type="text" class="dialog__input" id="invTitle" placeholder="e.g. API latency spike" autofocus>
                    </label>
                    <label class="dialog__label">Severity
                        <select class="dialog__select" id="invSeverity">
                            <option value="critical">Critical</option>
                            <option value="high">High</option>
                            <option value="medium" selected>Medium</option>
                            <option value="low">Low</option>
                        </select>
                    </label>
                    <label class="dialog__label">Affected Systems (comma-separated)
                        <input type="text" class="dialog__input" id="invSystems" placeholder="e.g. api-gateway, nginx">
                    </label>
                    <label class="dialog__label">Description
                        <textarea class="dialog__input" id="invDescription" rows="3" placeholder="What's happening?"></textarea>
                    </label>
                </div>
                <div class="dialog__footer">
                    <button class="dialog__btn dialog__btn--secondary" id="btnCancelInvestigation">Cancel</button>
                    <button class="dialog__btn dialog__btn--primary" id="btnSubmitInvestigation">Create Investigation</button>
                </div>
            </div>
        `;

        document.body.appendChild(overlay);

        const close = () => overlay.remove();
        overlay.querySelector('#btnCloseInvestigation').addEventListener('click', close);
        overlay.querySelector('#btnCancelInvestigation').addEventListener('click', close);
        overlay.addEventListener('click', (e) => { if (e.target === overlay) close(); });

        overlay.querySelector('#btnSubmitInvestigation').addEventListener('click', async () => {
            const title = overlay.querySelector('#invTitle').value.trim();
            if (!title) { overlay.querySelector('#invTitle').focus(); return; }

            const severity = overlay.querySelector('#invSeverity').value;
            const systems = overlay.querySelector('#invSystems').value.split(',').map(s => s.trim()).filter(Boolean);
            const description = overlay.querySelector('#invDescription').value.trim();

            try {
                await this.app.apiFetch('/api/investigations', {
                    method: 'POST',
                    body: JSON.stringify({
                        title, severity, affected_systems: systems, description,
                        workspace_id: this.app.activeWorkspaceId,
                    }),
                });
                close();
                this.fetchInvestigations();
            } catch (e) {
                console.error('Failed to create investigation:', e);
                alert(`Failed: ${e.message}`);
            }
        });

        setTimeout(() => overlay.querySelector('#invTitle').focus(), 50);
    }

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
