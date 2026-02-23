/**
 * Recipe Window — browse and run recipes.
 *
 * Two-panel layout:
 *   Left:  Recipe list with search + tag filter
 *   Right: Recipe detail, parameter form, run output
 */

class RecipeWindow {
    constructor(app) {
        this.app = app;
        this.recipes = [];
        this.activeRecipe = null;
        this.running = false;
        this.abortController = null;
    }

    activate() {
        this.fetchRecipes();
    }

    deactivate() {
        if (this.running) {
            this.stopRecipe();
        }
    }

    bindEvents() {
        const search = document.getElementById('recipeSearch');
        if (search) {
            search.addEventListener('input', () => this.renderList());
        }

        const filter = document.getElementById('recipeTagFilter');
        if (filter) {
            filter.addEventListener('change', () => this.fetchRecipes());
        }

        const newBtn = document.getElementById('btnNewRecipe');
        if (newBtn) {
            newBtn.addEventListener('click', () => this.showCreateForm());
        }
    }

    // ---- Data ----

    async fetchRecipes() {
        const wsId = this.app.activeWorkspaceId || 'global';
        const filter = document.getElementById('recipeTagFilter');
        const tag = filter ? filter.value : '';

        try {
            const url = `/api/workspaces/${wsId}/recipes` + (tag ? `?tag=${encodeURIComponent(tag)}` : '');
            const headers = {};
            if (this.app.authToken) headers['Authorization'] = `Bearer ${this.app.authToken}`;

            const resp = await fetch(url, { headers });
            if (!resp.ok) throw new Error(`HTTP ${resp.status}`);

            const data = await resp.json();
            this.recipes = data.recipes || [];
        } catch (err) {
            console.error('Failed to fetch recipes:', err);
            this.recipes = [];
        }

        this.populateTagFilter();
        this.renderList();
    }

    populateTagFilter() {
        const filter = document.getElementById('recipeTagFilter');
        if (!filter) return;

        const currentVal = filter.value;
        const allTags = new Set();
        for (const r of this.recipes) {
            for (const t of (r.tags || [])) allTags.add(t);
        }

        // Keep "All Tags" option, rebuild the rest
        filter.innerHTML = '<option value="">All Tags</option>';
        for (const t of [...allTags].sort()) {
            const opt = document.createElement('option');
            opt.value = t;
            opt.textContent = t;
            if (t === currentVal) opt.selected = true;
            filter.appendChild(opt);
        }
    }

    // ---- Rendering: List ----

    renderList() {
        const container = document.getElementById('recipeList');
        if (!container) return;

        const searchEl = document.getElementById('recipeSearch');
        const query = searchEl ? searchEl.value.toLowerCase().trim() : '';

        let filtered = this.recipes;
        if (query) {
            filtered = filtered.filter(r =>
                (r.name || '').toLowerCase().includes(query) ||
                (r.description || '').toLowerCase().includes(query) ||
                (r.tags || []).some(t => t.toLowerCase().includes(query))
            );
        }

        if (filtered.length === 0) {
            container.innerHTML = `<div class="recipe-list__empty">${
                query ? 'No recipes match your search.' : 'No recipes found. Add recipes to ~/.workbench/recipes/'
            }</div>`;
            return;
        }

        container.innerHTML = '';
        for (const recipe of filtered) {
            const card = this.buildCard(recipe);
            card.addEventListener('click', () => this.selectRecipe(recipe.name));
            container.appendChild(card);
        }
    }

    buildCard(recipe) {
        const card = document.createElement('div');
        card.className = 'recipe-card' +
            (this.activeRecipe && this.activeRecipe.name === recipe.name ? ' recipe-card--active' : '');
        card.dataset.recipeName = recipe.name;

        const header = document.createElement('div');
        header.className = 'recipe-card__header';
        header.innerHTML = `
            <span class="recipe-card__name">${this.esc(recipe.name)}</span>
            <span class="recipe-card__version">v${this.esc(recipe.version || '1.0')}</span>
        `;
        card.appendChild(header);

        if (recipe.description) {
            const desc = document.createElement('div');
            desc.className = 'recipe-card__description';
            desc.textContent = recipe.description;
            card.appendChild(desc);
        }

        if (recipe.tags && recipe.tags.length) {
            const tags = document.createElement('div');
            tags.className = 'recipe-card__tags';
            for (const t of recipe.tags) {
                const pill = document.createElement('span');
                pill.className = 'recipe-tag';
                pill.textContent = t;
                tags.appendChild(pill);
            }
            card.appendChild(tags);
        }

        return card;
    }

    // ---- Rendering: Detail ----

    async selectRecipe(name) {
        const wsId = this.app.activeWorkspaceId || 'global';

        try {
            const headers = {};
            if (this.app.authToken) headers['Authorization'] = `Bearer ${this.app.authToken}`;

            const resp = await fetch(`/api/workspaces/${wsId}/recipes/${encodeURIComponent(name)}`, { headers });
            if (!resp.ok) throw new Error(`HTTP ${resp.status}`);

            this.activeRecipe = await resp.json();
        } catch (err) {
            console.error('Failed to fetch recipe details:', err);
            return;
        }

        this.renderList();  // Update active state
        this.renderDetail();
    }

    renderDetail() {
        const emptyState = document.getElementById('recipeEmptyState');
        const detailView = document.getElementById('recipeDetailView');
        if (!this.activeRecipe) {
            if (emptyState) emptyState.style.display = '';
            if (detailView) detailView.style.display = 'none';
            return;
        }

        if (emptyState) emptyState.style.display = 'none';
        if (detailView) detailView.style.display = '';

        const r = this.activeRecipe;
        let html = '';

        // Header
        html += `<div class="recipe-detail__header">`;
        html += `<div class="recipe-detail__title-row">`;
        html += `<span class="recipe-detail__name">${this.esc(r.name)}</span>`;
        html += `<span class="recipe-detail__version">v${this.esc(r.version || '1.0')}</span>`;
        html += `</div>`;
        if (r.description) {
            html += `<div class="recipe-detail__description">${this.esc(r.description)}</div>`;
        }
        html += `</div>`;

        // Meta: tags + tools
        html += `<div class="recipe-detail__meta">`;
        if (r.tags && r.tags.length) {
            html += `<div class="recipe-detail__meta-group">`;
            html += `<span class="recipe-detail__meta-label">Tags</span>`;
            html += `<div class="recipe-detail__meta-value">`;
            for (const t of r.tags) {
                html += `<span class="recipe-tag">${this.esc(t)}</span>`;
            }
            html += `</div></div>`;
        }
        if (r.tools && r.tools.length) {
            html += `<div class="recipe-detail__meta-group">`;
            html += `<span class="recipe-detail__meta-label">Tools</span>`;
            html += `<div class="recipe-detail__meta-value">`;
            for (const t of r.tools) {
                html += `<span class="recipe-detail__tool-badge">${this.esc(t)}</span>`;
            }
            html += `</div></div>`;
        }
        html += `</div>`;

        // Parameters form
        const params = r.parameters || [];
        if (params.length) {
            html += `<div class="recipe-detail__section-title">Parameters</div>`;
            html += `<div class="recipe-param-form" id="recipeParamForm">`;
            for (const p of params) {
                html += this.buildParamField(p);
            }
            html += `</div>`;
        }

        // Run button
        html += `<div class="recipe-detail__actions">`;
        html += `<button class="recipe-run-btn" id="btnRunRecipe">Run Recipe</button>`;
        html += `<button class="recipe-deploy-btn" id="btnDeployRecipe">Deploy Agent</button>`;
        html += `<button class="recipe-edit-btn" id="btnEditRecipe">Edit</button>`;
        html += `<button class="recipe-stop-btn" id="btnStopRecipe" style="display:none">Stop</button>`;
        html += `</div>`;

        // Output area
        html += `<div class="recipe-detail__output" id="recipeOutputSection" style="display:none">`;
        html += `<div class="recipe-detail__output-header">`;
        html += `<span class="recipe-detail__output-title">Output</span>`;
        html += `<span class="recipe-detail__output-status" id="recipeOutputStatus"></span>`;
        html += `</div>`;
        html += `<div class="recipe-output__stream" id="recipeOutputStream"></div>`;
        html += `</div>`;

        detailView.innerHTML = html;

        // Bind run/stop/deploy/edit buttons
        const btnRun = document.getElementById('btnRunRecipe');
        const btnStop = document.getElementById('btnStopRecipe');
        const btnDeploy = document.getElementById('btnDeployRecipe');
        const btnEdit = document.getElementById('btnEditRecipe');
        if (btnRun) btnRun.addEventListener('click', () => this.runRecipe());
        if (btnStop) btnStop.addEventListener('click', () => this.stopRecipe());
        if (btnDeploy) btnDeploy.addEventListener('click', () => this.deployRecipe());
        if (btnEdit) btnEdit.addEventListener('click', () => this.showCreateForm(this.activeRecipe, true));
    }

    buildParamField(param) {
        const id = `recipeParam_${param.name}`;
        const required = param.required ? '<span class="recipe-param__required">*</span>' : '';
        let html = `<div class="recipe-param">`;
        html += `<label class="recipe-param__label" for="${id}">${this.esc(param.name)}${required}</label>`;

        if (param.description) {
            html += `<div class="recipe-param__description">${this.esc(param.description)}</div>`;
        }

        if (param.type === 'choice' && param.choices && param.choices.length) {
            html += `<select class="recipe-param__select" id="${id}" data-param-name="${this.esc(param.name)}">`;
            for (const c of param.choices) {
                const selected = c === param.default ? ' selected' : '';
                html += `<option value="${this.esc(c)}"${selected}>${this.esc(c)}</option>`;
            }
            html += `</select>`;
        } else if (param.type === 'bool') {
            const checked = param.default ? ' checked' : '';
            html += `<div class="recipe-param__checkbox-row">`;
            html += `<input type="checkbox" class="recipe-param__checkbox" id="${id}" data-param-name="${this.esc(param.name)}"${checked}>`;
            html += `<label for="${id}">Enabled</label>`;
            html += `</div>`;
        } else {
            const placeholder = param.default ? `Default: ${param.default}` : '';
            const inputType = (param.type === 'int' || param.type === 'float') ? 'number' : 'text';
            html += `<input type="${inputType}" class="recipe-param__input" id="${id}" ` +
                `data-param-name="${this.esc(param.name)}" ` +
                `placeholder="${this.esc(placeholder)}" ` +
                `value="${this.esc(param.default || '')}">`;
        }

        html += `</div>`;
        return html;
    }

    collectParams() {
        const params = {};
        const form = document.getElementById('recipeParamForm');
        if (!form) return params;

        form.querySelectorAll('[data-param-name]').forEach(el => {
            const name = el.dataset.paramName;
            if (el.type === 'checkbox') {
                params[name] = el.checked ? 'true' : 'false';
            } else {
                const val = el.value.trim();
                if (val) params[name] = val;
            }
        });

        return params;
    }

    // ---- Execution ----

    async deployRecipe() {
        if (!this.activeRecipe) return;

        const wsId = this.app.activeWorkspaceId || 'global';
        const name = this.activeRecipe.name;
        const parameters = this.collectParams();

        const btn = document.getElementById('btnDeployRecipe');
        if (btn) {
            btn.disabled = true;
            btn.textContent = 'Deploying…';
        }

        try {
            const result = await this.app.apiFetch(
                `/api/workspaces/${wsId}/recipes/${encodeURIComponent(name)}/deploy`,
                { method: 'POST', body: JSON.stringify({ parameters }) }
            );

            // Open Agent Activity panel so user can see it running
            if (this.app.agentHud) {
                this.app.agentHud.openPanel();
            }

            // Flash the button green briefly
            if (btn) {
                btn.textContent = 'Deployed!';
                btn.classList.add('recipe-deploy-btn--success');
                setTimeout(() => {
                    btn.disabled = false;
                    btn.textContent = 'Deploy Agent';
                    btn.classList.remove('recipe-deploy-btn--success');
                }, 2000);
            }
        } catch (e) {
            console.error('Deploy failed:', e);
            if (btn) {
                btn.disabled = false;
                btn.textContent = 'Deploy Agent';
            }
            alert(`Deploy failed: ${e.message}`);
        }
    }

    async runRecipe() {
        if (!this.activeRecipe || this.running) return;

        const wsId = this.app.activeWorkspaceId || 'global';
        const name = this.activeRecipe.name;
        const parameters = this.collectParams();

        // Show output area, swap buttons
        const outputSection = document.getElementById('recipeOutputSection');
        const outputStream = document.getElementById('recipeOutputStream');
        const outputStatus = document.getElementById('recipeOutputStatus');
        const btnRun = document.getElementById('btnRunRecipe');
        const btnStop = document.getElementById('btnStopRecipe');

        if (outputSection) outputSection.style.display = '';
        if (outputStream) outputStream.textContent = '';
        if (outputStatus) {
            outputStatus.className = 'recipe-detail__output-status recipe-detail__output-status--running';
            outputStatus.textContent = 'Running...';
        }
        if (btnRun) btnRun.style.display = 'none';
        if (btnStop) btnStop.style.display = '';

        this.running = true;
        this.abortController = new AbortController();

        try {
            const headers = {
                'Content-Type': 'application/json',
                'X-CSRF-Token': this.app.csrfToken,
            };
            if (this.app.authToken) headers['Authorization'] = `Bearer ${this.app.authToken}`;

            const resp = await fetch(`/api/workspaces/${wsId}/recipes/${encodeURIComponent(name)}/run`, {
                method: 'POST',
                headers,
                body: JSON.stringify({ parameters }),
                signal: this.abortController.signal,
            });

            if (!resp.ok) {
                const err = await resp.json().catch(() => ({ error: `HTTP ${resp.status}` }));
                throw new Error(err.detail || err.error || `HTTP ${resp.status}`);
            }

            // Read SSE stream
            const reader = resp.body.getReader();
            const decoder = new TextDecoder();
            let buffer = '';

            while (true) {
                const { done, value } = await reader.read();
                if (done) break;

                buffer += decoder.decode(value, { stream: true });
                const lines = buffer.split('\n');
                buffer = lines.pop() || '';

                let eventType = '';
                let eventData = '';

                for (const line of lines) {
                    if (line.startsWith('event: ')) {
                        eventType = line.slice(7).trim();
                    } else if (line.startsWith('data: ')) {
                        eventData = line.slice(6);
                        if (eventType && eventData) {
                            try {
                                const data = JSON.parse(eventData);
                                this.handleRecipeEvent(eventType, data, outputStream);
                            } catch (e) {
                                console.warn('Failed to parse recipe SSE:', eventData);
                            }
                        }
                        eventType = '';
                        eventData = '';
                    } else if (line.trim() === '') {
                        eventType = '';
                        eventData = '';
                    }
                }
            }

            // Done
            if (outputStatus) {
                outputStatus.className = 'recipe-detail__output-status recipe-detail__output-status--done';
                outputStatus.textContent = 'Complete';
            }
        } catch (err) {
            if (err.name === 'AbortError') {
                if (outputStatus) {
                    outputStatus.className = 'recipe-detail__output-status recipe-detail__output-status--done';
                    outputStatus.textContent = 'Stopped';
                }
            } else {
                console.error('Recipe execution error:', err);
                if (outputStatus) {
                    outputStatus.className = 'recipe-detail__output-status recipe-detail__output-status--error';
                    outputStatus.textContent = 'Error';
                }
                if (outputStream) {
                    outputStream.textContent += `\nError: ${err.message}`;
                }
            }
        } finally {
            this.running = false;
            this.abortController = null;
            if (btnRun) btnRun.style.display = '';
            if (btnStop) btnStop.style.display = 'none';
        }
    }

    handleRecipeEvent(type, data, outputEl) {
        if (!outputEl) return;

        switch (type) {
            case 'text_delta':
                outputEl.textContent += data.delta || '';
                outputEl.scrollTop = outputEl.scrollHeight;
                break;

            case 'tool_call_start': {
                const div = document.createElement('div');
                div.className = 'recipe-output__tool-call';
                div.id = `recipe-tc-${data.tool_call_id}`;
                div.innerHTML = `<span class="recipe-output__tool-name">${this.esc(data.name)}</span>`;
                outputEl.appendChild(div);
                outputEl.scrollTop = outputEl.scrollHeight;
                break;
            }

            case 'tool_call_result': {
                const tcEl = document.getElementById(`recipe-tc-${data.tool_call_id}`);
                if (tcEl) {
                    const resultDiv = document.createElement('div');
                    resultDiv.className = 'recipe-output__tool-result';
                    const content = typeof data.content === 'string'
                        ? data.content
                        : JSON.stringify(data.content, null, 2);
                    // Truncate long results
                    resultDiv.textContent = content.length > 500
                        ? content.slice(0, 500) + '...'
                        : content;
                    tcEl.appendChild(resultDiv);
                }
                outputEl.scrollTop = outputEl.scrollHeight;
                break;
            }

            case 'error':
                outputEl.textContent += `\nError: ${data.message || 'Unknown error'}`;
                outputEl.scrollTop = outputEl.scrollHeight;
                break;

            case 'done':
                // Stream finished
                break;
        }
    }

    stopRecipe() {
        if (this.abortController) {
            this.abortController.abort();
        }
    }

    // ---- Creation / Editor ----

    async showCreateForm(prefill = null, isEditing = false) {
        if (!isEditing) this.activeRecipe = null;
        this.editorMode = 'form';  // 'form' or 'yaml'
        this.editorPrefill = prefill;
        this._isEditing = isEditing;

        // Fetch available tools for the tool picker
        if (!this._availableTools) {
            try {
                const headers = {};
                if (this.app.authToken) headers['Authorization'] = `Bearer ${this.app.authToken}`;
                const resp = await fetch('/api/tools', { headers });
                if (resp.ok) {
                    const data = await resp.json();
                    this._availableTools = data.tools || [];
                }
            } catch (e) {
                console.warn('Failed to fetch tools:', e);
            }
            if (!this._availableTools) this._availableTools = [];
        }

        this.renderList();
        this.renderEditor(prefill);
    }

    renderEditor(prefill = null) {
        const emptyState = document.getElementById('recipeEmptyState');
        const detailView = document.getElementById('recipeDetailView');
        if (emptyState) emptyState.style.display = 'none';
        if (detailView) {
            detailView.style.display = '';
            detailView.innerHTML = '';
        }

        const editor = document.createElement('div');
        editor.className = 'recipe-editor';
        editor.id = 'recipeEditor';

        // Header with mode toggle
        editor.innerHTML = `
            <div class="recipe-editor__header">
                <span class="recipe-editor__title">${this._isEditing ? 'Edit Recipe' : 'New Recipe'}</span>
                <div class="recipe-editor__mode-toggle">
                    <button class="recipe-editor__mode-btn recipe-editor__mode-btn--active" data-editor-mode="form">Form</button>
                    <button class="recipe-editor__mode-btn" data-editor-mode="yaml">YAML</button>
                </div>
            </div>
            <div id="recipeEditorFormContainer"></div>
            <div class="recipe-editor__yaml" id="recipeEditorYamlContainer">
                <textarea class="recipe-editor__yaml-textarea" id="recipeYamlTextarea" spellcheck="false"></textarea>
            </div>
            <div class="recipe-editor__error" id="recipeEditorError"></div>
            <div class="recipe-editor__actions">
                <button class="recipe-editor__cancel-btn" id="btnCancelRecipe">Cancel</button>
                <button class="recipe-editor__save-btn" id="btnSaveRecipe">Save Recipe</button>
            </div>
        `;

        detailView.appendChild(editor);

        // Build form
        this.renderEditorForm(prefill);

        // Bind events
        editor.querySelectorAll('[data-editor-mode]').forEach(btn => {
            btn.addEventListener('click', () => this.toggleEditorMode(btn.dataset.editorMode));
        });
        document.getElementById('btnCancelRecipe').addEventListener('click', () => this.cancelCreate());
        document.getElementById('btnSaveRecipe').addEventListener('click', () => this.saveRecipe());
    }

    renderEditorForm(prefill = null) {
        const container = document.getElementById('recipeEditorFormContainer');
        if (!container) return;

        const p = prefill || {};
        const selectedTools = new Set(p.tools || []);

        let html = `<div class="recipe-editor__form" id="recipeEditorForm">`;

        // Name
        const nameReadonly = this._isEditing ? 'readonly style="opacity:0.6;cursor:not-allowed"' : '';
        const nameHint = this._isEditing
            ? '<span class="recipe-editor__hint">Name cannot be changed while editing</span>'
            : '<span class="recipe-editor__hint">Alphanumeric, hyphens, underscores only</span>';
        html += `<div class="recipe-editor__field">
            <label class="recipe-editor__label">Name</label>
            <input class="recipe-editor__input" id="editorName" placeholder="my-recipe" value="${this.esc(p.name || '')}" ${nameReadonly}>
            ${nameHint}
        </div>`;

        // Description
        html += `<div class="recipe-editor__field">
            <label class="recipe-editor__label">Description</label>
            <input class="recipe-editor__input" id="editorDescription" placeholder="What does this recipe do?" value="${this.esc(p.description || '')}">
        </div>`;

        // Version + Tags row
        html += `<div style="display:flex;gap:12px">
            <div class="recipe-editor__field" style="flex:0 0 100px">
                <label class="recipe-editor__label">Version</label>
                <input class="recipe-editor__input" id="editorVersion" value="${this.esc(p.version || '1.0')}">
            </div>
            <div class="recipe-editor__field" style="flex:1">
                <label class="recipe-editor__label">Tags</label>
                <input class="recipe-editor__input" id="editorTags" placeholder="ops, diagnostics" value="${this.esc((p.tags || []).join(', '))}">
                <span class="recipe-editor__hint">Comma-separated</span>
            </div>
        </div>`;

        // Tools
        html += `<div class="recipe-editor__section">
            <span class="recipe-editor__section-title">Tools</span>
            <span class="recipe-editor__hint" style="margin-left:8px">Empty = all tools available</span>
        </div>`;
        html += `<div class="recipe-editor__tools-grid" id="editorToolsGrid">`;
        for (const tool of (this._availableTools || [])) {
            const sel = selectedTools.has(tool.name) ? ' recipe-editor__tool-chip--selected' : '';
            html += `<div class="recipe-editor__tool-chip${sel}" data-tool-name="${this.esc(tool.name)}" title="${this.esc(tool.description || '')}">${this.esc(tool.name)}</div>`;
        }
        html += `</div>`;

        // Parameters
        html += `<div class="recipe-editor__section">
            <span class="recipe-editor__section-title">Parameters</span>
            <button class="recipe-editor__section-btn" id="btnAddParam">+ Add</button>
        </div>`;
        html += `<div class="recipe-editor__param-list" id="editorParamList"></div>`;

        // Prompt template
        html += `<div class="recipe-editor__field">
            <label class="recipe-editor__label">Prompt Template</label>
            <textarea class="recipe-editor__textarea recipe-editor__textarea--prompt" id="editorPromptTemplate" placeholder="Check the health of {{service}}. Report any issues found.">${this.esc(p.prompt_template || '')}</textarea>
            <span class="recipe-editor__hint">Use {{param_name}} for parameter placeholders</span>
        </div>`;

        // Output format
        html += `<div class="recipe-editor__field">
            <label class="recipe-editor__label">Output Format (optional)</label>
            <input class="recipe-editor__input" id="editorOutputFormat" placeholder="e.g. Return JSON with keys: status, details" value="${this.esc(p.output_format || '')}">
        </div>`;

        html += `</div>`;
        container.innerHTML = html;

        // Bind tool chip toggles
        container.querySelectorAll('.recipe-editor__tool-chip').forEach(chip => {
            chip.addEventListener('click', () => {
                chip.classList.toggle('recipe-editor__tool-chip--selected');
            });
        });

        // Bind add parameter
        document.getElementById('btnAddParam').addEventListener('click', () => this.addParameter());

        // Pre-populate parameters if provided
        if (p.parameters && p.parameters.length) {
            for (const param of p.parameters) {
                this.addParameter(param);
            }
        }
    }

    addParameter(defaults = null) {
        const list = document.getElementById('editorParamList');
        if (!list) return;

        const idx = list.children.length;
        const row = document.createElement('div');
        row.className = 'recipe-editor__param-row';

        const d = defaults || {};
        row.innerHTML = `
            <input type="text" placeholder="name" value="${this.esc(d.name || '')}" data-param-field="name">
            <select data-param-field="type">
                <option value="string"${d.type === 'string' || !d.type ? ' selected' : ''}>string</option>
                <option value="int"${d.type === 'int' ? ' selected' : ''}>int</option>
                <option value="float"${d.type === 'float' ? ' selected' : ''}>float</option>
                <option value="bool"${d.type === 'bool' ? ' selected' : ''}>bool</option>
                <option value="choice"${d.type === 'choice' ? ' selected' : ''}>choice</option>
            </select>
            <div class="recipe-editor__param-req" title="Required">
                <input type="checkbox" data-param-field="required" ${d.required !== false ? 'checked' : ''}>
            </div>
            <button class="recipe-editor__param-remove" title="Remove">&times;</button>
        `;

        row.querySelector('.recipe-editor__param-remove').addEventListener('click', () => row.remove());
        list.appendChild(row);
    }

    collectFormData() {
        const name = (document.getElementById('editorName')?.value || '').trim();
        const description = (document.getElementById('editorDescription')?.value || '').trim();
        const version = (document.getElementById('editorVersion')?.value || '1.0').trim();
        const tagsStr = (document.getElementById('editorTags')?.value || '').trim();
        const tags = tagsStr ? tagsStr.split(',').map(t => t.trim()).filter(Boolean) : [];
        const prompt_template = (document.getElementById('editorPromptTemplate')?.value || '').trim();
        const output_format = (document.getElementById('editorOutputFormat')?.value || '').trim();

        // Tools
        const tools = [];
        document.querySelectorAll('.recipe-editor__tool-chip--selected').forEach(chip => {
            tools.push(chip.dataset.toolName);
        });

        // Parameters
        const parameters = [];
        document.querySelectorAll('#editorParamList .recipe-editor__param-row').forEach(row => {
            const pName = row.querySelector('[data-param-field="name"]')?.value?.trim();
            if (!pName) return;
            parameters.push({
                name: pName,
                type: row.querySelector('[data-param-field="type"]')?.value || 'string',
                required: row.querySelector('[data-param-field="required"]')?.checked ?? true,
            });
        });

        return { name, description, version, tags, tools, parameters, prompt_template, output_format };
    }

    formToYaml() {
        const d = this.collectFormData();
        let yaml = '';
        yaml += `name: ${d.name || 'my-recipe'}\n`;
        if (d.description) yaml += `description: "${d.description.replace(/"/g, '\\"')}"\n`;
        yaml += `version: "${d.version}"\n`;
        if (d.prompt_template) {
            yaml += `prompt_template: "${d.prompt_template.replace(/"/g, '\\"')}"\n`;
        }
        if (d.parameters.length) {
            yaml += `parameters:\n`;
            for (const p of d.parameters) {
                yaml += `  - name: ${p.name}\n`;
                yaml += `    type: ${p.type}\n`;
                yaml += `    required: ${p.required}\n`;
            }
        }
        if (d.tools.length) {
            yaml += `tools:\n`;
            for (const t of d.tools) yaml += `  - ${t}\n`;
        }
        if (d.tags.length) {
            yaml += `tags:\n`;
            for (const t of d.tags) yaml += `  - ${t}\n`;
        }
        if (d.output_format) {
            yaml += `output_format: "${d.output_format.replace(/"/g, '\\"')}"\n`;
        }
        return yaml;
    }

    yamlToForm() {
        const textarea = document.getElementById('recipeYamlTextarea');
        if (!textarea) return;

        // Simple YAML parser — enough for recipe format
        const text = textarea.value;
        const lines = text.split('\n');
        const data = { parameters: [], tools: [], tags: [] };
        let currentArray = null;
        let currentParam = null;

        for (const line of lines) {
            const trimmed = line.trim();
            if (!trimmed || trimmed.startsWith('#')) continue;

            // Top-level key: value
            const kvMatch = trimmed.match(/^(\w+):\s*(.*)$/);
            if (kvMatch && !line.startsWith('  ')) {
                currentArray = null;
                currentParam = null;
                const key = kvMatch[1];
                let val = kvMatch[2].replace(/^["']|["']$/g, '').trim();

                if (['parameters', 'tools', 'tags', 'trigger'].includes(key) && !val) {
                    currentArray = key;
                } else {
                    data[key] = val;
                }
                continue;
            }

            // Array items
            if (currentArray && trimmed.startsWith('- ')) {
                const val = trimmed.slice(2).trim();
                if (currentArray === 'parameters') {
                    // - name: xxx
                    const nameMatch = val.match(/^name:\s*(.+)$/);
                    if (nameMatch) {
                        currentParam = { name: nameMatch[1].trim(), type: 'string', required: true };
                        data.parameters.push(currentParam);
                    } else {
                        currentParam = { name: val, type: 'string', required: true };
                        data.parameters.push(currentParam);
                    }
                } else {
                    data[currentArray].push(val.replace(/^["']|["']$/g, ''));
                }
                continue;
            }

            // Nested param fields (e.g. "    type: string")
            if (currentParam && trimmed.match(/^\w+:/)) {
                const pkvMatch = trimmed.match(/^(\w+):\s*(.+)$/);
                if (pkvMatch) {
                    const pkey = pkvMatch[1];
                    let pval = pkvMatch[2].replace(/^["']|["']$/g, '').trim();
                    if (pkey === 'required') pval = (pval === 'true');
                    currentParam[pkey] = pval;
                }
            }
        }

        // Populate form
        const setVal = (id, val) => { const el = document.getElementById(id); if (el) el.value = val || ''; };
        setVal('editorName', data.name);
        setVal('editorDescription', data.description);
        setVal('editorVersion', data.version || '1.0');
        setVal('editorTags', (data.tags || []).join(', '));
        setVal('editorPromptTemplate', data.prompt_template);
        setVal('editorOutputFormat', data.output_format);

        // Tools
        const toolSet = new Set(data.tools || []);
        document.querySelectorAll('.recipe-editor__tool-chip').forEach(chip => {
            chip.classList.toggle('recipe-editor__tool-chip--selected', toolSet.has(chip.dataset.toolName));
        });

        // Parameters
        const paramList = document.getElementById('editorParamList');
        if (paramList) {
            paramList.innerHTML = '';
            for (const p of (data.parameters || [])) {
                this.addParameter(p);
            }
        }
    }

    toggleEditorMode(mode) {
        this.editorMode = mode;
        const formContainer = document.getElementById('recipeEditorFormContainer');
        const yamlContainer = document.getElementById('recipeEditorYamlContainer');

        document.querySelectorAll('[data-editor-mode]').forEach(btn => {
            btn.classList.toggle('recipe-editor__mode-btn--active', btn.dataset.editorMode === mode);
        });

        if (mode === 'yaml') {
            // Sync form → YAML
            const yaml = this.formToYaml();
            document.getElementById('recipeYamlTextarea').value = yaml;
            formContainer.style.display = 'none';
            yamlContainer.classList.add('recipe-editor__yaml--active');
        } else {
            // Sync YAML → form
            this.yamlToForm();
            formContainer.style.display = '';
            yamlContainer.classList.remove('recipe-editor__yaml--active');
        }
    }

    async saveRecipe() {
        const errorEl = document.getElementById('recipeEditorError');
        if (errorEl) errorEl.textContent = '';

        let yamlContent;
        if (this.editorMode === 'yaml') {
            yamlContent = document.getElementById('recipeYamlTextarea')?.value || '';
        } else {
            yamlContent = this.formToYaml();
        }

        if (!yamlContent.trim()) {
            if (errorEl) errorEl.textContent = 'Recipe content is empty.';
            return;
        }

        const wsId = this.app.activeWorkspaceId || 'global';

        try {
            const headers = {
                'Content-Type': 'application/json',
                'X-CSRF-Token': this.app.csrfToken,
            };
            if (this.app.authToken) headers['Authorization'] = `Bearer ${this.app.authToken}`;

            const resp = await fetch(`/api/workspaces/${wsId}/recipes`, {
                method: 'POST',
                headers,
                body: JSON.stringify({ yaml_content: yamlContent }),
            });

            if (!resp.ok) {
                const err = await resp.json().catch(() => ({}));
                throw new Error(err.detail || err.error || `HTTP ${resp.status}`);
            }

            const result = await resp.json();

            this._isEditing = false;
            // Refresh list and re-select
            await this.fetchRecipes();
            this.selectRecipe(result.name);
        } catch (err) {
            console.error('Failed to save recipe:', err);
            if (errorEl) errorEl.textContent = err.message;
        }
    }

    cancelCreate() {
        const wasEditing = this._isEditing;
        const editedRecipe = wasEditing ? this.activeRecipe : null;
        this.editorPrefill = null;
        this._isEditing = false;

        if (wasEditing && editedRecipe) {
            // Return to the detail view for this recipe
            this.renderDetail();
        } else {
            const emptyState = document.getElementById('recipeEmptyState');
            const detailView = document.getElementById('recipeDetailView');
            if (emptyState) emptyState.style.display = '';
            if (detailView) {
                detailView.style.display = 'none';
                detailView.innerHTML = '';
            }
        }
    }

    // ---- Utilities ----

    esc(str) {
        if (!str) return '';
        const div = document.createElement('div');
        div.textContent = String(str);
        return div.innerHTML;
    }
}
