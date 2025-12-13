// Context Panel JavaScript - shadcn-inspired notes UI
class ContextPanel {
    constructor() {
        this.panel = document.getElementById('contextPanel');
        this.toggle = document.getElementById('contextPanelToggle');
        this.isOpen = false;
        this.roomId = roomName; // From Django template

        this.init();
    }

    init() {
        if (!this.panel || !this.toggle) return;

        this.toggle.addEventListener('click', () => this.togglePanel());
        this.loadContext();

        // Refresh context every 30 seconds
        setInterval(() => this.loadContext(), 30000);
    }

    togglePanel() {
        this.isOpen = !this.isOpen;
        this.panel.classList.toggle('open');
        this.toggle.classList.toggle('active');

        if (this.isOpen && !this.panel.dataset.loaded) {
            this.loadContext();
        }
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

        contentEl.innerHTML = `
            <!-- AI Summary -->
            <div class="context-summary-card">
                <div class="context-summary-label">
                    <i class="fas fa-brain me-1"></i> AI Summary
                </div>
                <div class="context-summary-text">
                    ${this.escapeHtml(context.summary || 'No summary available yet. Keep chatting!')}
                </div>
            </div>
            
            <!-- Key Participants -->
            ${context.participants && context.participants.length > 0 ? `
                <div class="context-summary-card">
                    <div class="context-summary-label">
                        <i class="fas fa-users me-1"></i> Participants
                    </div>
                    <div class="note-tags">
                        ${context.participants.map(p => `<span class="note-tag">${this.escapeHtml(p)}</span>`).join('')}
                    </div>
                </div>
            ` : ''}
            
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
            
            <!-- Latest Daily Summary -->
            ${context.latest_daily_summary ? `
                <div class="context-summary-card">
                    <div class="context-summary-label">
                        <i class="fas fa-calendar-day me-1"></i> Today's Summary
                    </div>
                    <div class="context-summary-text">
                        ${this.escapeHtml(context.latest_daily_summary)}
                    </div>
                </div>
            ` : ''}
        `;
    }

    renderNotes(notes) {
        if (!notes || notes.length === 0) {
            return `
                <div class="notes-empty">
                    <i class="fas fa-note-sticky"></i>
                    <p>No notes yet.<br>AI will extract important points from your conversation.</p>
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
                ${note.tags && note.tags.length > 0 ? `
                    <div class="note-tags">
                        ${note.tags.map(tag => `<span class="note-tag">#${this.escapeHtml(tag)}</span>`).join('')}
                    </div>
                ` : ''}
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
                <p>Failed to load context.<br>Please try again.</p>
            </div>
        `;
    }

    escapeHtml(text) {
        const div = document.createElement('div');
        div.textContent = text;
        return div.innerHTML;
    }
}

// Initialize on page load
document.addEventListener('DOMContentLoaded', () => {
    new ContextPanel();
});
