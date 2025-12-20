// Context Panel JavaScript - shadcn-inspired notes UI
class ContextPanel {
    constructor() {
        this.panel = document.getElementById('contextPanel');
        this.toggle = document.getElementById('contextPanelToggle');
        // Close button might be dynamically added or existing
        this.closeBtn = this.panel?.querySelector('.context-panel-close');
        this.isOpen = false;
        this.roomId = roomName; // From Django template
        this.isAddingNote = false; // State for form visibility

        this.init();
    }

    init() {
        if (!this.panel || !this.toggle) return;

        this.toggle.addEventListener('click', () => this.togglePanel());

        // Setup Close Button if it exists in HTML, otherwise we'll add it dynamically
        if (this.closeBtn) {
            this.closeBtn.addEventListener('click', () => this.closePanel());
        } else {
            // Add close functionality to any element with this class (delegate)
            this.panel.addEventListener('click', (e) => {
                if (e.target.closest('.context-panel-close')) {
                    this.closePanel();
                }
            });
        }

        this.loadContext();

        // Refresh context every 30 seconds
        setInterval(() => {
            if (this.isOpen && !this.isAddingNote) {
                this.loadContext();
            }
        }, 30000);
    }

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
        this.isAddingNote = false; // Reset form state
    }

    async loadContext() {
        const contentEl = document.getElementById('contextPanelContent');
        if (!contentEl) return;

        try {
            // Fetch context from Django API endpoint
            const response = await fetch(`/chatbot/api/rooms/${this.roomId}/context/`);

            if (!response.ok) {
                throw new Error('Failed to fetch context');
            }

            const data = await response.json();
            this.renderContext(data);
            this.panel.dataset.loaded = 'true';

        } catch (error) {
            console.error('Context load error:', error);
            this.renderError();
        }
    }

    renderContext(context) {
        const contentEl = document.getElementById('contextPanelContent');
        if (this.isAddingNote) return; // Don't overwrite if user is typing

        contentEl.innerHTML = `
            <!-- Actions Header -->
            <div class="d-flex justify-content-between align-items-center mb-3">
                <button class="btn btn-sm btn-outline-primary w-100" onclick="window.contextPanel.showAddNoteForm()">
                    <i class="fas fa-plus me-1"></i> Add Manual Note
                </button>
            </div>

            <!-- AI Summary -->
            <div class="context-summary-card">
                <div class="context-summary-label">
                    <i class="fas fa-brain me-1"></i> AI Summary
                </div>
                <div class="context-summary-text">
                    ${this.escapeHtml(context.summary || 'No summary available yet. Keep chatting!')}
                </div>
            </div>
            
            <!-- Active Topics -->
            ${context.active_topics && context.active_topics.length > 0 ? `
                <div class="context-summary-card">
                    <div class="context-summary-label">
                        <i class="fas fa-tags me-1"></i> Active Topics
                    </div>
                    <div class="note-tags">
                        ${context.active_topics.map(t => `<span class="note-tag">${this.escapeHtml(t)}</span>`).join('')}
                    </div>
                </div>
            ` : ''}
            
            <!-- Recent Notes -->
            <div class="notes-section">
                <div class="notes-header">
                    <div class="notes-title">
                        <i class="fas fa-sticky-note"></i>
                        Notes
                        ${context.recent_notes && context.recent_notes.length > 0 ?
                `<span class="notes-count">${context.recent_notes.length}</span>` : ''}
                    </div>
                </div>
                
                ${this.renderNotes(context.recent_notes)}
            </div>
        `;
    }

    showAddNoteForm() {
        this.isAddingNote = true;
        const contentEl = document.getElementById('contextPanelContent');

        contentEl.innerHTML = `
            <div class="note-form-card">
                <div class="d-flex justify-content-between align-items-center mb-3">
                    <h6 class="m-0">New Note</h6>
                    <button class="btn btn-sm btn-link text-muted" onclick="window.contextPanel.cancelAddNote()">Cancel</button>
                </div>
                
                <form id="addNoteForm" onsubmit="window.contextPanel.submitNote(event)">
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
                    
                    <button type="submit" class="btn btn-primary btn-sm w-100">
                        <i class="fas fa-save me-1"></i> Save Note
                    </button>
                </form>
            </div>
        `;
    }

    cancelAddNote() {
        this.isAddingNote = false;
        this.loadContext(); // Reload list
    }

    async submitNote(event) {
        event.preventDefault();

        const content = document.getElementById('noteContent').value;
        const type = document.getElementById('noteType').value;
        const priority = document.getElementById('notePriority').value;

        if (!content) return;

        // Show loading state
        const btn = event.target.querySelector('button[type="submit"]');
        const originalText = btn.innerHTML;
        btn.disabled = true;
        btn.innerHTML = '<i class="fas fa-spinner fa-spin"></i> Saving...';

        try {
            const response = await fetch(`/chatbot/api/rooms/${this.roomId}/notes/`, {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                    'X-CSRFToken': this.getCookie('csrftoken')
                },
                body: JSON.stringify({
                    content: content,
                    note_type: type,
                    priority: priority
                })
            });

            if (!response.ok) throw new Error('Failed to save note');

            // Success
            this.isAddingNote = false;
            await this.loadContext(); // Reload context

        } catch (error) {
            console.error('Error saving note:', error);
            btn.innerHTML = 'Error! Try again';
            btn.disabled = false;
            setTimeout(() => {
                btn.innerHTML = originalText;
            }, 2000);
        }
    }

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
    closeContextPanel() {
        const panel = document.getElementById('contextPanel');
        panel.classList.add('closed');
    }

    updateRoom(newRoomId) {
        if (!newRoomId || this.roomId === newRoomId) return;
        this.roomId = newRoomId;
        this.panel.dataset.loaded = 'false';
        if (this.isOpen) {
            this.loadContext();
        }
    }
}


// Initialize on page load and expose to window for onClick handlers
document.addEventListener('DOMContentLoaded', () => {
    window.contextPanel = new ContextPanel();
});
