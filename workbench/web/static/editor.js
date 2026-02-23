/**
 * EditorWindow — Ace-based file editor with markdown preview.
 *
 * Provides file tree browsing, tabbed editing, and live markdown preview.
 * Depends on Ace editor and markdown-it (loaded as vendor scripts).
 */

class EditorWindow {
    constructor(app) {
        this.app = app;
        this.ace = null;
        this.md = null;
        this.initialized = false;
        this.currentPath = null;        // directory being browsed
        this.openFiles = [];            // [{path, name, content, language, modified, dirty}]
        this.activeFileIndex = -1;
        this._previewDebounce = null;
        this.showHidden = localStorage.getItem('editor_show_hidden') === 'true';
    }

    init() {
        if (this.initialized) return;
        this.initialized = true;

        // Init markdown-it
        if (typeof markdownit !== 'undefined') {
            this.md = markdownit({
                html: true,
                linkify: true,
                typographer: true,
            });
        }

        // Init Ace
        const editorEl = document.getElementById('editorAce');
        if (!editorEl || typeof ace === 'undefined') return;

        ace.config.set('basePath', '/static/vendor/ace');
        this.ace = ace.edit('editorAce', {
            theme: 'ace/theme/one_dark',
            fontSize: 13,
            fontFamily: "'SF Mono', 'Fira Code', 'Consolas', monospace",
            showPrintMargin: false,
            tabSize: 4,
            useSoftTabs: true,
            wrap: true,
            showGutter: true,
            highlightActiveLine: true,
        });

        // Cursor position tracking
        this.ace.selection.on('changeCursor', () => {
            const pos = this.ace.getCursorPosition();
            const el = document.getElementById('editorStatusPos');
            if (el) el.textContent = `Ln ${pos.row + 1}, Col ${pos.column + 1}`;
        });

        // Mark dirty on change + live preview update
        this.ace.on('change', () => {
            if (this.activeFileIndex >= 0) {
                const file = this.openFiles[this.activeFileIndex];
                if (!file.dirty) {
                    file.dirty = true;
                    this.renderTabs();
                }
            }
            // Live preview update (instant)
            const preview = document.getElementById('editorPreview');
            if (preview && preview.style.display !== 'none') {
                this.renderPreview();
            }
        });

        // Ctrl+S save
        this.ace.commands.addCommand({
            name: 'save',
            bindKey: { win: 'Ctrl-S', mac: 'Cmd-S' },
            exec: () => this.saveFile(),
        });

        // Wire resize handles
        this._initSidebarResize();
        this._initSplitResize();

        // Wire new file button
        const btnNew = document.getElementById('editorBtnNewFile');
        if (btnNew) {
            btnNew.addEventListener('click', () => this.createNewFile());
        }

        // Wire go-up button
        const btnUp = document.getElementById('editorBtnUp');
        if (btnUp) {
            btnUp.addEventListener('click', () => this.goUp());
        }

        // Wire show-hidden toggle
        const btnHidden = document.getElementById('editorBtnHidden');
        if (btnHidden) {
            btnHidden.classList.toggle('editor-window__sidebar-btn--active', this.showHidden);
            btnHidden.addEventListener('click', () => this.toggleHidden());
        }

        // Load initial path
        this.loadFileTree(this._getWorkspacePath());
    }

    _getWorkspacePath() {
        // Use active workspace path if available
        if (this.app.workspaces) {
            const ws = this.app.workspaces.find(w => w.id === this.app.activeWorkspaceId);
            if (ws && ws.path) return ws.path;
        }
        return '~';
    }

    // ---- File Tree ----

    async loadFileTree(dirPath) {
        this.currentPath = dirPath;
        const tree = document.getElementById('editorFileTree');
        if (!tree) return;

        try {
            const qs = `path=${encodeURIComponent(dirPath)}${this.showHidden ? '&show_hidden=true' : ''}`;
            const data = await this.app.apiFetch(`/api/files?${qs}`);
            this.currentPath = data.path;
            tree.innerHTML = '';

            // Parent dir link
            if (data.parent) {
                const up = document.createElement('div');
                up.className = 'editor-file-item editor-file-item--dir';
                up.innerHTML = '<span class="editor-file-item__icon">📁</span><span class="editor-file-item__name">..</span>';
                up.addEventListener('click', () => this.loadFileTree(data.parent));
                tree.appendChild(up);
            }

            for (const entry of data.entries) {
                const item = document.createElement('div');
                item.className = `editor-file-item editor-file-item--${entry.type}`;

                const icon = entry.type === 'dir' ? '📁' : this._fileIcon(entry.name);
                item.innerHTML = `<span class="editor-file-item__icon">${icon}</span><span class="editor-file-item__name">${this.app.escapeHtml(entry.name)}</span>`;

                if (entry.type === 'dir') {
                    item.addEventListener('click', () => this.loadFileTree(entry.path));
                } else {
                    item.addEventListener('click', () => this.openFile(entry.path, entry.name));
                    // Highlight if currently open
                    if (this.activeFileIndex >= 0 && this.openFiles[this.activeFileIndex].path === entry.path) {
                        item.classList.add('editor-file-item--active');
                    }
                }

                tree.appendChild(item);
            }

            // Update sidebar title
            const title = document.querySelector('.editor-window__sidebar-title');
            if (title) {
                const short = data.path.replace(/^\/home\/[^/]+/, '~');
                title.textContent = short;
                title.title = data.path;
            }
        } catch (e) {
            tree.innerHTML = `<div class="editor-file-item editor-file-item--error">Error: ${this.app.escapeHtml(e.message || String(e))}</div>`;
        }
    }

    goUp() {
        if (this.currentPath) {
            const parent = this.currentPath.replace(/\/[^/]+$/, '') || '/';
            this.loadFileTree(parent);
        }
    }

    toggleHidden() {
        this.showHidden = !this.showHidden;
        localStorage.setItem('editor_show_hidden', this.showHidden);
        const btn = document.getElementById('editorBtnHidden');
        if (btn) btn.classList.toggle('editor-window__sidebar-btn--active', this.showHidden);
        if (this.currentPath) this.loadFileTree(this.currentPath);
    }

    _fileIcon(name) {
        const ext = name.split('.').pop().toLowerCase();
        const icons = {
            md: '📝', yaml: '⚙', yml: '⚙', json: '{ }',
            py: '🐍', js: '📜', ts: '📜', css: '🎨',
            html: '🌐', sh: '💻', txt: '📄', log: '📄',
        };
        return icons[ext] || '📄';
    }

    // ---- File Create/Open/Save ----

    async createNewFile() {
        if (!this.currentPath) return;
        const name = prompt('New file name (e.g. notes.md):');
        if (!name || !name.trim()) return;
        let clean = name.trim();
        if (/[/\0]/.test(clean) || clean === '.' || clean === '..') {
            alert('Invalid file name');
            return;
        }
        const filePath = this.currentPath + '/' + clean;
        try {
            await this.app.apiFetch('/api/files/write', {
                method: 'PUT',
                body: JSON.stringify({ path: filePath, content: '' }),
            });
            this.loadFileTree(this.currentPath);
            this.openFile(filePath, clean, { editMode: true });
        } catch (e) {
            console.error('Failed to create file:', e);
            alert('Failed to create file: ' + (e.message || e));
        }
    }

    async openFile(filePath, fileName, opts = {}) {
        // Check if already open
        const existing = this.openFiles.findIndex(f => f.path === filePath);
        if (existing >= 0) {
            this.switchToTab(existing, opts);
            return;
        }

        try {
            const data = await this.app.apiFetch(`/api/files/read?path=${encodeURIComponent(filePath)}`);

            this.openFiles.push({
                path: data.path,
                name: fileName || data.path.split('/').pop(),
                content: data.content,
                language: data.language,
                modified: data.modified,
                dirty: false,
            });

            this.switchToTab(this.openFiles.length - 1, opts);
        } catch (e) {
            console.error('Failed to open file:', e);
        }
    }

    switchToTab(index, opts = {}) {
        if (index < 0 || index >= this.openFiles.length) return;

        // Save current content if switching away
        if (this.activeFileIndex >= 0 && this.ace) {
            this.openFiles[this.activeFileIndex].content = this.ace.getValue();
        }

        this.activeFileIndex = index;
        const file = this.openFiles[index];

        // Load into Ace
        if (this.ace) {
            this.ace.setValue(file.content, -1);
            this.ace.session.setMode(`ace/mode/${file.language || 'text'}`);
            this.ace.focus();
        }

        // Update status bar
        const statusFile = document.getElementById('editorStatusFile');
        const statusLang = document.getElementById('editorStatusLang');
        if (statusFile) statusFile.textContent = file.path.replace(/^\/home\/[^/]+/, '~');
        if (statusLang) statusLang.textContent = file.language || 'text';

        // Layout: always code + preview side by side
        const aceEl = document.getElementById('editorAce');
        const preview = document.getElementById('editorPreview');
        const splitHandle = document.getElementById('editorSplitHandle');
        if (aceEl) { aceEl.style.flex = '1'; aceEl.style.width = ''; aceEl.style.display = 'block'; }
        if (preview) { preview.style.display = 'flex'; this.renderPreview(); }
        if (splitHandle) splitHandle.style.display = 'block';
        if (this.ace) this.ace.resize();

        this.renderTabs();

        // Re-render file tree to highlight active
        if (this.currentPath) {
            this.loadFileTree(this.currentPath);
        }
    }

    async saveFile() {
        if (this.activeFileIndex < 0) return;
        const file = this.openFiles[this.activeFileIndex];
        if (!this.ace) return;

        file.content = this.ace.getValue();

        try {
            await this.app.apiFetch('/api/files/write', {
                method: 'PUT',
                body: JSON.stringify({ path: file.path, content: file.content }),
            });
            file.dirty = false;
            this.renderTabs();

            // Brief flash on status bar
            const statusFile = document.getElementById('editorStatusFile');
            if (statusFile) {
                const orig = statusFile.textContent;
                statusFile.textContent = 'Saved!';
                setTimeout(() => { statusFile.textContent = orig; }, 1000);
            }
        } catch (e) {
            console.error('Failed to save:', e);
        }
    }

    closeTab(index) {
        const file = this.openFiles[index];
        if (file.dirty) {
            if (!confirm(`${file.name} has unsaved changes. Close anyway?`)) return;
        }

        this.openFiles.splice(index, 1);

        if (this.openFiles.length === 0) {
            this.activeFileIndex = -1;
            if (this.ace) this.ace.setValue('', -1);
            const statusFile = document.getElementById('editorStatusFile');
            const statusLang = document.getElementById('editorStatusLang');
            if (statusFile) statusFile.textContent = 'No file open';
            if (statusLang) statusLang.textContent = '';
            const preview = document.getElementById('editorPreview');
            if (preview) preview.style.display = 'none';
            this.renderTabs();
        } else if (index <= this.activeFileIndex) {
            this.switchToTab(Math.max(0, this.activeFileIndex - 1));
        } else {
            this.renderTabs();
        }
    }

    // ---- Tabs ----

    renderTabs() {
        const container = document.getElementById('editorTabs');
        if (!container) return;

        container.innerHTML = '';
        for (let i = 0; i < this.openFiles.length; i++) {
            const file = this.openFiles[i];
            const tab = document.createElement('div');
            tab.className = 'editor-tab' + (i === this.activeFileIndex ? ' editor-tab--active' : '');

            const label = document.createElement('span');
            label.className = 'editor-tab__label';
            label.textContent = (file.dirty ? '● ' : '') + file.name;
            label.addEventListener('click', () => this.switchToTab(i));

            const close = document.createElement('span');
            close.className = 'editor-tab__close';
            close.textContent = '×';
            close.addEventListener('click', (e) => {
                e.stopPropagation();
                this.closeTab(i);
            });

            tab.appendChild(label);
            tab.appendChild(close);
            container.appendChild(tab);
        }

        // Right-side actions
        if (this.activeFileIndex >= 0) {
            const actions = document.createElement('div');
            actions.className = 'editor-tab__actions';

            // Code + Preview toggles
            const aceEl = document.getElementById('editorAce');
            const preview = document.getElementById('editorPreview');
            const codeVisible = aceEl && aceEl.style.display !== 'none';
            const previewVisible = preview && preview.style.display !== 'none';

            const codeToggle = document.createElement('div');
            codeToggle.className = 'editor-tab editor-tab--toggle';
            codeToggle.textContent = codeVisible ? 'Hide Code' : 'Show Code';
            codeToggle.addEventListener('click', () => this.toggleCode());
            actions.appendChild(codeToggle);

            const previewToggle = document.createElement('div');
            previewToggle.className = 'editor-tab editor-tab--toggle';
            previewToggle.textContent = previewVisible ? 'Hide Preview' : 'Show Preview';
            previewToggle.addEventListener('click', () => this.togglePreview());
            actions.appendChild(previewToggle);

            // Save button (shows when dirty)
            const file = this.openFiles[this.activeFileIndex];
            if (file.dirty) {
                const saveBtn = document.createElement('div');
                saveBtn.className = 'editor-tab editor-tab--save';
                saveBtn.textContent = 'Save';
                saveBtn.title = 'Ctrl+S';
                saveBtn.addEventListener('click', () => this.saveFile());
                actions.appendChild(saveBtn);
            }

            container.appendChild(actions);
        }
    }

    // ---- Markdown Preview ----

    _isMarkdown() {
        if (this.activeFileIndex < 0) return false;
        const lang = this.openFiles[this.activeFileIndex].language;
        return lang === 'markdown';
    }

    renderPreview() {
        if (!this.md || !this.ace) return;
        const content = this.ace.getValue();
        const html = this.md.render(content);
        const el = document.getElementById('editorPreviewContent');
        if (el) el.innerHTML = html;
    }

    toggleCode() {
        const aceEl = document.getElementById('editorAce');
        const splitHandle = document.getElementById('editorSplitHandle');
        const preview = document.getElementById('editorPreview');
        if (!aceEl) return;

        if (aceEl.style.display === 'none') {
            aceEl.style.flex = '1';
            aceEl.style.width = '';
            aceEl.style.display = 'block';
            if (splitHandle && preview && preview.style.display !== 'none') splitHandle.style.display = 'block';
            if (this.ace) this.ace.resize();
            this.ace.focus();
        } else {
            aceEl.style.display = 'none';
            if (splitHandle) splitHandle.style.display = 'none';
        }
        this.renderTabs();
    }

    togglePreview() {
        const preview = document.getElementById('editorPreview');
        const splitHandle = document.getElementById('editorSplitHandle');
        const aceEl = document.getElementById('editorAce');
        if (!preview) return;

        if (preview.style.display === 'none') {
            preview.style.display = 'flex';
            this.renderPreview();
            if (splitHandle && aceEl && aceEl.style.display !== 'none') splitHandle.style.display = 'block';
        } else {
            preview.style.display = 'none';
            if (splitHandle) splitHandle.style.display = 'none';
        }
        if (this.ace) this.ace.resize();
        this.renderTabs();
    }

    // ---- Sidebar Resize ----

    _initSplitResize() {
        const handle = document.getElementById('editorSplitHandle');
        if (!handle) return;

        handle.addEventListener('mousedown', (e) => {
            e.preventDefault();
            const aceEl = document.getElementById('editorAce');
            const preview = document.getElementById('editorPreview');
            const area = document.querySelector('.editor-window__editor-area');
            if (!aceEl || !preview || !area) return;

            const startX = e.clientX;
            const areaWidth = area.offsetWidth;
            const startAceWidth = aceEl.offsetWidth;

            handle.classList.add('editor-window__split-handle--active');

            const onMove = (ev) => {
                const delta = ev.clientX - startX;
                const newAceWidth = Math.max(100, Math.min(areaWidth - 100, startAceWidth + delta));
                aceEl.style.flex = 'none';
                aceEl.style.width = newAceWidth + 'px';
                preview.style.flex = '1';
                if (this.ace) this.ace.resize();
            };

            const onUp = () => {
                handle.classList.remove('editor-window__split-handle--active');
                document.removeEventListener('mousemove', onMove);
                document.removeEventListener('mouseup', onUp);
            };

            document.addEventListener('mousemove', onMove);
            document.addEventListener('mouseup', onUp);
        });
    }

    _initSidebarResize() {
        const handle = document.getElementById('editorResizeHandle');
        if (!handle) return;

        handle.addEventListener('mousedown', (e) => {
            e.preventDefault();
            const sidebar = document.getElementById('editorSidebar');
            if (!sidebar) return;

            const startX = e.clientX;
            const startWidth = sidebar.offsetWidth;

            const onMove = (ev) => {
                const delta = ev.clientX - startX;
                const newWidth = Math.max(140, Math.min(500, startWidth + delta));
                sidebar.style.width = newWidth + 'px';
            };

            const onUp = () => {
                document.removeEventListener('mousemove', onMove);
                document.removeEventListener('mouseup', onUp);
                if (this.ace) this.ace.resize();
            };

            document.addEventListener('mousemove', onMove);
            document.addEventListener('mouseup', onUp);
        });
    }
}
