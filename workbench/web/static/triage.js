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
        this.chatPanelOpen = false;
        this._pendingReviewCounts = {}; // investigationId -> number of submitted assertions
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
        const filter = document.getElementById('investigationFilter');
        if (filter) filter.addEventListener('change', () => this.fetchInvestigations());

        // Chat panel close
        const btnCloseChat = document.getElementById('btnCloseTriageChat');
        if (btnCloseChat) btnCloseChat.addEventListener('click', () => this.closeChatPanel());

        // Search input
        const search = document.getElementById('investigationSearch');
        if (search) {
            search.addEventListener('input', () => this.renderList());
        }

        // Escape closes open panels
        document.addEventListener('keydown', (e) => {
            if (e.key === 'Escape') {
                if (this.chatPanelOpen) this.closeChatPanel();
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
            container.innerHTML = '<div class="investigation-list__empty">No investigations. Add a Case or Jira pill from the context bar.</div>';
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
            const pendingCount = this._pendingReviewCounts[inv.investigation_id] || 0;

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
                    ${pendingCount > 0 ? `<span class="investigation-card__review-badge" title="${pendingCount} assertion${pendingCount !== 1 ? 's' : ''} pending review">${pendingCount}</span>` : ''}
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
            <div class="inv-assertions" id="invAssertionsSection">
                <div class="inv-assertions__header">
                    <div class="inv-assertions__title">Evidence &amp; Assertions</div>
                    <button class="inv-assertions__ingest-btn" id="btnIngestFile" style="display:none">↑ Ingest File</button>
                </div>
                <div class="inv-assertions__loading" id="invAssertionsBody">Loading…</div>
                <input type="file" id="ingestFileInput" style="display:none" accept="*/*">
            </div>
            <div class="inv-narrative" id="invNarrativeSection" style="display:none">
                <div class="inv-narrative__header">
                    <div class="inv-narrative__title">Narratives</div>
                    <div class="inv-narrative__actions">
                        <button class="inv-narrative__regen-btn" id="btnRegenInternal" disabled>Regenerate Internal</button>
                        <button class="inv-narrative__regen-btn" id="btnRegenCustomer" disabled>Regenerate Customer</button>
                    </div>
                </div>
                <div class="inv-narrative__blocks" id="invNarrativeBlocks">
                    <span class="inv-narrative__empty">Loading…</span>
                </div>
            </div>
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
                <button class="investigation-detail__chat-btn investigation-detail__chat-btn--agent" id="btnInvestigateAgent">
                    🤖 Investigate with Agent
                </button>
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
        const btnAgent = detail.querySelector('#btnInvestigateAgent');
        if (btnAgent) {
            btnAgent.addEventListener('click', () => this.startAgentInvestigation(inv));
        }

        // Wire action buttons
        const btnEscalate = detail.querySelector('#btnEscalate');
        if (btnEscalate) btnEscalate.addEventListener('click', () => this.escalateInvestigation(inv.investigation_id));
        const btnResolve = detail.querySelector('#btnResolve');
        if (btnResolve) btnResolve.addEventListener('click', () => this.resolveInvestigation(inv.investigation_id));

        // Wire context pills
        this.wireContextPills(inv);

        // Wire ingest button (shown once _loadAssertions resolves a document)
        this._currentInvId = inv.investigation_id;
        this._currentDocId = null;

        // Async: load document assertions + narratives
        this._loadAssertions(inv.investigation_id);
    }

    // ---- Evidence & assertions ----

    async _loadAssertions(investigationId) {
        const container = document.getElementById('invAssertionsBody');
        if (!container) return;

        try {
            const data = await this.app.apiFetch(
                `/api/investigations/${investigationId}/documents`
            );
            const docs = data.documents || [];

            if (docs.length === 0) {
                container.innerHTML = '<span class="inv-assertions__empty">No evidence documents yet.</span>';
                return;
            }

            // Store first doc ID for ingest button and reveal it
            if (docs.length > 0) {
                this._currentDocId = docs[0].document_id;
                const btnIngest = document.getElementById('btnIngestFile');
                if (btnIngest) {
                    btnIngest.style.display = '';
                    btnIngest.onclick = () => this._triggerIngest(investigationId, this._currentDocId);
                }
                const ingestInput = document.getElementById('ingestFileInput');
                if (ingestInput) {
                    ingestInput.onchange = (e) => {
                        const f = e.target.files[0];
                        if (f) this._uploadFile(investigationId, this._currentDocId, f);
                        e.target.value = '';  // reset so same file can be re-selected
                    };
                }
            }

            // Fetch full graph for each document and collect assertions + narratives
            const allAssertions = [];
            const allNarratives = [];        // { block, _doc_id, _doc_revision }
            const docMeta = {};             // docId -> { current_revision }

            for (const doc of docs) {
                try {
                    const full = await this.app.apiFetch(
                        `/api/investigations/${investigationId}/documents/${doc.document_id}?include=graph`
                    );
                    const blocks = (full.state && full.state.blocks) ? full.state.blocks : {};
                    const assertionStates = (full.state && full.state.assertion_states) ? full.state.assertion_states : {};
                    const docId = doc.document_id;
                    const revision = full.current_revision || 0;
                    docMeta[docId] = { current_revision: revision };

                    for (const [bid, block] of Object.entries(blocks)) {
                        if (block.type === 'assertion') {
                            // Effective approval state comes from assertion_states (review-driven),
                            // not from the assertion block's own workflow_state
                            const effectiveState = assertionStates[bid] || block.workflow_state || 'draft';
                            allAssertions.push({
                                ...block,
                                _doc_id: docId,
                                _inv_id: investigationId,
                                _effective_state: effectiveState,
                                _doc_revision: revision,
                            });
                        }
                        if (block.type === 'narrative') {
                            allNarratives.push({ ...block, _doc_id: docId, _doc_revision: revision });
                        }
                    }
                } catch (_) { /* skip bad doc */ }
            }

            // Track pending review count and refresh list badge
            const submittedCount = allAssertions.filter(a => a._effective_state === 'submitted').length;
            this._pendingReviewCounts[investigationId] = submittedCount;
            this.renderList();

            if (allAssertions.length === 0) {
                container.innerHTML = '<span class="inv-assertions__empty">No assertions yet.</span>';
            } else {
                const hasApproved = allAssertions.some(a => a._effective_state === 'approved');

                container.innerHTML = '';
                for (const a of allAssertions) {
                    const card = this._buildAssertionCard(a);
                    container.appendChild(card);
                }

                // Enable narrative regen buttons if there are approved assertions
                this._wireNarrativeButtons(investigationId, docMeta, hasApproved);
            }

            // Render narrative panel
            this._renderNarrativePanel(allNarratives);

        } catch (e) {
            const container2 = document.getElementById('invAssertionsBody');
            if (container2) container2.innerHTML = '<span class="inv-assertions__empty">Could not load assertions.</span>';
        }
    }

    _buildAssertionCard(assertion) {
        const card = document.createElement('div');
        card.className = 'inv-assertion-card';

        const evidenceCount = Array.isArray(assertion.evidence) ? assertion.evidence.length : 0;
        const effectiveState = assertion._effective_state || 'draft';
        const hasEvidence = evidenceCount > 0;
        const isDecided = (effectiveState === 'approved' || effectiveState === 'rejected');
        const isAgentAuthored = (assertion.created_by || '').startsWith('agent:');

        card.innerHTML = `
            <div class="inv-assertion-card__claim">${this.app.escapeHtml(assertion.claim || '')}</div>
            <div class="inv-assertion-card__footer">
                <span class="inv-assertion-card__state-badge inv-assertion-card__state-badge--${effectiveState}">${effectiveState}</span>
                ${isAgentAuthored ? '<span class="inv-assertion-card__agent-chip">🤖 agent</span>' : ''}
                ${hasEvidence ? `<span class="inv-assertion-card__evidence-badge">${evidenceCount} evidence span${evidenceCount !== 1 ? 's' : ''}</span>` : ''}
                ${hasEvidence ? `<button class="inv-assertion-card__show-ev">Show evidence</button>` : ''}
            </div>
            ${!isDecided ? `
            <div class="inv-assertion-card__actions">
                <button class="inv-assertion-card__approve-btn">Approve</button>
                <button class="inv-assertion-card__reject-btn">Reject</button>
            </div>` : ''}
        `;

        if (hasEvidence) {
            const btn = card.querySelector('.inv-assertion-card__show-ev');
            btn.addEventListener('click', () => {
                if (!this._evidenceViewer) {
                    this._evidenceViewer = new EvidenceViewer(this.app);
                }
                this._evidenceViewer.show(assertion._inv_id, assertion._doc_id, assertion.id);
            });
        }

        if (!isDecided) {
            const approveBtn = card.querySelector('.inv-assertion-card__approve-btn');
            const rejectBtn = card.querySelector('.inv-assertion-card__reject-btn');
            if (approveBtn) {
                approveBtn.addEventListener('click', () => {
                    this._showReviewModal(assertion, 'approved', card);
                });
            }
            if (rejectBtn) {
                rejectBtn.addEventListener('click', () => {
                    this._showReviewModal(assertion, 'rejected', card);
                });
            }
        }

        return card;
    }

    _showReviewModal(assertion, decision, card) {
        const label = decision === 'approved' ? 'Approve' : 'Reject';
        const modal = document.createElement('div');
        modal.className = 'review-modal';
        modal.innerHTML = `
            <div class="review-modal__dialog">
                <div class="review-modal__title">${label} Assertion</div>
                <div class="review-modal__claim">"${this.app.escapeHtml((assertion.claim || '').slice(0, 120))}"</div>
                <div>
                    <div class="review-modal__label">Reason (required)</div>
                    <textarea class="review-modal__reason" placeholder="Enter your reason…" rows="3"></textarea>
                </div>
                <div class="review-modal__actions">
                    <button class="review-modal__cancel">Cancel</button>
                    <button class="review-modal__confirm review-modal__confirm--${decision}">${label}</button>
                </div>
            </div>
        `;
        document.body.appendChild(modal);

        const textarea = modal.querySelector('.review-modal__reason');
        const cancelBtn = modal.querySelector('.review-modal__cancel');
        const confirmBtn = modal.querySelector('.review-modal__confirm');

        const close = () => modal.remove();
        cancelBtn.addEventListener('click', close);
        modal.addEventListener('click', e => { if (e.target === modal) close(); });

        confirmBtn.addEventListener('click', async () => {
            const reason = textarea.value.trim();
            if (!reason) { textarea.focus(); return; }
            confirmBtn.disabled = true;
            try {
                const resp = await this.app.apiFetch(
                    `/api/investigations/${assertion._inv_id}/documents/${assertion._doc_id}/reviews`,
                    {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify({
                            target_assertion_ids: [assertion.id],
                            decision,
                            reason,
                        }),
                    }
                );
                close();
                // Update badge on the card without full re-render
                const badge = card.querySelector('.inv-assertion-card__state-badge');
                if (badge) {
                    badge.className = `inv-assertion-card__state-badge inv-assertion-card__state-badge--${decision}`;
                    badge.textContent = decision;
                }
                const actionsRow = card.querySelector('.inv-assertion-card__actions');
                if (actionsRow) actionsRow.remove();
                // Refresh narrative buttons now that approval state changed
                this._loadAssertions(assertion._inv_id);
            } catch (err) {
                confirmBtn.disabled = false;
                alert('Review failed: ' + (err.message || 'unknown error'));
            }
        });

        textarea.focus();
    }

    _wireNarrativeButtons(investigationId, docMeta, hasApproved) {
        // Use first available document for narrative regen
        const docIds = Object.keys(docMeta);
        if (docIds.length === 0) return;
        const docId = docIds[0];
        const revision = docMeta[docId].current_revision;

        const btnInternal = document.getElementById('btnRegenInternal');
        const btnCustomer = document.getElementById('btnRegenCustomer');
        const section = document.getElementById('invNarrativeSection');

        if (section) section.style.display = '';

        if (btnInternal) {
            btnInternal.disabled = !hasApproved;
            btnInternal.onclick = () => this._regenNarrative(investigationId, docId, 'internal', revision);
        }
        if (btnCustomer) {
            btnCustomer.disabled = !hasApproved;
            btnCustomer.onclick = () => this._regenNarrative(investigationId, docId, 'customer', revision);
        }
    }

    async _regenNarrative(investigationId, docId, audience, expectedRevision) {
        const blocksEl = document.getElementById('invNarrativeBlocks');
        if (blocksEl) blocksEl.innerHTML = '<span class="inv-narrative__empty">Generating…</span>';
        try {
            await this.app.apiFetch(
                `/api/investigations/${investigationId}/documents/${docId}/narratives:regenerate`,
                {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ audience, expected_revision: expectedRevision }),
                }
            );
            // Reload assertions (which also reloads narratives)
            this._loadAssertions(investigationId);
        } catch (err) {
            if (blocksEl) blocksEl.innerHTML = `<span class="inv-narrative__empty" style="color:var(--status-error)">${this.app.escapeHtml(err.message || 'Failed')}</span>`;
        }
    }

    _renderNarrativePanel(narratives) {
        const blocksEl = document.getElementById('invNarrativeBlocks');
        const section = document.getElementById('invNarrativeSection');
        if (!blocksEl) return;

        if (narratives.length === 0) {
            blocksEl.innerHTML = '<span class="inv-narrative__empty">No narratives generated yet.</span>';
            if (section) section.style.display = '';
            return;
        }

        // Latest per audience
        const latest = {};
        for (const n of narratives) {
            const aud = n.audience || 'internal';
            if (!latest[aud] || (n.generated_at || '') > (latest[aud].generated_at || '')) {
                latest[aud] = n;
            }
        }

        blocksEl.innerHTML = '';
        for (const [aud, n] of Object.entries(latest)) {
            const block = document.createElement('div');
            block.className = 'inv-narrative__block';
            block.innerHTML = `
                <div class="inv-narrative__block-meta">
                    <span class="inv-narrative__audience-badge inv-narrative__audience-badge--${aud}">${aud}</span>
                    <span class="inv-narrative__rev">rev ${n.source_revision || 0} · ${(n.generated_at || '').slice(0, 16).replace('T', ' ')}</span>
                </div>
                <div class="inv-narrative__content">${this.app.escapeHtml(n.content || '')}</div>
            `;
            blocksEl.appendChild(block);
        }

        if (section) section.style.display = '';
    }

    // ---- File ingest ----

    _triggerIngest(investigationId, docId) {
        if (!docId) return;
        const input = document.getElementById('ingestFileInput');
        if (input) input.click();
    }

    async _uploadFile(investigationId, docId, file) {
        const btn = document.getElementById('btnIngestFile');
        const container = document.getElementById('invAssertionsBody');

        if (btn) { btn.disabled = true; btn.textContent = 'Uploading…'; }

        const fd = new FormData();
        fd.append('file', file);
        fd.append('label', '');
        fd.append('newline_mode', 'unknown');

        try {
            const result = await this.app.apiFetch(
                `/api/investigations/${investigationId}/documents/${docId}/ingest/file`,
                { method: 'POST', body: fd }
            );

            // Refresh the assertions panel (picks up new output block)
            this._loadAssertions(investigationId);

            // Brief confirmation in the container
            if (container) {
                const note = document.createElement('div');
                note.style.cssText = 'font-size:11px;color:var(--status-connected,#4af);padding:4px 0';
                note.textContent = `✓ Ingested "${file.name}" — ${result.byte_length?.toLocaleString() || '?'} bytes${result.indexed ? ', indexed' : ''}`;
                container.prepend(note);
                setTimeout(() => note.remove(), 5000);
            }
        } catch (err) {
            if (container) {
                const note = document.createElement('div');
                note.style.cssText = 'font-size:11px;color:var(--status-error,#f76a6a);padding:4px 0';
                note.textContent = `Upload failed: ${err.message || 'unknown error'}`;
                container.prepend(note);
                setTimeout(() => note.remove(), 8000);
            }
        } finally {
            if (btn) { btn.disabled = false; btn.textContent = '↑ Ingest File'; }
        }
    }

    // ---- Embedded chat (right panel) ----

    async showChatView(sessionId, investigationTitle) {
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

    async startAgentInvestigation(investigation) {
        // Require a document to be loaded first
        const docId = this._currentDocId;
        if (!docId) {
            alert('No evidence document found. Ingest some files first before starting an agent investigation.');
            return;
        }
        const invId = investigation.investigation_id;

        try {
            // Create a new session with agent_investigation metadata
            const session = await this.app.apiFetch('/api/sessions', {
                method: 'POST',
                body: JSON.stringify({
                    workspace_id: investigation.workspace_id || this.app.activeWorkspaceId,
                    metadata: {
                        investigation_id: invId,
                        document_id: docId,
                        agent_investigation: true,
                    },
                }),
            });

            // Link session to investigation
            await this.app.apiFetch(`/api/investigations/${invId}`, {
                method: 'PUT',
                body: JSON.stringify({ session_id: session.session_id }),
            });

            investigation.session_id = session.session_id;
            this.activeInvestigation = investigation;

            await this.showChatView(session.session_id, `🤖 ${investigation.title}`);
        } catch (e) {
            console.error('Failed to start agent investigation:', e);
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
