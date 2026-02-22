/**
 * EvidenceViewer — modal that resolves and renders evidence for an assertion.
 *
 * Usage:
 *   const viewer = new EvidenceViewer(app);
 *   await viewer.show(investigationId, documentId, assertionId);
 */

class EvidenceViewer {
    constructor(app) {
        this.app = app;
        this._modal = null;
    }

    // ----------------------------------------------------------------
    // Public API
    // ----------------------------------------------------------------

    async show(investigationId, documentId, assertionId) {
        this._removeModal();

        const modal = this._buildLoadingModal();
        document.body.appendChild(modal);
        this._modal = modal;

        try {
            const data = await this.app.apiFetch(
                `/api/investigations/${investigationId}/documents/${documentId}/evidence/${assertionId}?context_before=4&context_after=4`
            );
            this._renderModal(modal, data);
        } catch (err) {
            this._renderError(modal, err.message || 'Failed to load evidence');
        }
    }

    dismiss() {
        this._removeModal();
    }

    // ----------------------------------------------------------------
    // Modal construction
    // ----------------------------------------------------------------

    _buildLoadingModal() {
        const modal = document.createElement('div');
        modal.className = 'ev-modal';
        modal.innerHTML = `
            <div class="ev-modal__dialog">
                <div class="ev-modal__header">
                    <div class="ev-modal__title">Loading evidence…</div>
                    <button class="ev-modal__close" title="Close">&times;</button>
                </div>
                <div class="ev-modal__body">
                    <div class="inv-assertions__loading">Resolving spans…</div>
                </div>
            </div>
        `;
        this._wireClose(modal);
        return modal;
    }

    _renderModal(modal, data) {
        const claim = data.claim || '(no claim)';
        const state = data.workflow_state || 'draft';
        const evidence = data.evidence || [];

        const stateClass = `ev-item__state-badge--${state}`;
        const header = modal.querySelector('.ev-modal__header');
        header.innerHTML = `
            <div>
                <div class="ev-modal__title">${this.app.escapeHtml(claim)}</div>
                <div class="ev-modal__meta">
                    <span class="inv-assertion-card__state-badge inv-assertion-card__state-badge--${state}">${state}</span>
                    &nbsp;&nbsp;${evidence.length} evidence item${evidence.length !== 1 ? 's' : ''}
                </div>
            </div>
            <button class="ev-modal__close" title="Close">&times;</button>
        `;
        this._wireClose(modal);

        const body = modal.querySelector('.ev-modal__body');
        body.innerHTML = '';

        if (evidence.length === 0) {
            body.innerHTML = '<div class="inv-assertions__empty">No evidence items attached.</div>';
            return;
        }

        evidence.forEach((ev, idx) => {
            const item = document.createElement('div');
            item.className = 'ev-item';

            const metaHtml = [
                ev.artifact_ref ? `<span title="Artifact SHA-256">${ev.artifact_ref.slice(0, 12)}…</span>` : '',
                ev.content_encoding ? `<span>${ev.content_encoding}</span>` : '',
                ev.newline_mode   ? `<span>${ev.newline_mode}</span>` : '',
                `<span>bytes ${ev.byte_start}–${ev.byte_end}</span>`,
                `<span>lines ${ev.line_start + 1}–${ev.line_end + 1}</span>`,
                !ev.excerpt_matches_stored ? `<span style="color:var(--status-error,#f76a6a)" title="Excerpt hash mismatch — artifact may have changed">⚠ hash mismatch</span>` : '',
            ].filter(Boolean).join('');

            item.innerHTML = `
                <div class="ev-item__meta">${metaHtml}</div>
                <div class="ev-viewer" id="ev-viewer-${idx}"></div>
                ${ev.note ? `<div style="font-size:11px;color:var(--text-tertiary);margin-top:6px">${this.app.escapeHtml(ev.note)}</div>` : ''}
            `;
            body.appendChild(item);

            const viewer = item.querySelector(`#ev-viewer-${idx}`);
            this._renderViewer(viewer, ev);
        });
    }

    _renderError(modal, message) {
        const body = modal.querySelector('.ev-modal__body');
        body.innerHTML = `<div style="color:var(--status-error,#f76a6a);font-size:13px">Error: ${this.app.escapeHtml(message)}</div>`;
    }

    // ----------------------------------------------------------------
    // Code viewer — line-numbered, highlighted span
    // ----------------------------------------------------------------

    _renderViewer(container, ev) {
        const ctx = ev.context || {};
        const before    = ctx.before    || [];
        const highlight = ctx.highlighted || [];
        const after     = ctx.after     || [];
        const ctxStart  = ctx.context_line_start || 0;
        const lineStart = ev.line_start || 0;
        const lineEnd   = ev.line_end   || 0;

        const rows = document.createElement('div');
        rows.className = 'ev-viewer__rows';

        // Before context
        before.forEach((text, i) => {
            rows.appendChild(this._makeRow(ctxStart + i, text, 'before'));
        });

        // Separator if there's a gap between file start and context
        if (ctxStart > 0 && before.length === 0) {
            rows.appendChild(this._makeSep());
        }

        // Highlighted lines
        highlight.forEach((text, i) => {
            rows.appendChild(this._makeRow(lineStart + i, text, 'highlight'));
        });

        // After context
        after.forEach((text, i) => {
            rows.appendChild(this._makeRow(lineEnd + 1 + i, text, 'after'));
        });

        container.appendChild(rows);

        // Scroll first highlighted line into view (deterministic jump)
        requestAnimationFrame(() => {
            const first = rows.querySelector('.ev-viewer__row--highlight');
            if (first) {
                first.scrollIntoView({ block: 'nearest', behavior: 'smooth' });
            }
        });
    }

    _makeRow(lineIndex, text, kind) {
        const row = document.createElement('div');
        row.className = `ev-viewer__row ev-viewer__row--${kind}`;

        const gutter = document.createElement('div');
        gutter.className = 'ev-viewer__gutter';
        gutter.textContent = lineIndex + 1;  // 1-based display

        const line = document.createElement('div');
        line.className = 'ev-viewer__line';
        // Preserve whitespace, escape HTML
        line.textContent = text;

        row.appendChild(gutter);
        row.appendChild(line);
        return row;
    }

    _makeSep() {
        const row = document.createElement('div');
        row.className = 'ev-viewer__row ev-viewer__row--sep';
        const gutter = document.createElement('div');
        gutter.className = 'ev-viewer__gutter';
        gutter.textContent = '…';
        const line = document.createElement('div');
        line.className = 'ev-viewer__line';
        line.textContent = '…';
        row.appendChild(gutter);
        row.appendChild(line);
        return row;
    }

    // ----------------------------------------------------------------
    // Helpers
    // ----------------------------------------------------------------

    _wireClose(modal) {
        const btn = modal.querySelector('.ev-modal__close');
        if (btn) btn.addEventListener('click', () => this._removeModal());

        // Backdrop click
        modal.addEventListener('click', (e) => {
            if (e.target === modal) this._removeModal();
        });

        // Escape key
        this._escHandler = (e) => {
            if (e.key === 'Escape') this._removeModal();
        };
        document.addEventListener('keydown', this._escHandler);
    }

    _removeModal() {
        if (this._modal) {
            this._modal.remove();
            this._modal = null;
        }
        if (this._escHandler) {
            document.removeEventListener('keydown', this._escHandler);
            this._escHandler = null;
        }
    }
}
