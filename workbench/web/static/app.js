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
        this.currentView = 'inbox'; // 'conversation' | 'inbox'
        this.searchDebounceTimer = null;

        // Streaming state
        this.isStreaming = false;
        this.followAlong = true;

        // Multi-window state
        this.activeWindow = 'inbox';

        // Sub-modules (loaded from separate files)
        this.agentHud = new AgentHud(this);
        this.contextBar = new ContextBar(this);
        this.triageWindow = new TriageWindow(this);
        this.recipeWindow = new RecipeWindow(this);

        // Track which workspaces are expanded/closed in sidebar
        this.expandedWorkspaces = new Set();
        this.closedWorkspaces = new Set();

        this.init();
    }

    async init() {
        this.bindElements();
        this.bindEvents();
        await this.fetchCSRFToken();
        await Promise.all([
            this.fetchWorkspaces(),
            this.fetchSessions(),
            this.fetchProviders(),
        ]);
        this.render();
        this.agentHud.start();
        await this.contextBar.init();
        this.triageWindow.bindEvents();
        this.recipeWindow.bindEvents();
        this.initSidebarResize();
        // Default to inbox view on load
        this.switchView('inbox');
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

        // Settings
        this.elBtnSettings = document.getElementById('btnSettings');
        this.elSettingsOverlay = document.getElementById('settingsOverlay');
        this.elBtnCloseSettings = document.getElementById('btnCloseSettings');
        this.elSettingsContent = document.getElementById('settingsContent');
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
            if (e.key === 'Escape') {
                this.closeSettings();
                this.closeOverlay('knowledgeOverlay');
                this.closeOverlay('browserOverlay');
                this.closeOverlay('feedbackOverlay');
                return;
            }
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
                } else if (e.key === ',' ) {
                    e.preventDefault();
                    this.openSettings();
                }
            }
        });

        // Settings panel (gear in top bar + sidebar nav)
        this.elBtnSettings.addEventListener('click', () => this.openSettings());
        this.elBtnCloseSettings.addEventListener('click', () => this.closeSettings());
        this.elSettingsOverlay.addEventListener('click', (e) => {
            if (e.target === this.elSettingsOverlay) this.closeSettings();
        });
        // Settings tab navigation
        this.elSettingsOverlay.querySelectorAll('[data-settings-tab]').forEach(btn => {
            btn.addEventListener('click', () => this.switchSettingsTab(btn.dataset.settingsTab));
        });

        // Sidebar bottom nav — overlays
        document.getElementById('navSettings').addEventListener('click', () => this.openSettings());
        document.getElementById('navKnowledge').addEventListener('click', () => this.openOverlay('knowledgeOverlay'));
        document.getElementById('navBrowser').addEventListener('click', () => this.openOverlay('browserOverlay'));
        document.getElementById('navFeedback').addEventListener('click', () => this.openOverlay('feedbackOverlay'));

        // Generic overlay close buttons (data-close-overlay attribute)
        document.querySelectorAll('[data-close-overlay]').forEach(btn => {
            btn.addEventListener('click', () => this.closeOverlay(btn.dataset.closeOverlay));
        });
        // Click backdrop to close generic overlays
        document.querySelectorAll('.panel-overlay').forEach(overlay => {
            overlay.addEventListener('click', (e) => {
                if (e.target === overlay) this.closeOverlay(overlay.id);
            });
        });

        // Start conversation (under active workspace)
        this.elStartBtn.addEventListener('click', () => this.createSession());
        // Playground "+" always creates under global
        this.elNewPlayground.addEventListener('click', () => {
            this.activeWorkspaceId = 'global';
            this.createSession();
        });

        // Send message (or stop if streaming)
        this.elSendBtn.addEventListener('click', () => {
            if (this.isStreaming && this._streamAbortController) {
                this._streamAbortController.abort();
            } else {
                this.sendMessage();
            }
        });
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

        // Follow along: disengage on manual scroll up, re-engage at bottom
        if (this.elMessages) {
            this.elMessages.addEventListener('scroll', () => {
                if (this.isStreaming) {
                    const el = this.elMessages;
                    const isAtBottom = el.scrollHeight - el.scrollTop - el.clientHeight < 50;
                    this.followAlong = isAtBottom;
                    this.updateFollowAlongButton();
                }
            });
        }

        // Follow along button click
        const followBtn = document.getElementById('btnFollowAlong');
        if (followBtn) {
            followBtn.addEventListener('click', () => {
                this.followAlong = !this.followAlong;
                this.updateFollowAlongButton();
                if (this.followAlong) this.scrollToBottom();
            });
        }

        // Window tabs
        document.querySelectorAll('.window-tab[data-window]').forEach(tab => {
            tab.addEventListener('click', () => this.switchWindow(tab.dataset.window));
        });

        // Workspace tabs
        this.elTabGlobal.addEventListener('click', () => this.switchWorkspace('global'));
        this.elBtnNewWorkspace.addEventListener('click', () => this.openNewWorkspaceDialog());
        const elNewWsSection = document.getElementById('btnNewWorkspaceSection');
        if (elNewWsSection) {
            elNewWsSection.addEventListener('click', () => this.openNewWorkspaceDialog());
        }
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

    async fetchProviders() {
        try {
            const data = await this.apiFetch('/api/providers');
            const select = document.getElementById('modelSelect');
            if (select && data.providers && data.providers.length > 0) {
                select.innerHTML = '';
                for (const p of data.providers) {
                    const opt = document.createElement('option');
                    opt.value = p;
                    opt.textContent = p;
                    opt.selected = (p === data.active);
                    select.appendChild(opt);
                }
            }
        } catch (e) {
            console.warn('Could not fetch providers:', e);
        }
    }

    // ---- Workspace Actions ----

    async switchWorkspace(workspaceId) {
        // Toggle expand/collapse if clicking the already-active workspace
        if (this.expandedWorkspaces.has(workspaceId)) {
            this.expandedWorkspaces.delete(workspaceId);
        } else {
            this.expandedWorkspaces.add(workspaceId);
        }

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

        // Reload context pills for the new workspace
        if (this.contextBar) {
            this.contextBar.loadPills();
        }
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
            // Ensure new workspace is visible and expanded
            this.closedWorkspaces.delete(ws.workspace_id);
            this.expandedWorkspaces.add(ws.workspace_id);
            this.switchWorkspace(ws.workspace_id);
        } catch (e) {
            console.error('Failed to create workspace:', e);
            alert(`Failed to create workspace: ${e.message}`);
        }
    }

    closeWorkspace(workspaceId) {
        if (workspaceId === 'global') return;
        // Just remove from visible UI — workspace data and sessions are preserved
        this.expandedWorkspaces.delete(workspaceId);
        this.closedWorkspaces.add(workspaceId);
        if (this.activeWorkspaceId === workspaceId) {
            this.activeWorkspaceId = 'global';
            this.currentSessionId = null;
            this.elMessages.innerHTML = '';
            this.elAboutSection.style.display = '';
        }
        this.render();
    }

    reopenWorkspace(workspaceId) {
        this.closedWorkspaces.delete(workspaceId);
        this.expandedWorkspaces.add(workspaceId);
        this.activeWorkspaceId = workspaceId;
        this.render();
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
            // Auto-expand the workspace so the new conversation is visible
            if (this.activeWorkspaceId !== 'global') {
                this.expandedWorkspaces.add(this.activeWorkspaceId);
            }
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

        if (!this.currentSessionId) return;

        await this.sendMessageStreaming();
    }

    async sendMessageStreaming() {
        const content = this.elMessageInput.value.trim();
        if (!content || !this.currentSessionId) return;

        // Track the user message for promote-to-recipe
        this._lastUserMessage = content;

        // Clear input and show user message
        this.elMessageInput.value = '';
        this.elMessageInput.style.height = 'auto';
        this.elSendBtn.classList.remove('input-box__send-btn--active');
        this.appendMessage('user', content);
        this.elAboutSection.style.display = 'none';

        // Create assistant message container (will be filled progressively)
        const assistantDiv = this.createAssistantMessageContainer();
        const contentEl = assistantDiv.querySelector('.message__content');

        // Show streaming indicator
        this._streamAbortController = new AbortController();
        this.setStreaming(true);

        try {
            const response = await fetch(`/api/sessions/${this.currentSessionId}/stream`, {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                    'X-CSRF-Token': this.csrfToken,
                    ...(this.authToken ? {'Authorization': `Bearer ${this.authToken}`} : {}),
                },
                body: JSON.stringify({ content }),
                signal: this._streamAbortController.signal,
            });

            if (!response.ok) {
                const err = await response.json();
                this.appendMessage('error', err.error || 'Failed to send message');
                return;
            }

            const reader = response.body.getReader();
            const decoder = new TextDecoder();
            let buffer = '';

            while (true) {
                const { done, value } = await reader.read();
                if (done) break;

                buffer += decoder.decode(value, { stream: true });
                const lines = buffer.split('\n');
                buffer = lines.pop() || '';  // Keep incomplete line in buffer

                let eventType = '';
                let eventData = '';

                for (const line of lines) {
                    if (line.startsWith('event: ')) {
                        eventType = line.slice(7).trim();
                    } else if (line.startsWith('data: ')) {
                        eventData = line.slice(6);
                        // Process complete event
                        if (eventType && eventData) {
                            try {
                                const data = JSON.parse(eventData);
                                this.handleSSEEvent(eventType, data, contentEl, assistantDiv);
                            } catch (e) {
                                console.warn('Failed to parse SSE data:', eventData);
                            }
                        }
                        eventType = '';
                        eventData = '';
                    } else if (line.trim() === '') {
                        // Empty line = event boundary, reset
                        eventType = '';
                        eventData = '';
                    }
                }
            }
        } catch (err) {
            if (err.name === 'AbortError') {
                // User stopped the stream — not an error
            } else {
                console.error('Streaming error:', err);
                this.appendMessage('error', 'Connection lost during streaming');
            }
        } finally {
            this._streamAbortController = null;
            this.setStreaming(false);
        }
    }

    handleSSEEvent(type, data, contentEl, assistantDiv) {
        switch (type) {
            case 'text_delta':
                // Accumulate raw text; use lightweight render while streaming
                if (!contentEl._rawText) contentEl._rawText = '';
                contentEl._rawText += data.delta || '';
                contentEl.innerHTML = this.renderStreamingText(contentEl._rawText);
                if (this.followAlong) this.scrollToBottom();
                break;

            case 'tool_call_start':
                this.renderToolCallCard(assistantDiv, data.tool_call_id, data.name, data.args);
                if (this.followAlong) this.scrollToBottom();
                break;

            case 'tool_call_result':
                this.updateToolCallResult(data.tool_call_id, data.content, data.success, data.error);
                if (this.followAlong) this.scrollToBottom();
                break;

            case 'confirmation_required':
                this.showConfirmationPrompt(assistantDiv, data.tool_call_id, data.tool_name, data.args);
                if (this.followAlong) this.scrollToBottom();
                break;

            case 'error':
                const errorDiv = document.createElement('div');
                errorDiv.className = 'message__error';
                errorDiv.textContent = data.message || 'Unknown error';
                assistantDiv.appendChild(errorDiv);
                break;

            case 'done':
                // Streaming complete — do full markdown render on final text
                if (contentEl._rawText) {
                    contentEl.innerHTML = this.renderMarkdownLite(contentEl._rawText);
                }
                // Inject "Save as Recipe" if tool calls were made
                const toolGroup = assistantDiv.querySelector('.tool-call-group');
                if (toolGroup) {
                    const saveBtn = document.createElement('button');
                    saveBtn.className = 'btn-save-recipe';
                    saveBtn.textContent = 'Save as Recipe';
                    saveBtn.addEventListener('click', (e) => {
                        e.stopPropagation();
                        this.promoteToRecipe(assistantDiv);
                    });
                    toolGroup.querySelector('.tool-call-group__summary').appendChild(saveBtn);
                }
                break;
        }
    }

    createAssistantMessageContainer() {
        const div = document.createElement('div');
        div.className = 'message message--assistant';

        const avatar = document.createElement('div');
        avatar.className = 'message__avatar';
        avatar.textContent = 'Agent';

        const contentWrapper = document.createElement('div');
        contentWrapper.className = 'message__body';

        const content = document.createElement('div');
        content.className = 'message__content';

        contentWrapper.appendChild(content);
        div.appendChild(avatar);
        div.appendChild(contentWrapper);

        this.elMessages.appendChild(div);
        return div;
    }

    /**
     * Get or create a collapsible tool-call group inside an assistant message.
     * All tool calls for a single assistant turn are grouped together.
     */
    getToolCallGroup(parentEl) {
        const body = parentEl.querySelector('.message__body') || parentEl;
        let group = body.querySelector('.tool-call-group');
        if (group) return group;

        group = document.createElement('div');
        group.className = 'tool-call-group';
        group.dataset.total = '0';
        group.dataset.completed = '0';

        const summary = document.createElement('div');
        summary.className = 'tool-call-group__summary';
        summary.innerHTML = `
            <span class="tool-call-group__icon">&#9881;</span>
            <span class="tool-call-group__label">1 tool call</span>
            <span class="tool-call-group__status tool-call-group__status--running">running</span>
            <button class="tool-call-group__toggle" aria-label="Toggle tool calls">&#9656;</button>
        `;

        const items = document.createElement('div');
        items.className = 'tool-call-group__items';
        items.style.display = 'none';

        summary.addEventListener('click', () => {
            const isHidden = items.style.display === 'none';
            items.style.display = isHidden ? 'block' : 'none';
            summary.querySelector('.tool-call-group__toggle').innerHTML = isHidden ? '&#9662;' : '&#9656;';
            group.classList.toggle('tool-call-group--expanded', isHidden);
        });

        group.appendChild(summary);
        group.appendChild(items);
        body.appendChild(group);
        return group;
    }

    updateToolCallGroupSummary(group) {
        const total = parseInt(group.dataset.total) || 0;
        const completed = parseInt(group.dataset.completed) || 0;
        const hasError = group.querySelector('.tool-call-card--error') !== null;

        const label = group.querySelector('.tool-call-group__label');
        const status = group.querySelector('.tool-call-group__status');

        label.textContent = `${total} tool call${total !== 1 ? 's' : ''}`;

        if (completed >= total) {
            if (hasError) {
                status.textContent = 'completed with errors';
                status.className = 'tool-call-group__status tool-call-group__status--error';
            } else {
                status.textContent = 'all completed';
                status.className = 'tool-call-group__status tool-call-group__status--success';
            }
        } else {
            status.textContent = `${completed}/${total} completed`;
            status.className = 'tool-call-group__status tool-call-group__status--running';
        }
    }

    renderToolCallCard(parentEl, toolCallId, name, args) {
        const group = this.getToolCallGroup(parentEl);
        group.dataset.total = (parseInt(group.dataset.total) || 0) + 1;
        this.updateToolCallGroupSummary(group);

        const card = document.createElement('div');
        card.className = 'tool-call-card';
        card.id = `tool-call-${toolCallId}`;

        const header = document.createElement('div');
        header.className = 'tool-call-card__header';
        header.innerHTML = `
            <span class="tool-call-card__icon">&#9881;</span>
            <span class="tool-call-card__name">${this.escapeHtml(name)}</span>
            <span class="tool-call-card__status tool-call-card__status--running">Running...</span>
            <button class="tool-call-card__toggle" aria-label="Toggle details">&#9656;</button>
        `;

        const details = document.createElement('div');
        details.className = 'tool-call-card__details';
        details.style.display = 'none';

        const argsBlock = document.createElement('pre');
        argsBlock.className = 'tool-call-card__args';
        argsBlock.textContent = JSON.stringify(args, null, 2);
        details.appendChild(argsBlock);

        // Toggle details on click
        header.querySelector('.tool-call-card__toggle').addEventListener('click', (e) => {
            e.stopPropagation();
            const isHidden = details.style.display === 'none';
            details.style.display = isHidden ? 'block' : 'none';
            header.querySelector('.tool-call-card__toggle').innerHTML = isHidden ? '&#9662;' : '&#9656;';
        });

        card.appendChild(header);
        card.appendChild(details);

        group.querySelector('.tool-call-group__items').appendChild(card);
    }

    updateToolCallResult(toolCallId, content, success, error) {
        const card = document.getElementById(`tool-call-${toolCallId}`);
        if (!card) return;

        const statusEl = card.querySelector('.tool-call-card__status');
        if (success) {
            statusEl.textContent = 'Completed';
            statusEl.className = 'tool-call-card__status tool-call-card__status--success';
            card.classList.add('tool-call-card--success');
        } else {
            statusEl.textContent = error || 'Failed';
            statusEl.className = 'tool-call-card__status tool-call-card__status--error';
            card.classList.add('tool-call-card--error');
        }

        // Add result to details
        const details = card.querySelector('.tool-call-card__details');
        if (details && content) {
            const resultBlock = document.createElement('div');
            resultBlock.className = 'tool-call-card__result';

            const resultLabel = document.createElement('div');
            resultLabel.className = 'tool-call-card__result-label';
            resultLabel.textContent = 'Result:';

            const resultContent = document.createElement('pre');
            resultContent.className = 'tool-call-card__result-content';
            resultContent.textContent = content;

            resultBlock.appendChild(resultLabel);
            resultBlock.appendChild(resultContent);
            details.appendChild(resultBlock);
        }

        // Update group summary
        const group = card.closest('.tool-call-group');
        if (group) {
            group.dataset.completed = (parseInt(group.dataset.completed) || 0) + 1;
            this.updateToolCallGroupSummary(group);
        }
    }

    showConfirmationPrompt(parentEl, toolCallId, toolName, args) {
        const prompt = document.createElement('div');
        prompt.className = 'confirmation-prompt';
        prompt.id = `confirm-${toolCallId}`;

        prompt.innerHTML = `
            <div class="confirmation-prompt__header">
                <span class="confirmation-prompt__icon">&#9888;</span>
                <span class="confirmation-prompt__title">Confirmation Required</span>
            </div>
            <div class="confirmation-prompt__body">
                <div class="confirmation-prompt__tool">${this.escapeHtml(toolName)}</div>
                <pre class="confirmation-prompt__args">${this.escapeHtml(JSON.stringify(args, null, 2))}</pre>
            </div>
            <div class="confirmation-prompt__actions">
                <button class="confirmation-prompt__btn confirmation-prompt__btn--allow">Allow</button>
                <button class="confirmation-prompt__btn confirmation-prompt__btn--deny">Deny</button>
            </div>
        `;

        // Wire buttons
        prompt.querySelector('.confirmation-prompt__btn--allow').addEventListener('click', () => {
            this.resolveConfirmation(toolCallId, true);
            prompt.remove();
        });
        prompt.querySelector('.confirmation-prompt__btn--deny').addEventListener('click', () => {
            this.resolveConfirmation(toolCallId, false);
            prompt.remove();
        });

        const body = parentEl.querySelector('.message__body');
        if (body) {
            body.appendChild(prompt);
        } else {
            parentEl.appendChild(prompt);
        }
    }

    async resolveConfirmation(toolCallId, confirmed) {
        try {
            await fetch(`/api/sessions/${this.currentSessionId}/confirm`, {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                    'X-CSRF-Token': this.csrfToken,
                    ...(this.authToken ? {'Authorization': `Bearer ${this.authToken}`} : {}),
                },
                body: JSON.stringify({ tool_call_id: toolCallId, confirmed }),
            });
        } catch (e) {
            console.error('Failed to resolve confirmation:', e);
        }
    }

    setStreaming(active) {
        this.isStreaming = active;
        const indicator = document.getElementById('streamingIndicator');
        if (indicator) {
            indicator.style.display = active ? 'flex' : 'none';
        }
        // Toggle Send ↔ Stop
        if (this.elSendBtn) {
            if (active) {
                this.elSendBtn.textContent = '■';
                this.elSendBtn.title = 'Stop';
                this.elSendBtn.classList.add('input-box__send-btn--stop');
            } else {
                this.elSendBtn.textContent = '➤';
                this.elSendBtn.title = 'Send';
                this.elSendBtn.classList.remove('input-box__send-btn--stop');
            }
        }
        this.updateFollowAlongButton();
    }

    updateFollowAlongButton() {
        const btn = document.getElementById('btnFollowAlong');
        if (!btn) return;
        btn.classList.toggle('follow-along--active', this.followAlong);
        btn.style.display = this.isStreaming ? 'flex' : 'none';
    }

    scrollToBottom() {
        if (this.elMessages) {
            this.elMessages.scrollTop = this.elMessages.scrollHeight;
        }
    }

    async selectSession(sessionId) {
        this.currentSessionId = sessionId;

        // Set active workspace to match the session's workspace
        const session = this.sessions.find(s => s.session_id === sessionId);
        if (session) {
            const wsId = (session.metadata && session.metadata.workspace_id) || 'global';
            this.activeWorkspaceId = wsId;
            if (wsId !== 'global') {
                this.expandedWorkspaces.add(wsId);
            }
        }

        // Switch to inbox window — unless chat is reparented (e.g. triage embed)
        if (!this._chatOriginalParent) {
            if (this.activeWindow !== 'inbox') {
                this.switchWindow('inbox');
            }
            this.switchView('conversation');
        }
        this.elAboutSection.style.display = 'none';
        this.elMessages.innerHTML = '';

        const detail = await this.fetchSessionDetail(sessionId);
        if (detail && detail.events) {
            this.renderSessionEvents(detail.events);
        }

        this.render();
    }

    renderSessionEvents(events) {
        this.elMessages.innerHTML = '';

        let currentAssistantDiv = null;

        for (const event of events) {
            const type = event.event_type || event.type;
            const payload = event.payload || event;

            switch (type) {
                case 'user_message':
                    currentAssistantDiv = null;
                    this.appendMessage('user', payload.content || payload.text || '');
                    break;

                case 'assistant_message':
                    currentAssistantDiv = this.createAssistantMessageContainer();
                    const content = currentAssistantDiv.querySelector('.message__content');
                    content.innerHTML = this.renderMarkdownLite(payload.content || payload.text || '');
                    break;

                case 'tool_call_request':
                    if (currentAssistantDiv) {
                        this.renderToolCallCard(
                            currentAssistantDiv,
                            payload.tool_call_id || payload.id,
                            payload.tool_name || payload.name,
                            payload.arguments || payload.args || {}
                        );
                    }
                    break;

                case 'tool_call_result':
                    this.updateToolCallResult(
                        payload.tool_call_id || payload.id,
                        payload.content || '',
                        payload.success !== false,
                        payload.error
                    );
                    break;

                case 'confirmation':
                    // Show resolved confirmation inline
                    if (currentAssistantDiv) {
                        const badge = document.createElement('div');
                        badge.className = `confirmation-badge confirmation-badge--${payload.confirmed ? 'allowed' : 'denied'}`;
                        badge.textContent = payload.confirmed ? 'Confirmed' : 'Denied';
                        const body = currentAssistantDiv.querySelector('.message__body');
                        if (body) body.appendChild(badge);
                    }
                    break;
            }
        }

        this.scrollToBottom();
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

    /**
     * Move the conversation view DOM into a different container.
     * Used by Triage to embed chat inline without duplicating logic.
     * Call returnChat() to move it back to the Inbox window.
     */
    reparentChat(targetContainerId) {
        const conv = document.getElementById('conversationView');
        const target = document.getElementById(targetContainerId);
        if (conv && target) {
            this._chatOriginalParent = conv.parentElement;
            target.appendChild(conv);
        }
    }

    /**
     * Return the conversation view to its original parent (Inbox window).
     */
    returnChat() {
        const conv = document.getElementById('conversationView');
        if (conv && this._chatOriginalParent) {
            this._chatOriginalParent.appendChild(conv);
            this._chatOriginalParent = null;
        }
    }

    // ---- Settings Panel ----

    openSettings() {
        this.elSettingsOverlay.style.display = 'flex';
    }

    closeSettings() {
        this.elSettingsOverlay.style.display = 'none';
    }

    switchSettingsTab(tabName) {
        // Update nav active state
        this.elSettingsOverlay.querySelectorAll('[data-settings-tab]').forEach(btn => {
            btn.classList.toggle('settings-panel__nav-item--active', btn.dataset.settingsTab === tabName);
        });
        // Show/hide tab content
        const idMap = { general: 'settingsTabGeneral', llm: 'settingsTabLlm', agents: 'settingsTabAgents', integrations: 'settingsTabIntegrations', policy: 'settingsTabPolicy' };
        Object.entries(idMap).forEach(([key, id]) => {
            const el = document.getElementById(id);
            if (el) el.style.display = key === tabName ? '' : 'none';
        });
    }

    // ---- Generic Overlays (Knowledge, Browser, Feedback) ----

    openOverlay(overlayId) {
        const el = document.getElementById(overlayId);
        if (el) el.style.display = 'flex';
    }

    closeOverlay(overlayId) {
        const el = document.getElementById(overlayId);
        if (el) el.style.display = 'none';
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

        const projects = this.workspaces.filter(ws => ws.type === 'project' && !this.closedWorkspaces.has(ws.workspace_id));
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
                    this.closeWorkspace(ws.workspace_id);
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

        const projects = this.workspaces.filter(ws => ws.type === 'project' && !this.closedWorkspaces.has(ws.workspace_id));
        for (const ws of projects) {
            const isExpanded = this.expandedWorkspaces.has(ws.workspace_id);
            const isActive = ws.workspace_id === this.activeWorkspaceId;

            // Workspace header row
            const el = document.createElement('div');
            el.className = 'sidebar__workspace-item';
            if (isActive) {
                el.classList.add('sidebar__workspace-item--active');
            }

            const backend = ws.backend || 'local';
            const isSSH = backend !== 'local';

            el.innerHTML = `
                <span class="sidebar__workspace-chevron${isExpanded ? ' sidebar__workspace-chevron--expanded' : ''}">▸</span>
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
                    // Auto-expand the workspace when creating a conversation in it
                    this.expandedWorkspaces.add(ws.workspace_id);
                    this.createSession();
                });
            }

            this.elWorkspaceList.appendChild(el);

            // Nested conversations (only when expanded)
            if (isExpanded) {
                const wsSessions = this.sessions.filter(s => {
                    const wsId = (s.metadata && s.metadata.workspace_id) || 'global';
                    return wsId === ws.workspace_id;
                });

                const container = document.createElement('div');
                container.className = 'sidebar__workspace-sessions';

                for (const session of wsSessions) {
                    const sEl = document.createElement('div');
                    sEl.className = 'sidebar__conversation-item sidebar__conversation-item--nested';
                    if (session.session_id === this.currentSessionId) {
                        sEl.classList.add('sidebar__conversation-item--active');
                    }

                    const title = this.sessionLabel(session);

                    // Show a spinning indicator if streaming on this session
                    const isStreaming = this.isStreaming && session.session_id === this.currentSessionId;
                    const indicator = isStreaming
                        ? '<span class="sidebar__session-indicator sidebar__session-indicator--active" title="Streaming">●</span>'
                        : '';

                    sEl.innerHTML = `<span class="sidebar__conversation-title">${this.escapeHtml(title)}</span>${indicator}`;

                    sEl.addEventListener('click', (e) => {
                        e.stopPropagation();
                        this.selectSession(session.session_id);
                    });
                    container.appendChild(sEl);
                }

                this.elWorkspaceList.appendChild(container);
            }
        }
    }

    renderPlaygroundSessions() {
        this.elPlaygroundList.innerHTML = '';

        // Only show sessions that belong to the global/playground workspace
        const filtered = this.sessions.filter(s => {
            const wsId = (s.metadata && s.metadata.workspace_id) || 'global';
            return wsId === 'global';
        });

        for (const session of filtered) {
            const el = document.createElement('div');
            el.className = 'sidebar__conversation-item';
            if (session.session_id === this.currentSessionId) {
                el.classList.add('sidebar__conversation-item--active');
            }

            const title = this.sessionLabel(session);

            el.innerHTML = `<span class="sidebar__conversation-title">${this.escapeHtml(title)}</span>`;

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

    renderStreamingText(text) {
        // Lightweight render for in-progress streaming — handles line breaks and code blocks
        // but skips bold/italic since tokens arrive incrementally and markers are incomplete
        const esc = (s) => this.escapeHtml(s);

        // Handle completed code blocks
        text = text.replace(/```(\w*)\n([\s\S]*?)```/g, (_, lang, code) => {
            return `<pre class="md-code-block"><code class="md-lang-${esc(lang || 'text')}">${esc(code.replace(/\n$/, ''))}</code></pre>`;
        });

        // Handle in-progress code block (still streaming)
        text = text.replace(/```(\w*)\n([\s\S]*)$/g, (_, lang, code) => {
            return `<pre class="md-code-block md-code-block--streaming"><code class="md-lang-${esc(lang || 'text')}">${esc(code)}</code></pre>`;
        });

        // Split remaining text into lines, escape and join with <br>
        // (don't try to parse bold/italic/headers mid-stream)
        const parts = text.split(/(<pre[\s\S]*?<\/pre>)/g);
        return parts.map(part => {
            if (part.startsWith('<pre')) return part; // already rendered code block
            return esc(part).replace(/\n/g, '<br>');
        }).join('');
    }

    renderMarkdownLite(text) {
        // Lightweight markdown renderer — no dependencies
        // Handles: code blocks, inline code, headers, bold, italic, lists, links, line breaks
        const esc = (s) => this.escapeHtml(s);

        // Extract fenced code blocks first to protect them from other processing
        const codeBlocks = [];
        text = text.replace(/```(\w*)\n([\s\S]*?)```/g, (_, lang, code) => {
            const idx = codeBlocks.length;
            codeBlocks.push(`<pre class="md-code-block"><code class="md-lang-${esc(lang || 'text')}">${esc(code.replace(/\n$/, ''))}</code></pre>`);
            return `\x00CODEBLOCK${idx}\x00`;
        });

        // Handle incomplete code blocks (still streaming)
        text = text.replace(/```(\w*)\n([\s\S]*)$/g, (_, lang, code) => {
            const idx = codeBlocks.length;
            codeBlocks.push(`<pre class="md-code-block md-code-block--streaming"><code class="md-lang-${esc(lang || 'text')}">${esc(code)}</code></pre>`);
            return `\x00CODEBLOCK${idx}\x00`;
        });

        // Extract inline code
        const inlineCode = [];
        text = text.replace(/`([^`]+)`/g, (_, code) => {
            const idx = inlineCode.length;
            inlineCode.push(`<code class="md-inline-code">${esc(code)}</code>`);
            return `\x00INLINE${idx}\x00`;
        });

        // Process line by line
        const lines = text.split('\n');
        const result = [];
        let inList = false;

        for (let line of lines) {
            // Check for code block placeholder
            const blockMatch = line.match(/^\x00CODEBLOCK(\d+)\x00$/);
            if (blockMatch) {
                if (inList) { result.push('</ul>'); inList = false; }
                result.push(codeBlocks[parseInt(blockMatch[1])]);
                continue;
            }

            // Escape HTML in normal lines
            line = esc(line);

            // Restore inline code placeholders (already escaped inside)
            line = line.replace(/\x00INLINE(\d+)\x00/g, (_, idx) => inlineCode[parseInt(idx)]);

            // Headers
            if (/^#{4,6}\s/.test(line)) {
                if (inList) { result.push('</ul>'); inList = false; }
                const level = line.match(/^(#+)/)[1].length;
                line = line.replace(/^#+\s+/, '');
                result.push(`<h${level} class="md-heading">${line}</h${level}>`);
                continue;
            }
            if (/^###\s/.test(line)) {
                if (inList) { result.push('</ul>'); inList = false; }
                result.push(`<h3 class="md-heading">${line.replace(/^###\s+/, '')}</h3>`);
                continue;
            }
            if (/^##\s/.test(line)) {
                if (inList) { result.push('</ul>'); inList = false; }
                result.push(`<h2 class="md-heading">${line.replace(/^##\s+/, '')}</h2>`);
                continue;
            }
            if (/^#\s/.test(line)) {
                if (inList) { result.push('</ul>'); inList = false; }
                result.push(`<h1 class="md-heading">${line.replace(/^#\s+/, '')}</h1>`);
                continue;
            }

            // Unordered lists
            if (/^[\s]*[-*]\s/.test(line)) {
                if (!inList) { result.push('<ul class="md-list">'); inList = true; }
                result.push(`<li>${line.replace(/^[\s]*[-*]\s+/, '')}</li>`);
                continue;
            }

            // Ordered lists
            if (/^[\s]*\d+\.\s/.test(line)) {
                if (!inList) { result.push('<ol class="md-list">'); inList = true; }
                result.push(`<li>${line.replace(/^[\s]*\d+\.\s+/, '')}</li>`);
                continue;
            }

            if (inList) { result.push('</ul>'); inList = false; }

            // Bold and italic
            line = line.replace(/\*\*\*(.+?)\*\*\*/g, '<strong><em>$1</em></strong>');
            line = line.replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>');
            line = line.replace(/\*(.+?)\*/g, '<em>$1</em>');

            // Links
            line = line.replace(/\[([^\]]+)\]\(([^)]+)\)/g, '<a href="$2" target="_blank" rel="noopener">$1</a>');

            // Empty line = paragraph break
            if (line.trim() === '') {
                result.push('<br>');
                continue;
            }

            result.push(`<p class="md-paragraph">${line}</p>`);
        }

        if (inList) result.push('</ul>');

        return result.join('\n');
    }

    // ---- Window Switching ----

    switchWindow(windowName) {
        this.activeWindow = windowName;

        // Toggle window containers
        document.querySelectorAll('.window').forEach(w => {
            if (w.dataset.window === windowName) {
                w.style.display = 'flex';
                w.classList.add('window--active');
            } else {
                w.style.display = 'none';
                w.classList.remove('window--active');
            }
        });

        // Toggle window tab active state
        document.querySelectorAll('.window-tab').forEach(tab => {
            tab.classList.toggle('window-tab--active', tab.dataset.window === windowName);
        });

        // Activate/deactivate window-specific logic
        if (windowName === 'triage') {
            this.triageWindow.activate();
        } else {
            this.triageWindow.deactivate();
        }
        if (windowName === 'recipes') {
            this.recipeWindow.activate();
        } else {
            this.recipeWindow.deactivate();
        }

        // Reset inbox to list view when switching to it
        if (windowName === 'inbox') {
            this.switchView('inbox');
        }
    }

    promoteToRecipe(assistantDiv) {
        // Collect tool names from tool call cards
        const tools = new Set();
        assistantDiv.querySelectorAll('.tool-call-card__name').forEach(el => {
            tools.add(el.textContent.trim());
        });

        // Get description from assistant text
        const contentEl = assistantDiv.querySelector('.message__content');
        const description = (contentEl?._rawText || contentEl?.textContent || '').slice(0, 120).trim();

        const prefill = {
            prompt_template: this._lastUserMessage || '',
            tools: [...tools],
            description,
        };

        this.switchWindow('recipes');
        this.recipeWindow.showCreateForm(prefill);
    }

    sessionLabel(session) {
        // Use first user message as title
        if (session.last_message) {
            const msg = session.last_message.trim();
            if (msg.length > 40) return msg.substring(0, 40) + '...';
            return msg;
        }
        // Investigation sessions
        if (session.metadata && session.metadata.investigation_id) {
            return 'Investigation chat';
        }
        // Workspace name + relative time
        const ws = session.metadata && session.metadata.workspace;
        const age = this.formatDate(session.created_at);
        if (ws && ws !== 'global') return `${ws} — ${age}`;
        return `New conversation — ${age}`;
    }

    initSidebarResize() {
        const handle = document.getElementById('sidebarResizeHandle');
        const sidebar = document.getElementById('sidebar');
        const layout = document.querySelector('.layout');
        if (!handle || !sidebar || !layout) return;

        let startX, startWidth;

        const onMouseMove = (e) => {
            const delta = e.clientX - startX;
            const newWidth = Math.max(160, Math.min(480, startWidth + delta));
            sidebar.style.width = newWidth + 'px';
        };

        const onMouseUp = () => {
            handle.classList.remove('layout__resize-handle--active');
            layout.classList.remove('layout--resizing');
            document.removeEventListener('mousemove', onMouseMove);
            document.removeEventListener('mouseup', onMouseUp);
        };

        handle.addEventListener('mousedown', (e) => {
            e.preventDefault();
            startX = e.clientX;
            startWidth = sidebar.getBoundingClientRect().width;
            handle.classList.add('layout__resize-handle--active');
            layout.classList.add('layout--resizing');
            document.addEventListener('mousemove', onMouseMove);
            document.addEventListener('mouseup', onMouseUp);
        });
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
