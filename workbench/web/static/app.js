/**
 * Agent Manager — Client-side application
 *
 * Manages UI state, API communication (with auth + CSRF),
 * sidebar interactions, conversation rendering, and inbox search.
 */

class AgentManagerApp {
    constructor() {
        // State
        this.csrfToken = '';
        this.authToken = '';  // Set via config or login flow
        this.sessions = [];
        this.workspaces = [];
        this.currentSessionId = null;
        this.currentWorkspace = 'playground';
        this.currentView = 'conversation'; // 'conversation' | 'inbox'
        this.searchDebounceTimer = null;

        // Boot
        this.init();
    }

    async init() {
        this.bindElements();
        this.bindEvents();
        await this.fetchCSRFToken();
        await Promise.all([
            this.fetchWorkspaces(),
            this.fetchSessions(),
        ]);
        this.render();
    }

    // ---- Element References ----

    bindElements() {
        // Sidebar
        this.elInboxBtn = document.getElementById('btnInbox');
        this.elInboxBadge = document.getElementById('inboxBadge');
        this.elStartBtn = document.getElementById('btnStartConversation');
        this.elWorkspaceList = document.getElementById('workspaceList');
        this.elPlaygroundList = document.getElementById('playgroundList');
        this.elNewPlayground = document.getElementById('btnNewPlayground');
        this.elOpenWorkspace = document.getElementById('btnOpenWorkspace');

        // Conversation view
        this.elConversationView = document.getElementById('conversationView');
        this.elCurrentWorkspace = document.getElementById('currentWorkspace');
        this.elMessages = document.getElementById('messagesContainer');
        this.elMessageInput = document.getElementById('messageInput');
        this.elSendBtn = document.getElementById('btnSend');
        this.elViewInbox = document.getElementById('btnViewInbox');
        this.elAboutSection = document.getElementById('aboutSection');
        this.elModeSelect = document.getElementById('modeSelect');
        this.elModelSelect = document.getElementById('modelSelect');

        // Inbox view
        this.elInboxView = document.getElementById('inboxView');
        this.elInboxSearch = document.getElementById('inboxSearch');
        this.elInboxList = document.getElementById('inboxList');
        this.elInboxEmpty = document.getElementById('inboxEmpty');
        this.elBackBtn = document.getElementById('btnBackToConversation');
    }

    // ---- Event Binding ----

    bindEvents() {
        // Start conversation
        this.elStartBtn.addEventListener('click', () => this.createSession());
        this.elNewPlayground.addEventListener('click', () => this.createSession());

        // Send message
        this.elSendBtn.addEventListener('click', () => this.sendMessage());
        this.elMessageInput.addEventListener('keydown', (e) => {
            if (e.key === 'Enter' && !e.shiftKey) {
                e.preventDefault();
                this.sendMessage();
            }
        });

        // Auto-resize textarea
        this.elMessageInput.addEventListener('input', () => {
            this.elMessageInput.style.height = 'auto';
            this.elMessageInput.style.height = Math.min(this.elMessageInput.scrollHeight, 200) + 'px';
            // Toggle send button active state
            this.elSendBtn.classList.toggle('input-box__send-btn--active', this.elMessageInput.value.trim().length > 0);
        });

        // View switching
        this.elViewInbox.addEventListener('click', () => this.switchView('inbox'));
        this.elInboxBtn.addEventListener('click', () => this.switchView('inbox'));
        this.elBackBtn.addEventListener('click', () => this.switchView('conversation'));

        // Inbox search
        this.elInboxSearch.addEventListener('input', () => {
            clearTimeout(this.searchDebounceTimer);
            this.searchDebounceTimer = setTimeout(() => this.searchInbox(), 250);
        });
    }

    // ---- API Layer ----

    async apiFetch(path, options = {}) {
        const headers = {
            'Content-Type': 'application/json',
            ...options.headers,
        };

        if (this.authToken) {
            headers['Authorization'] = `Bearer ${this.authToken}`;
        }

        if (['POST', 'PUT', 'DELETE'].includes(options.method)) {
            headers['X-CSRF-Token'] = this.csrfToken;
        }

        try {
            const resp = await fetch(path, { ...options, headers });
            if (!resp.ok) {
                const err = await resp.json().catch(() => ({ error: resp.statusText }));
                throw new Error(err.error || `HTTP ${resp.status}`);
            }
            return await resp.json();
        } catch (e) {
            console.error(`API error [${path}]:`, e);
            throw e;
        }
    }

    async fetchCSRFToken() {
        try {
            const data = await this.apiFetch('/api/csrf-token');
            this.csrfToken = data.csrf_token;
        } catch (e) {
            console.warn('CSRF token fetch failed:', e);
        }
    }

    // ---- Data Fetching ----

    async fetchWorkspaces() {
        try {
            const data = await this.apiFetch('/api/workspaces');
            this.workspaces = data.workspaces || [];
        } catch (e) {
            this.workspaces = [{ name: 'local', type: 'local', connected: true }];
        }
    }

    async fetchSessions() {
        try {
            const data = await this.apiFetch('/api/sessions');
            this.sessions = data.sessions || [];
        } catch (e) {
            this.sessions = [];
        }
    }

    async fetchSessionDetail(sessionId) {
        try {
            return await this.apiFetch(`/api/sessions/${sessionId}`);
        } catch (e) {
            return null;
        }
    }

    // ---- Actions ----

    async createSession() {
        try {
            const data = await this.apiFetch('/api/sessions', {
                method: 'POST',
                body: JSON.stringify({ workspace: this.currentWorkspace }),
            });

            this.currentSessionId = data.session_id;
            await this.fetchSessions();
            this.render();
            this.switchView('conversation');
            this.elAboutSection.style.display = 'none';
            this.elMessageInput.focus();
        } catch (e) {
            console.error('Failed to create session:', e);
        }
    }

    async sendMessage() {
        const content = this.elMessageInput.value.trim();
        if (!content) return;

        if (!this.currentSessionId) {
            await this.createSession();
        }

        // Optimistic UI: show message immediately
        this.appendMessage('user', content);
        this.elMessageInput.value = '';
        this.elMessageInput.style.height = 'auto';
        this.elSendBtn.classList.remove('input-box__send-btn--active');
        this.elAboutSection.style.display = 'none';

        // Show loading
        const loadingEl = this.appendLoading();

        try {
            const data = await this.apiFetch(`/api/sessions/${this.currentSessionId}/messages`, {
                method: 'POST',
                body: JSON.stringify({ content }),
            });

            loadingEl.remove();

            if (data.response) {
                this.appendMessage('assistant', data.response);
            }
        } catch (e) {
            loadingEl.remove();
            this.appendMessage('assistant', `Error: ${e.message}`);
        }
    }

    async searchInbox() {
        const q = this.elInboxSearch.value.trim();
        try {
            const data = await this.apiFetch(`/api/inbox/search?q=${encodeURIComponent(q)}`);
            this.renderInboxItems(data.items || []);
        } catch (e) {
            console.error('Inbox search failed:', e);
        }
    }

    async selectSession(sessionId) {
        this.currentSessionId = sessionId;
        this.switchView('conversation');
        this.elAboutSection.style.display = 'none';

        // Clear and load messages
        this.elMessages.innerHTML = '';

        const detail = await this.fetchSessionDetail(sessionId);
        if (detail && detail.events) {
            for (const event of detail.events) {
                if (event.event_type === 'user_message') {
                    this.appendMessage('user', event.payload.content || '');
                } else if (event.event_type === 'assistant_message') {
                    this.appendMessage('assistant', event.payload.content || '');
                }
            }
        }

        this.renderSidebar();
    }

    // ---- View Switching ----

    switchView(view) {
        this.currentView = view;
        if (view === 'conversation') {
            this.elConversationView.style.display = 'flex';
            this.elInboxView.style.display = 'none';
        } else {
            this.elConversationView.style.display = 'none';
            this.elInboxView.style.display = 'flex';
            this.searchInbox();
        }
    }

    // ---- Rendering ----

    render() {
        this.renderSidebar();
        this.renderWorkspaceLabel();
    }

    renderWorkspaceLabel() {
        this.elCurrentWorkspace.textContent =
            this.currentWorkspace.charAt(0).toUpperCase() + this.currentWorkspace.slice(1);
    }

    renderSidebar() {
        this.renderWorkspaces();
        this.renderPlaygroundSessions();
    }

    renderWorkspaces() {
        this.elWorkspaceList.innerHTML = '';

        for (const ws of this.workspaces) {
            const el = document.createElement('div');
            el.className = 'sidebar__workspace-item';
            if (ws.name === this.currentWorkspace) {
                el.classList.add('sidebar__workspace-item--active');
            }

            const statusClass = ws.connected
                ? 'sidebar__workspace-status--connected'
                : 'sidebar__workspace-status--disconnected';

            el.innerHTML = `
                <span class="sidebar__workspace-chevron">▸</span>
                <span class="sidebar__workspace-status ${statusClass}"></span>
                <span class="sidebar__workspace-name">${this.escapeHtml(ws.name)}</span>
                ${ws.type === 'ssh'
                    ? '<span class="sidebar__workspace-type sidebar__workspace-type--ssh">SSH</span>'
                    : ''
                }
                <button class="sidebar__workspace-add-btn" title="New conversation">+</button>
            `;

            el.addEventListener('click', () => {
                this.currentWorkspace = ws.name;
                this.render();
            });

            const addBtn = el.querySelector('.sidebar__workspace-add-btn');
            if (addBtn) {
                addBtn.addEventListener('click', (e) => {
                    e.stopPropagation();
                    this.currentWorkspace = ws.name;
                    this.createSession();
                });
            }

            this.elWorkspaceList.appendChild(el);
        }
    }

    renderPlaygroundSessions() {
        this.elPlaygroundList.innerHTML = '';

        const playgroundSessions = this.sessions.filter(
            s => (s.workspace || 'playground') === 'playground'
        );

        for (const session of playgroundSessions) {
            const el = document.createElement('div');
            el.className = 'sidebar__conversation-item';
            if (session.session_id === this.currentSessionId) {
                el.classList.add('sidebar__conversation-item--active');
            }

            const title = session.last_message
                ? session.last_message.substring(0, 30) + (session.last_message.length > 30 ? '...' : '')
                : `Session ${session.session_id.substring(0, 8)}...`;

            el.innerHTML = `<span class="sidebar__workspace-name">${this.escapeHtml(title)}</span>`;

            el.addEventListener('click', () => this.selectSession(session.session_id));
            this.elPlaygroundList.appendChild(el);
        }
    }

    renderInboxItems(items) {
        if (!items || items.length === 0) {
            this.elInboxList.innerHTML = '';
            this.elInboxList.appendChild(this.elInboxEmpty);
            this.elInboxEmpty.style.display = 'flex';
            return;
        }

        this.elInboxList.innerHTML = '';

        for (const item of items) {
            const el = document.createElement('div');
            el.className = 'inbox-item';

            const statusClass = item.status === 'completed'
                ? 'inbox-item__status--completed'
                : 'inbox-item__status--returned';

            el.innerHTML = `
                <div class="inbox-item__icon">📋</div>
                <div class="inbox-item__content">
                    <div class="inbox-item__title">${this.escapeHtml(item.last_message || item.session_id)}</div>
                    <div class="inbox-item__meta">${item.workspace} · ${item.message_count} messages · ${this.formatDate(item.created_at)}</div>
                </div>
                <span class="inbox-item__status ${statusClass}">${item.status}</span>
            `;

            el.addEventListener('click', () => this.selectSession(item.session_id));
            this.elInboxList.appendChild(el);
        }
    }

    // ---- Message Rendering ----

    appendMessage(role, content) {
        const el = document.createElement('div');
        el.className = `message message--${role}`;
        el.innerHTML = `
            <div class="message__role">${role === 'user' ? 'You' : 'Agent'}</div>
            <div class="message__content">${this.escapeHtml(content)}</div>
        `;
        this.elMessages.appendChild(el);
        this.elMessages.scrollTop = this.elMessages.scrollHeight;
        return el;
    }

    appendLoading() {
        const el = document.createElement('div');
        el.className = 'message message--assistant';
        el.innerHTML = '<div class="spinner"></div>';
        this.elMessages.appendChild(el);
        this.elMessages.scrollTop = this.elMessages.scrollHeight;
        return el;
    }

    // ---- Utilities ----

    escapeHtml(str) {
        const div = document.createElement('div');
        div.textContent = str;
        return div.innerHTML;
    }

    formatDate(dateStr) {
        if (!dateStr) return '';
        try {
            const d = new Date(dateStr);
            return d.toLocaleDateString(undefined, { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' });
        } catch {
            return dateStr;
        }
    }
}

// ---- Boot ----
document.addEventListener('DOMContentLoaded', () => {
    window.app = new AgentManagerApp();
});
