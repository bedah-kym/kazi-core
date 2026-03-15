// Context Panel JavaScript - Accordion-based with lazy-loading sections
class ContextPanel {
    constructor() {
        this.panel = document.getElementById('contextPanel');
        this.toggle = document.getElementById('contextPanelToggle');
        this.closeBtn = this.panel?.querySelector('.context-panel-close');
        this.isOpen = false;
        this.roomId = window.currentRoomId || null;
        this.isAddingNote = false;
        this.isAddingContact = false;
        this.activeMode = this.loadStoredMode();
        this.receipts = [];
        this.contextData = null;
        this.contactSearchTimeout = null;

        // Section definitions
        this.sections = [
            { id: 'controls', label: 'Assistant Controls', icon: 'fa-sliders-h', eager: true },
            { id: 'contacts', label: 'Contacts', icon: 'fa-address-book', eager: false },
            { id: 'receipts', label: 'Action Receipts', icon: 'fa-receipt', eager: false },
            { id: 'notes', label: 'Notes', icon: 'fa-sticky-note', eager: false },
            { id: 'summary', label: 'AI Summary', icon: 'fa-brain', eager: false },
        ];

        // Track loaded/expanded state per section
        this.sectionLoaded = {};
        this.sectionExpanded = {};

        this.init();
    }

    init() {
        if (!this.panel || !this.toggle) return;

        this.toggle.addEventListener('click', () => this.togglePanel());

        if (this.closeBtn) {
            this.closeBtn.addEventListener('click', () => this.closePanel());
        } else {
            this.panel.addEventListener('click', (e) => {
                if (e.target.closest('.context-panel-close')) {
                    this.closePanel();
                }
            });
        }

        if (this.roomId) {
            this.loadContext();
        }

        this.panel.addEventListener('click', (e) => this.handlePanelClick(e));

        // Refresh context every 30 seconds (only fetches base data)
        setInterval(() => {
            if (this.isOpen && !this.isAddingNote && !this.isAddingContact) {
                this.loadContext();
            }
        }, 30000);
    }

    // ----------------------------------------------------------------
    // Panel open/close
    // ----------------------------------------------------------------

    togglePanel() {
        if (this.isOpen) {
            this.closePanel();
        } else {
            this.openPanel();
        }
    }

    openPanel() {
        this.isOpen = true;
        this.panel.classList.add('open');
        this.toggle.classList.add('active');

        if (!this.panel.dataset.loaded) {
            this.loadContext();
        }
    }

    closePanel() {
        this.isOpen = false;
        this.panel.classList.remove('open');
        this.toggle.classList.remove('active');
        this.isAddingNote = false;
        this.isAddingContact = false;
    }

    // ----------------------------------------------------------------
    // Data loading
    // ----------------------------------------------------------------

    async loadContext() {
        const contentEl = document.getElementById('contextPanelContent');
        if (!contentEl || !this.roomId) return;

        try {
            const response = await fetch(`/chatbot/api/rooms/${this.roomId}/context/`);
            if (!response.ok) throw new Error('Failed to fetch context');

            this.contextData = await response.json();
            this.renderContext();
            this.panel.dataset.loaded = 'true';
        } catch (error) {
            console.error('Context load error:', error);
            this.renderError();
        }
    }

    // ----------------------------------------------------------------
    // Accordion rendering
    // ----------------------------------------------------------------

    renderContext() {
        const contentEl = document.getElementById('contextPanelContent');
        if (!contentEl || this.isAddingNote || this.isAddingContact) return;

        // Restore collapsed state from localStorage
        this._loadSectionState();

        let html = '';
        for (const section of this.sections) {
            const isExpanded = this.sectionExpanded[section.id] !== false; // default open for eager
            const badge = this._getSectionBadge(section.id);
            html += `
                <div class="context-section" data-section-id="${section.id}">
                    <div class="context-section-header ${isExpanded ? 'expanded' : ''}" data-toggle-section="${section.id}">
                        <div class="context-section-header-left">
                            <i class="fas ${section.icon} context-section-icon"></i>
                            <span class="context-section-label">${section.label}</span>
                            ${badge}
                        </div>
                        <i class="fas fa-chevron-down section-toggle"></i>
                    </div>
                    <div class="context-section-body ${isExpanded ? 'expanded' : ''}" id="section-body-${section.id}">
                        <div class="context-section-content" id="section-content-${section.id}">
                            ${section.eager ? this._renderEagerSection(section.id) : '<div class="context-loading"><div class="spinner"></div></div>'}
                        </div>
                    </div>
                </div>
            `;
        }
        contentEl.innerHTML = html;

        // Load eager sections immediately; lazy sections only if expanded
        for (const section of this.sections) {
            if (section.eager) {
                this.sectionLoaded[section.id] = true;
            } else if (this.sectionExpanded[section.id] !== false) {
                this.loadSection(section.id);
            }
        }
    }

    _getSectionBadge(sectionId) {
        if (!this.contextData) return '';
        if (sectionId === 'contacts' && this.contextData.contacts) {
            return `<span class="notes-count">${this.contextData.contacts.length}</span>`;
        }
        if (sectionId === 'notes' && this.contextData.recent_notes) {
            return `<span class="notes-count">${this.contextData.recent_notes.length}</span>`;
        }
        return '';
    }

    toggleSection(sectionId) {
        const header = this.panel.querySelector(`[data-toggle-section="${sectionId}"]`);
        const body = this.panel.querySelector(`#section-body-${sectionId}`);
        if (!header || !body) return;

        const isExpanded = header.classList.contains('expanded');
        header.classList.toggle('expanded', !isExpanded);
        body.classList.toggle('expanded', !isExpanded);

        this.sectionExpanded[sectionId] = !isExpanded;
        this._saveSectionState();

        // Lazy load on first expand
        if (!isExpanded && !this.sectionLoaded[sectionId]) {
            this.loadSection(sectionId);
        }
    }

    async loadSection(sectionId) {
        const contentEl = this.panel.querySelector(`#section-content-${sectionId}`);
        if (!contentEl) return;

        this.sectionLoaded[sectionId] = true;

        switch (sectionId) {
            case 'contacts':
                await this._loadContactsSection(contentEl);
                break;
            case 'receipts':
                await this._loadReceiptsSection(contentEl);
                break;
            case 'notes':
                this._loadNotesSection(contentEl);
                break;
            case 'summary':
                this._loadSummarySection(contentEl);
                break;
        }
    }

    _renderEagerSection(sectionId) {
        if (sectionId === 'controls') {
            return this._renderControlsContent();
        }
        return '';
    }

    // ----------------------------------------------------------------
    // Section: Controls (eager)
    // ----------------------------------------------------------------

    _renderControlsContent() {
        return `
            <div class="assistant-controls-help">
                Outcome: keep tasks moving while staying human.
            </div>
            <div class="mode-toggle-group" role="group" aria-label="Assistant mode">
                <button class="btn btn-sm mode-pill ${this.activeMode === 'auto' ? 'active' : ''}" data-mode="auto">Auto</button>
                <button class="btn btn-sm mode-pill ${this.activeMode === 'focus' ? 'active' : ''}" data-mode="focus">Focus</button>
                <button class="btn btn-sm mode-pill ${this.activeMode === 'social' ? 'active' : ''}" data-mode="social">Social</button>
            </div>
            <div class="assistant-actions">
                <button class="btn btn-sm btn-outline-secondary assistant-action-btn" data-quick-message="undo">Undo last</button>
                <button class="btn btn-sm btn-outline-secondary assistant-action-btn" data-quick-message="show actions">Show actions</button>
                <button class="btn btn-sm btn-outline-secondary assistant-action-btn" data-quick-message="stop for now">Pause tasks</button>
                <button class="btn btn-sm btn-outline-secondary assistant-action-btn" data-quick-message="resume">Resume</button>
            </div>
        `;
    }

    // ----------------------------------------------------------------
    // Section: Contacts (lazy)
    // ----------------------------------------------------------------

    async _loadContactsSection(contentEl) {
        try {
            const response = await fetch(`/chatbot/api/contacts/search/?room_id=${this.roomId}&q=`);
            let contacts = [];
            if (response.ok) {
                const data = await response.json();
                contacts = data.contacts || [];
            }
            // Fall back to context data if API returned nothing
            if (contacts.length === 0 && this.contextData && this.contextData.contacts) {
                contacts = this.contextData.contacts;
            }
            contentEl.innerHTML = this._renderContactsContent(contacts);
        } catch (err) {
            console.error('Contacts load error:', err);
            const contacts = (this.contextData && this.contextData.contacts) || [];
            contentEl.innerHTML = this._renderContactsContent(contacts);
        }
    }

    _renderContactsContent(contacts) {
        return `
            <div class="contact-search-row">
                <input type="text" class="form-control form-control-sm contact-search" placeholder="Search contacts..." data-contact-search>
                <button class="btn btn-sm btn-outline-primary" data-add-contact>
                    <i class="fas fa-plus"></i>
                </button>
            </div>
            <div id="contactList">
                ${this._renderContactList(contacts)}
            </div>
        `;
    }

    _renderContactList(contacts) {
        if (!contacts || contacts.length === 0) {
            return `
                <div class="receipts-empty">
                    No contacts yet. Add one or let the AI save them automatically.
                </div>
            `;
        }
        return contacts.map(c => {
            const info = [c.email, c.phone].filter(Boolean).join(' / ') || 'No info';
            return `
                <div class="contact-item" data-contact-id="${c.id}">
                    <div class="contact-item-main">
                        <div class="contact-item-name">${this.escapeHtml(c.name)}</div>
                        <div class="contact-item-info">${this.escapeHtml(info)}</div>
                    </div>
                    <div class="contact-item-actions">
                        <button class="btn btn-sm btn-link contact-action-btn" data-delete-contact="${c.id}" title="Delete">
                            <i class="fas fa-trash-alt"></i>
                        </button>
                    </div>
                </div>
            `;
        }).join('');
    }

    async searchContacts(query) {
        const listEl = this.panel.querySelector('#contactList');
        if (!listEl) return;

        try {
            const url = `/chatbot/api/contacts/search/?room_id=${this.roomId}&q=${encodeURIComponent(query)}`;
            const response = await fetch(url);
            if (!response.ok) return;
            const data = await response.json();
            listEl.innerHTML = this._renderContactList(data.contacts || []);
        } catch (err) {
            console.error('Contact search error:', err);
        }
    }

    showAddContactForm() {
        this.isAddingContact = true;
        const contentEl = document.getElementById('contextPanelContent');
        contentEl.innerHTML = `
            <div class="note-form-card">
                <div class="d-flex justify-content-between align-items-center mb-3">
                    <h6 class="m-0">New Contact</h6>
                    <button class="btn btn-sm btn-link text-muted" data-cancel-add-contact>Cancel</button>
                </div>
                <form id="addContactForm">
                    <div class="mb-2">
                        <input type="text" class="form-control form-control-sm" id="contactName" placeholder="Name *" required>
                    </div>
                    <div class="mb-2">
                        <input type="email" class="form-control form-control-sm" id="contactEmail" placeholder="Email">
                    </div>
                    <div class="mb-2">
                        <input type="text" class="form-control form-control-sm" id="contactPhone" placeholder="Phone">
                    </div>
                    <div class="mb-3">
                        <input type="text" class="form-control form-control-sm" id="contactLabel" placeholder="Label (e.g. colleague, client)">
                    </div>
                    <button type="submit" class="btn btn-primary btn-sm w-100" data-submit-contact>
                        <i class="fas fa-save me-1"></i> Save Contact
                    </button>
                </form>
            </div>
        `;
    }

    async submitContact(formEl) {
        const name = document.getElementById('contactName')?.value?.trim();
        const email = document.getElementById('contactEmail')?.value?.trim();
        const phone = document.getElementById('contactPhone')?.value?.trim();
        const label = document.getElementById('contactLabel')?.value?.trim();

        if (!name) return;

        const btn = formEl.querySelector('[data-submit-contact]');
        const originalText = btn.innerHTML;
        btn.disabled = true;
        btn.innerHTML = '<i class="fas fa-spinner fa-spin"></i> Saving...';

        try {
            const response = await fetch('/chatbot/api/contacts/', {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                    'X-CSRFToken': this.getCookie('csrftoken'),
                },
                body: JSON.stringify({
                    name, email, phone, label,
                    room_id: this.roomId,
                }),
            });
            if (!response.ok) throw new Error('Failed to save contact');

            this.isAddingContact = false;
            this.sectionLoaded['contacts'] = false;
            await this.loadContext();
        } catch (err) {
            console.error('Error saving contact:', err);
            btn.innerHTML = 'Error! Try again';
            btn.disabled = false;
            setTimeout(() => { btn.innerHTML = originalText; }, 2000);
        }
    }

    async deleteContact(contactId) {
        try {
            const response = await fetch(`/chatbot/api/contacts/${contactId}/`, {
                method: 'DELETE',
                headers: { 'X-CSRFToken': this.getCookie('csrftoken') },
            });
            if (response.ok) {
                const el = this.panel.querySelector(`[data-contact-id="${contactId}"]`);
                if (el) el.remove();
            }
        } catch (err) {
            console.error('Error deleting contact:', err);
        }
    }

    // ----------------------------------------------------------------
    // Section: Receipts (lazy)
    // ----------------------------------------------------------------

    async _loadReceiptsSection(contentEl) {
        const receipts = await this.loadReceipts();
        this.receipts = receipts;
        contentEl.innerHTML = `
            <div class="d-flex justify-content-end mb-2">
                <button class="btn btn-sm btn-link receipt-refresh" type="button">Refresh</button>
            </div>
            <div class="assistant-controls-help">
                Receipts show what ran in this room. Undo where possible.
            </div>
            <div id="receiptList">
                ${this.renderReceipts(receipts)}
            </div>
        `;
    }

    async loadReceipts() {
        if (!this.roomId) return [];
        try {
            const response = await fetch(`/chatbot/api/rooms/${this.roomId}/actions/?limit=3`);
            if (!response.ok) return [];
            const data = await response.json();
            return data.receipts || [];
        } catch (error) {
            console.error('Receipt load error:', error);
            return [];
        }
    }

    renderReceipts(receipts) {
        if (!receipts || receipts.length === 0) {
            return `
                <div class="receipts-empty">
                    No action receipts yet. Use Mathia to run tasks.
                    <div class="receipts-hint">Tip: say "show actions" for a fuller log.</div>
                </div>
            `;
        }
        return receipts.map(receipt => {
            const summary = this.escapeHtml(receipt.summary || receipt.action || 'Action');
            const timeLabel = this.formatTimestamp(receipt.created_at);
            const status = receipt.status || 'success';
            const reversible = receipt.reversible ? '<span class="receipt-pill">undo</span>' : '';
            return `
                <div class="receipt-item receipt-${status}">
                    <div class="receipt-main">
                        <div class="receipt-summary">${summary}</div>
                        <div class="receipt-meta">${timeLabel} - ${status}</div>
                    </div>
                    ${reversible}
                </div>
            `;
        }).join('');
    }

    // ----------------------------------------------------------------
    // Section: Notes (lazy, uses context data)
    // ----------------------------------------------------------------

    _loadNotesSection(contentEl) {
        const notes = this.contextData?.recent_notes || [];
        contentEl.innerHTML = `
            <div class="d-flex justify-content-end mb-2">
                <button class="btn btn-sm btn-outline-primary" data-add-note>
                    <i class="fas fa-plus me-1"></i> Add Note
                </button>
            </div>
            ${this.renderNotes(notes)}
        `;
    }

    // ----------------------------------------------------------------
    // Section: Summary (lazy, uses context data)
    // ----------------------------------------------------------------

    _loadSummarySection(contentEl) {
        const data = this.contextData || {};
        let html = `
            <div class="context-summary-text">
                ${this.escapeHtml(data.summary || 'No summary available yet. Keep chatting!')}
            </div>
        `;
        if (data.active_topics && data.active_topics.length > 0) {
            html += `
                <div class="context-summary-label mt-3">
                    <i class="fas fa-tags me-1"></i> Active Topics
                </div>
                <div class="note-tags">
                    ${data.active_topics.map(t => `<span class="note-tag">${this.escapeHtml(t)}</span>`).join('')}
                </div>
            `;
        }
        contentEl.innerHTML = html;
    }

    // ----------------------------------------------------------------
    // Note form (reused from original)
    // ----------------------------------------------------------------

    showAddNoteForm() {
        this.isAddingNote = true;
        const contentEl = document.getElementById('contextPanelContent');

        contentEl.innerHTML = `
            <div class="note-form-card">
                <div class="d-flex justify-content-between align-items-center mb-3">
                    <h6 class="m-0">New Note</h6>
                    <button class="btn btn-sm btn-link text-muted" data-cancel-add-note>Cancel</button>
                </div>
                <form id="addNoteForm">
                    <div class="mb-3">
                        <textarea class="form-control" id="noteContent" rows="3" placeholder="Enter decision, action item, or reminder..." required></textarea>
                    </div>
                    <div class="row g-2 mb-3">
                        <div class="col-6">
                            <label class="form-label small text-muted">Type</label>
                            <select class="form-select form-select-sm" id="noteType">
                                <option value="decision">Decision</option>
                                <option value="action_item">Action Item</option>
                                <option value="insight">Insight</option>
                                <option value="reminder">Reminder</option>
                                <option value="written" selected>General Note</option>
                            </select>
                        </div>
                        <div class="col-6">
                            <label class="form-label small text-muted">Priority</label>
                            <select class="form-select form-select-sm" id="notePriority">
                                <option value="low">Low</option>
                                <option value="medium" selected>Medium</option>
                                <option value="high">High</option>
                            </select>
                        </div>
                    </div>
                    <button type="submit" class="btn btn-primary btn-sm w-100" data-submit-note>
                        <i class="fas fa-save me-1"></i> Save Note
                    </button>
                </form>
            </div>
        `;
    }

    cancelAddNote() {
        this.isAddingNote = false;
        this.loadContext();
    }

    cancelAddContact() {
        this.isAddingContact = false;
        this.loadContext();
    }

    async submitNote(formEl) {
        const content = document.getElementById('noteContent')?.value;
        const type = document.getElementById('noteType')?.value;
        const priority = document.getElementById('notePriority')?.value;
        if (!content) return;

        const btn = formEl.querySelector('[data-submit-note]');
        const originalText = btn.innerHTML;
        btn.disabled = true;
        btn.innerHTML = '<i class="fas fa-spinner fa-spin"></i> Saving...';

        try {
            if (!this.roomId) throw new Error('No room selected');
            const response = await fetch(`/chatbot/api/rooms/${this.roomId}/notes/`, {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                    'X-CSRFToken': this.getCookie('csrftoken'),
                },
                body: JSON.stringify({ content, note_type: type, priority }),
            });
            if (!response.ok) throw new Error('Failed to save note');
            this.isAddingNote = false;
            await this.loadContext();
        } catch (error) {
            console.error('Error saving note:', error);
            btn.innerHTML = 'Error! Try again';
            btn.disabled = false;
            setTimeout(() => { btn.innerHTML = originalText; }, 2000);
        }
    }

    // ----------------------------------------------------------------
    // Render helpers
    // ----------------------------------------------------------------

    renderNotes(notes) {
        if (!notes || notes.length === 0) {
            return `
                <div class="notes-empty">
                    <i class="fas fa-note-sticky"></i>
                    <p>No notes yet.<br>AI will extract important points, or you can add one manually.</p>
                </div>
            `;
        }
        return notes.map(note => `
            <div class="note-card">
                <div class="note-card-header">
                    <span class="note-type-badge note-type-${note.type}">
                        ${this.getNoteIcon(note.type)} ${this.formatNoteType(note.type)}
                    </span>
                    <span class="note-priority note-priority-${note.priority}"
                          title="${note.priority} priority"></span>
                </div>
                <div class="note-content">
                    ${this.escapeHtml(note.content)}
                </div>
                <div class="d-flex justify-content-between align-items-center mt-2">
                    <small class="text-muted" style="font-size: 0.7em;">
                        ${new Date(note.created_at || Date.now()).toLocaleDateString()}
                    </small>
                </div>
            </div>
        `).join('');
    }

    getNoteIcon(type) {
        const icons = {
            'decision': '<i class="fas fa-check-circle"></i>',
            'action_item': '<i class="fas fa-tasks"></i>',
            'insight': '<i class="fas fa-lightbulb"></i>',
            'reminder': '<i class="fas fa-bell"></i>',
            'reference': '<i class="fas fa-bookmark"></i>',
            'written': '<i class="fas fa-pen"></i>'
        };
        return icons[type] || '<i class="fas fa-note-sticky"></i>';
    }

    formatNoteType(type) {
        return type.split('_').map(word =>
            word.charAt(0).toUpperCase() + word.slice(1)
        ).join(' ');
    }

    renderError() {
        const contentEl = document.getElementById('contextPanelContent');
        if (!contentEl) return;
        contentEl.innerHTML = `
            <div class="notes-empty">
                <i class="fas fa-exclamation-triangle"></i>
                <p>Failed to load context.<br>Please check your connection.</p>
                <button class="btn btn-sm btn-outline-secondary mt-2" onclick="window.contextPanel.loadContext()">
                    Try Again
                </button>
            </div>
        `;
    }

    // ----------------------------------------------------------------
    // Event delegation
    // ----------------------------------------------------------------

    handlePanelClick(event) {
        // Section toggle
        const sectionHeader = event.target.closest('[data-toggle-section]');
        if (sectionHeader) {
            event.preventDefault();
            this.toggleSection(sectionHeader.dataset.toggleSection);
            return;
        }

        // Mode buttons
        const modeBtn = event.target.closest('[data-mode]');
        if (modeBtn) {
            event.preventDefault();
            this.setMode(modeBtn.dataset.mode);
            return;
        }

        // Quick messages
        const quickBtn = event.target.closest('[data-quick-message]');
        if (quickBtn) {
            event.preventDefault();
            this.sendQuickMessage(quickBtn.dataset.quickMessage);
            return;
        }

        // Receipt refresh
        const refreshBtn = event.target.closest('.receipt-refresh');
        if (refreshBtn) {
            event.preventDefault();
            this.refreshReceipts();
            return;
        }

        // Add note button
        if (event.target.closest('[data-add-note]')) {
            event.preventDefault();
            this.showAddNoteForm();
            return;
        }

        // Cancel add note
        if (event.target.closest('[data-cancel-add-note]')) {
            event.preventDefault();
            this.cancelAddNote();
            return;
        }

        // Submit note form
        const noteForm = event.target.closest('#addNoteForm');
        if (noteForm && event.target.closest('[data-submit-note]')) {
            event.preventDefault();
            this.submitNote(noteForm);
            return;
        }

        // Add contact button
        if (event.target.closest('[data-add-contact]')) {
            event.preventDefault();
            this.showAddContactForm();
            return;
        }

        // Cancel add contact
        if (event.target.closest('[data-cancel-add-contact]')) {
            event.preventDefault();
            this.cancelAddContact();
            return;
        }

        // Submit contact form
        const contactForm = event.target.closest('#addContactForm');
        if (contactForm && event.target.closest('[data-submit-contact]')) {
            event.preventDefault();
            this.submitContact(contactForm);
            return;
        }

        // Delete contact
        const deleteBtn = event.target.closest('[data-delete-contact]');
        if (deleteBtn) {
            event.preventDefault();
            this.deleteContact(deleteBtn.dataset.deleteContact);
            return;
        }
    }

    // Handle input events (search debounce)
    _setupInputListeners() {
        this.panel.addEventListener('input', (e) => {
            if (e.target.matches('[data-contact-search]')) {
                clearTimeout(this.contactSearchTimeout);
                this.contactSearchTimeout = setTimeout(() => {
                    this.searchContacts(e.target.value.trim());
                }, 300);
            }
        });
    }

    async refreshReceipts() {
        const listEl = this.panel.querySelector('#receiptList');
        if (!listEl) return;
        const receipts = await this.loadReceipts();
        listEl.innerHTML = this.renderReceipts(receipts);
    }

    // ----------------------------------------------------------------
    // Mode management
    // ----------------------------------------------------------------

    setMode(mode) {
        if (!mode) return;
        this.activeMode = mode;
        this.storeMode(mode);
        this.updateModeButtons();
        this.sendQuickMessage(`mode ${mode}`);
    }

    updateModeButtons() {
        const buttons = this.panel.querySelectorAll('.mode-pill');
        buttons.forEach(btn => {
            btn.classList.toggle('active', btn.dataset.mode === this.activeMode);
        });
    }

    modeStorageKey() {
        return this.roomId ? `assistant_mode_${this.roomId}` : 'assistant_mode_default';
    }

    loadStoredMode() {
        try { return localStorage.getItem(this.modeStorageKey()) || 'auto'; }
        catch { return 'auto'; }
    }

    storeMode(mode) {
        try { localStorage.setItem(this.modeStorageKey(), mode); }
        catch { /* ignore */ }
    }

    // ----------------------------------------------------------------
    // Section state persistence
    // ----------------------------------------------------------------

    _sectionStateKey() {
        return this.roomId ? `ctx_sections_${this.roomId}` : 'ctx_sections_default';
    }

    _loadSectionState() {
        try {
            const stored = localStorage.getItem(this._sectionStateKey());
            if (stored) {
                this.sectionExpanded = JSON.parse(stored);
            } else {
                // Default: controls open, rest closed
                this.sectionExpanded = { controls: true };
            }
        } catch {
            this.sectionExpanded = { controls: true };
        }
    }

    _saveSectionState() {
        try {
            localStorage.setItem(this._sectionStateKey(), JSON.stringify(this.sectionExpanded));
        } catch { /* ignore */ }
    }

    // ----------------------------------------------------------------
    // Utilities
    // ----------------------------------------------------------------

    sendQuickMessage(message) {
        if (!message) return;
        const socket = window.getCurrentSocket ? window.getCurrentSocket() : null;
        if (!socket || socket.readyState !== WebSocket.OPEN) {
            console.warn('Socket not ready for quick message');
            return;
        }
        socket.send(JSON.stringify({
            message,
            from: window.usernameGlobal,
            command: 'new_message',
            chatid: window.currentRoomId,
            reply_to: null,
        }));
    }

    formatTimestamp(value) {
        if (!value) return 'just now';
        try { return new Date(value).toLocaleString(); }
        catch { return 'just now'; }
    }

    escapeHtml(text) {
        const div = document.createElement('div');
        div.textContent = text;
        return div.innerHTML;
    }

    getCookie(name) {
        let cookieValue = null;
        if (document.cookie && document.cookie !== '') {
            const cookies = document.cookie.split(';');
            for (let i = 0; i < cookies.length; i++) {
                const cookie = cookies[i].trim();
                if (cookie.substring(0, name.length + 1) === (name + '=')) {
                    cookieValue = decodeURIComponent(cookie.substring(name.length + 1));
                    break;
                }
            }
        }
        return cookieValue;
    }

    updateRoom(newRoomId) {
        if (!newRoomId || this.roomId === newRoomId) return;
        this.roomId = newRoomId;
        if (this.panel) this.panel.dataset.loaded = 'false';
        this.activeMode = this.loadStoredMode();
        this.sectionLoaded = {};
        this.updateModeButtons();
        if (this.isOpen) {
            this.loadContext();
        }
    }
}


// Initialize on page load and expose to window for onClick handlers
document.addEventListener('DOMContentLoaded', () => {
    window.contextPanel = new ContextPanel();
    // Setup input listeners after init
    window.contextPanel._setupInputListeners();
});
