/**
 * Agent Manager — Client-side application
 *
 * Manages UI state, API communication (with auth + CSRF),
 * workspace tabs, sidebar interactions, conversation rendering,
 * and inbox search.
 */

class AgentManagerApp {
    constructor() {
        // State
        this.csrfToken = '';
        this.authToken = '';
        this.sessions = [];
        this.workspaces = [];
        this.activeWorkspaceId = 'global';
        this.currentSessionId = null;
        this.currentView = 'conversation'; // 'conversation' | 'inbox'
        this.searchDebounceTimer = null;

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

        // File menu
        this.elMenuFile = document.getElementById('menuFile');
        this.elFileDropdown = document.getElementById('fileDropdown');
        this.elMenuStartConversation = document.getElementById('menuStartConversation');
        this.elMenuNewEditor = document.getElementById('menuNewEditor');
        this.elMenuOpenWorkspace = document.getElementById('menuOpenWorkspace');

        // Workspace tabs
        this.elDynamicTabs = document.getElementById('dynamicTabs');
        this.elTabGlobal = document.getElementById('tabGlobal');
        this.elBtnNewWorkspace = document.getElementById('btnNewWorkspace');

        // New workspace dialog
        this.elDialog = document.getElementById('newWorkspaceDialog');
        this.elWsName = document.getElementById('wsName');
        this.elWsPath = document.getElementById('wsPath');
        this.elWsBackend = document.getElementById('wsBackend');
        this.elBtnCloseDialog = document.getElementById('btnCloseDialog');
        this.elBtnCancelWorkspace = document.getElementById('btnCancelWorkspace');
        this.elBtnCreateWorkspace = document.getElementById('btnCreateWorkspace');

        // Directory browser
        this.elBtnBrowse = document.getElementById('btnBrowse');
        this.elDirBrowser = document.getElementById('dirBrowser');
        this.elDirBrowserPath = document.getElementById('dirBrowserPath');
        this.elDirBrowserList = document.getElementById('dirBrowserList');
        this.elBtnDirUp = document.getElementById('btnDirUp');
        this.elBtnDirCancel = document.getElementById('btnDirCancel');
        this.elBtnDirSelect = document.getElementById('btnDirSelect');
        this.elBtnNewFolder = document.getElementById('btnNewFolder');
        this.browsePath = null;
        this.browseParent = null;

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
        // File menu
        this.elMenuFile.addEventListener('click', (e) => {
            e.stopPropagation();
            this.toggleFileMenu();
        });
        this.elMenuStartConversation.addEventListener('click', () => {
            this.closeFileMenu();
            this.createSession();
        });
        this.elMenuNewEditor.addEventListener('click', () => {
            this.closeFileMenu();
            console.log('New Editor (not yet implemented)');
        });
        this.elMenuOpenWorkspace.addEventListener('click', () => {
            this.closeFileMenu();
            this.openNewWorkspaceDialog();
        });
        // Close menu on outside click
        document.addEventListener('click', (e) => {
            if (!e.target.closest('#menuFileWrapper')) {
                this.closeFileMenu();
            }
        });

        // Keyboard shortcuts
        document.addEventListener('keydown', (e) => {
            if (e.ctrlKey || e.metaKey) {
                if (e.key === 'n' || e.key === 'N') {
                    e.preventDefault();
                    this.closeFileMenu();
                    this.createSession();
                } else if (e.key === 'e' || e.key === 'E') {
                    e.preventDefault();
                    this.closeFileMenu();
                    console.log('New Editor (not yet implemented)');
                } else if (e.key === 'o' || e.key === 'O') {
                    e.preventDefault();
                    this.closeFileMenu();
                    this.openNewWorkspaceDialog();
                }
            }
        });

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

        // Workspace tabs
        this.elTabGlobal.addEventListener('click', () => this.switchWorkspace('global'));
        this.elBtnNewWorkspace.addEventListener('click', () => this.openNewWorkspaceDialog());
        if (this.elOpenWorkspace) {
            this.elOpenWorkspace.addEventListener('click', () => this.openNewWorkspaceDialog());
        }

        // Dialog
        this.elBtnCloseDialog.addEventListener('click', () => this.closeNewWorkspaceDialog());
        this.elBtnCancelWorkspace.addEventListener('click', () => this.closeNewWorkspaceDialog());
        this.elBtnCreateWorkspace.addEventListener('click', () => this.handleCreateWorkspace());
        this.elDialog.addEventListener('click', (e) => {
            if (e.target === this.elDialog) this.closeNewWorkspaceDialog();
        });

        // Directory browser
        this.elBtnBrowse.addEventListener('click', () => this.openDirBrowser());
        this.elBtnDirUp.addEventListener('click', () => {
            if (this.browseParent) this.browseDirectory(this.browseParent);
        });
        this.elBtnDirCancel.addEventListener('click', () => this.closeDirBrowser());
        this.elBtnNewFolder.addEventListener('click', () => this.promptNewFolder());
        this.elBtnDirSelect.addEventListener('click', () => {
            if (this.browsePath) {
                this.elWsPath.value = this.browsePath;
                // Auto-fill name from directory name if empty
                if (!this.elWsName.value.trim()) {
                    const dirName = this.browsePath.split('/').filter(Boolean).pop() || '';
                    this.elWsName.value = dirName;
                }
            }
            this.closeDirBrowser();
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
                throw new Error(err.error || err.detail || `HTTP ${resp.status}`);
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
            this.workspaces = [];
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

    // ---- Workspace Actions ----

    async switchWorkspace(workspaceId) {
        this.activeWorkspaceId = workspaceId;
        this.currentSessionId = null;

        // Notify backend
        try {
            await this.apiFetch(`/api/workspaces/${workspaceId}/open`, { method: 'POST' });
        } catch (e) {
            console.warn('Failed to mark workspace as opened:', e);
        }

        this.elMessages.innerHTML = '';
        this.elAboutSection.style.display = '';
        this.switchView('conversation');
        this.render();
    }

    // ---- File Menu ----

    toggleFileMenu() {
        const isOpen = this.elFileDropdown.classList.contains('top-bar__dropdown--open');
        if (isOpen) {
            this.closeFileMenu();
        } else {
            this.elFileDropdown.classList.add('top-bar__dropdown--open');
            this.elMenuFile.classList.add('top-bar__menu-item--active');
        }
    }

    closeFileMenu() {
        this.elFileDropdown.classList.remove('top-bar__dropdown--open');
        this.elMenuFile.classList.remove('top-bar__menu-item--active');
    }

    openNewWorkspaceDialog() {
        this.elDialog.style.display = 'flex';
        this.elWsName.value = '';
        this.elWsPath.value = '';
        this.elWsBackend.value = 'local';
        setTimeout(() => this.elWsName.focus(), 50);
    }

    closeNewWorkspaceDialog() {
        this.elDialog.style.display = 'none';
        this.closeDirBrowser();
    }

    // ---- Directory Browser ----

    openDirBrowser() {
        this.elDirBrowser.style.display = '';
        const startPath = this.elWsPath.value.trim() || '~';
        this.browseDirectory(startPath);
    }

    closeDirBrowser() {
        this.elDirBrowser.style.display = 'none';
    }

    async promptNewFolder() {
        if (!this.browsePath) return;

        // Insert a row between the file list and footer
        const footer = this.elBtnNewFolder.closest('.dir-browser__footer');
        const row = document.createElement('div');
        row.className = 'dir-browser__new-folder-row';
        row.innerHTML = `
            <input type="text" class="dialog__input dir-browser__new-folder-input" placeholder="Folder name" />
            <button class="dialog__btn dialog__btn--primary dialog__btn--sm">Create</button>
            <button class="dialog__btn dialog__btn--secondary dialog__btn--sm">Cancel</button>
        `;
        footer.parentElement.insertBefore(row, footer);

        const input = row.querySelector('input');
        const createBtn = row.querySelector('.dialog__btn--primary');
        const cancelBtn = row.querySelector('.dialog__btn--secondary');
        input.focus();

        const cleanup = () => row.remove();

        const create = async () => {
            const name = input.value.trim();
            if (!name) { cleanup(); return; }

            try {
                createBtn.textContent = '...';
                createBtn.disabled = true;
                await this.apiFetch('/api/browse/mkdir', {
                    method: 'POST',
                    body: JSON.stringify({ parent: this.browsePath, name }),
                });
                cleanup();
                await this.browseDirectory(this.browsePath);
            } catch (e) {
                input.classList.add('dir-browser__new-folder-input--error');
                input.value = '';
                input.placeholder = e.message || 'Failed';
                createBtn.textContent = 'Create';
                createBtn.disabled = false;
                setTimeout(() => cleanup(), 1500);
            }
        };

        createBtn.addEventListener('click', create);
        cancelBtn.addEventListener('click', cleanup);
        input.addEventListener('keydown', (e) => {
            if (e.key === 'Enter') { e.preventDefault(); create(); }
            if (e.key === 'Escape') cleanup();
        });
    }

    async browseDirectory(path) {
        try {
            const data = await this.apiFetch(`/api/browse?path=${encodeURIComponent(path)}`);
            this.browsePath = data.path;
            this.browseParent = data.parent;

            this.elDirBrowserPath.textContent = data.path;
            this.elBtnDirUp.disabled = !data.parent;

            this.renderDirEntries(data.entries || []);
        } catch (e) {
            console.error('Browse failed:', e);
            this.elDirBrowserList.innerHTML = `<div class="dir-browser__empty">Could not read directory</div>`;
        }
    }

    renderDirEntries(entries) {
        this.elDirBrowserList.innerHTML = '';

        if (entries.length === 0) {
            this.elDirBrowserList.innerHTML = '<div class="dir-browser__empty">No subdirectories</div>';
            return;
        }

        for (const entry of entries) {
            const el = document.createElement('div');
            el.className = 'dir-browser__entry';
            el.innerHTML = `
                <span class="dir-browser__entry-icon">📁</span>
                <span class="dir-browser__entry-name">${this.escapeHtml(entry.name)}</span>
                ${entry.has_children ? '<span class="dir-browser__entry-arrow">▸</span>' : ''}
            `;

            // Single click selects path
            el.addEventListener('click', () => {
                // Remove previous selection
                this.elDirBrowserList.querySelectorAll('.dir-browser__entry--selected')
                    .forEach(s => s.classList.remove('dir-browser__entry--selected'));
                el.classList.add('dir-browser__entry--selected');
                this.browsePath = entry.path;
                this.elDirBrowserPath.textContent = entry.path;
            });

            // Double click navigates into
            el.addEventListener('dblclick', () => {
                this.browseDirectory(entry.path);
            });

            this.elDirBrowserList.appendChild(el);
        }
    }

    async handleCreateWorkspace() {
        const name = this.elWsName.value.trim();
        if (!name) {
            this.elWsName.focus();
            return;
        }

        try {
            const ws = await this.apiFetch('/api/workspaces', {
                method: 'POST',
                body: JSON.stringify({
                    name,
                    path: this.elWsPath.value.trim(),
                    backend: this.elWsBackend.value,
                }),
            });

            this.closeNewWorkspaceDialog();
            await this.fetchWorkspaces();
            this.switchWorkspace(ws.workspace_id);
        } catch (e) {
            console.error('Failed to create workspace:', e);
            alert(`Failed to create workspace: ${e.message}`);
        }
    }

    async deleteWorkspace(workspaceId) {
        if (workspaceId === 'global') return;
        if (!confirm('Delete this workspace? Sessions will be unscoped.')) return;

        try {
            await this.apiFetch(`/api/workspaces/${workspaceId}`, { method: 'DELETE' });
            await this.fetchWorkspaces();
            if (this.activeWorkspaceId === workspaceId) {
                this.switchWorkspace('global');
            } else {
                this.render();
            }
        } catch (e) {
            console.error('Failed to delete workspace:', e);
        }
    }

    // ---- Session Actions ----

    async createSession() {
        try {
            const data = await this.apiFetch('/api/sessions', {
                method: 'POST',
                body: JSON.stringify({
                    workspace: this.activeWorkspaceName(),
                    workspace_id: this.activeWorkspaceId,
                }),
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

    async selectSession(sessionId) {
        this.currentSessionId = sessionId;
        this.switchView('conversation');
        this.elAboutSection.style.display = 'none';
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

    async searchInbox() {
        const q = this.elInboxSearch.value.trim();
        try {
            const data = await this.apiFetch(`/api/inbox/search?q=${encodeURIComponent(q)}`);
            this.renderInboxItems(data.items || []);
        } catch (e) {
            console.error('Inbox search failed:', e);
        }
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
        this.renderWorkspaceTabs();
        this.renderSidebar();
        this.renderWorkspaceLabel();
    }

    // Active workspace helpers

    activeWorkspace() {
        return this.workspaces.find(ws => ws.workspace_id === this.activeWorkspaceId) || null;
    }

    activeWorkspaceName() {
        const ws = this.activeWorkspace();
        return ws ? ws.name : 'Global';
    }

    // Workspace tab bar

    renderWorkspaceTabs() {
        // Update global tab active state
        this.elTabGlobal.classList.toggle('workspace-tab--active', this.activeWorkspaceId === 'global');

        // Render dynamic project tabs
        this.elDynamicTabs.innerHTML = '';

        const projects = this.workspaces.filter(ws => ws.type === 'project');
        for (const ws of projects) {
            const tab = document.createElement('button');
            tab.className = 'workspace-tab';
            tab.dataset.wsId = ws.workspace_id;
            if (ws.workspace_id === this.activeWorkspaceId) {
                tab.classList.add('workspace-tab--active');
            }

            tab.innerHTML = `
                <span class="workspace-tab__icon">📁</span>
                <span class="workspace-tab__label">${this.escapeHtml(ws.name)}</span>
                <span class="workspace-tab__close" title="Close workspace">✕</span>
            `;

            tab.addEventListener('click', (e) => {
                if (e.target.closest('.workspace-tab__close')) {
                    e.stopPropagation();
                    this.deleteWorkspace(ws.workspace_id);
                } else {
                    this.switchWorkspace(ws.workspace_id);
                }
            });

            this.elDynamicTabs.appendChild(tab);
        }
    }

    renderWorkspaceLabel() {
        const name = this.activeWorkspaceName();
        this.elCurrentWorkspace.textContent = name;
    }

    renderSidebar() {
        this.renderSidebarWorkspaces();
        this.renderPlaygroundSessions();
    }

    renderSidebarWorkspaces() {
        this.elWorkspaceList.innerHTML = '';

        const projects = this.workspaces.filter(ws => ws.type === 'project');
        for (const ws of projects) {
            const el = document.createElement('div');
            el.className = 'sidebar__workspace-item';
            if (ws.workspace_id === this.activeWorkspaceId) {
                el.classList.add('sidebar__workspace-item--active');
            }

            const backend = ws.backend || 'local';
            const isSSH = backend !== 'local';

            el.innerHTML = `
                <span class="sidebar__workspace-chevron">▸</span>
                <span class="sidebar__workspace-status sidebar__workspace-status--connected"></span>
                <span class="sidebar__workspace-name">${this.escapeHtml(ws.name)}</span>
                ${isSSH
                    ? '<span class="sidebar__workspace-type sidebar__workspace-type--ssh">SSH</span>'
                    : ''
                }
                <button class="sidebar__workspace-add-btn" title="New conversation">+</button>
            `;

            el.addEventListener('click', () => this.switchWorkspace(ws.workspace_id));

            const addBtn = el.querySelector('.sidebar__workspace-add-btn');
            if (addBtn) {
                addBtn.addEventListener('click', (e) => {
                    e.stopPropagation();
                    this.activeWorkspaceId = ws.workspace_id;
                    this.createSession();
                });
            }

            this.elWorkspaceList.appendChild(el);
        }
    }

    renderPlaygroundSessions() {
        this.elPlaygroundList.innerHTML = '';

        // Show sessions scoped to the active workspace
        const targetWsId = this.activeWorkspaceId;
        const filtered = this.sessions.filter(s => {
            const wsId = (s.metadata && s.metadata.workspace_id) || 'global';
            return wsId === targetWsId;
        });

        for (const session of filtered) {
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
