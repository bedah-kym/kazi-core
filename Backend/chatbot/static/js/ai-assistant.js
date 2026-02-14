// ============================================
// MATHIA AI ASSISTANT - Frontend Integration
// ============================================

class MathiaAssistant {
    constructor(username) {
        this.username = username;
        this.isThinking = false;
        this.messageInput = document.getElementById('chat-message-input');
        this.chatHistory = document.querySelector('.chat-history');

        this.init();
    }

    init() {
        console.log('🤖 Mathia AI Assistant initialized');
        this.setupAutocomplete();
        this.setupQuickPrompts();
        // this.listenForAIMessages(); // Handled via main.js handleMessage callback
    }

    // ============================================
    // AUTOCOMPLETE @mathia
    // ============================================
    setupAutocomplete() {
        if (!this.messageInput) return;

        this.messageInput.addEventListener('input', (e) => {
            const value = e.target.value;
            const lastWord = value.split(' ').pop();

            // Show autocomplete when typing @
            if (lastWord === '@' || lastWord === '@m' || lastWord === '@ma') {
                this.showAutocomplete();
            } else {
                this.hideAutocomplete();
            }
        });

        // Handle Tab key for autocomplete
        this.messageInput.addEventListener('keydown', (e) => {
            if (e.key === 'Tab' && this.isAutocompleteVisible()) {
                e.preventDefault();
                this.completeWithMathia();
            }
        });
    }

    showAutocomplete() {
        // Remove existing autocomplete
        this.hideAutocomplete();

        const suggestion = document.createElement('div');
        suggestion.className = 'mathia-autocomplete';
        suggestion.innerHTML = `
            <div class="autocomplete-item">
                <i class="fas fa-robot"></i>
                <span><strong>@mathia</strong> - Ask AI assistant</span>
            </div>
        `;

        suggestion.addEventListener('click', () => {
            this.completeWithMathia();
        });

        this.messageInput.parentElement.appendChild(suggestion);
    }

    hideAutocomplete() {
        const existing = document.querySelector('.mathia-autocomplete');
        if (existing) existing.remove();
    }

    isAutocompleteVisible() {
        return !!document.querySelector('.mathia-autocomplete');
    }

    completeWithMathia() {
        const value = this.messageInput.value;
        const words = value.split(' ');
        words[words.length - 1] = '@mathia ';
        this.messageInput.value = words.join(' ');
        this.messageInput.focus();
        this.hideAutocomplete();
    }

    // ============================================
    // QUICK PROMPTS
    // ============================================
    setupQuickPrompts() {
        if (document.querySelector('.mathia-quick-prompts')) return;

        const ACTIONS = [
            {
                id: 'invoice',
                label: 'Send Invoice',
                icon: 'fas fa-file-invoice-dollar',
                fields: [
                    { name: 'amount', label: 'Amount (KES)', type: 'number', placeholder: '1500', required: true },
                    { name: 'payer', label: 'Payer Email', type: 'email', placeholder: 'payer@example.com', required: false },
                    { name: 'desc', label: 'Description', type: 'text', placeholder: 'Design work', required: true },
                ],
                buildPrompt: (v) => `@mathia create an invoice for ${v.amount || '0'} KES ${v.payer ? 'to ' + v.payer : ''} for ${v.desc || 'services'} and email it`
            },
            {
                id: 'balance',
                label: 'Balance & Txns',
                icon: 'fas fa-wallet',
                fields: [],
                buildPrompt: () => `@mathia show my balance and last 3 transactions`
            },
            {
                id: 'reminder',
                label: 'Set Reminder',
                icon: 'fas fa-bell',
                fields: [
                    { name: 'content', label: 'Reminder', type: 'text', placeholder: 'Follow up with client', required: true },
                    { name: 'time', label: 'When', type: 'text', placeholder: 'today 5pm', required: true },
                    { name: 'channel', label: 'Channel', type: 'select', options: ['email', 'whatsapp', 'both'], required: true, default: 'email' },
                ],
                buildPrompt: (v) => `@mathia set a reminder to "${v.content}" at ${v.time} via ${v.channel || 'email'}`
            },
            {
                id: 'flights',
                label: 'Find Flights',
                icon: 'fas fa-plane-departure',
                fields: [
                    { name: 'origin', label: 'From', type: 'text', placeholder: 'Nairobi', required: true },
                    { name: 'dest', label: 'To', type: 'text', placeholder: 'London', required: true },
                    { name: 'date', label: 'Date', type: 'date', placeholder: '', required: true },
                    { name: 'pax', label: 'Passengers', type: 'number', placeholder: '1', required: false },
                ],
                buildPrompt: (v) => `@mathia find flights from ${v.origin} to ${v.dest} on ${v.date} for ${v.pax || 1} passenger${(v.pax||1)>1?'s':''}`
            },
            {
                id: 'email',
                label: 'Send Email',
                icon: 'fas fa-envelope',
                fields: [
                    { name: 'to', label: 'To', type: 'email', placeholder: 'user@example.com', required: true },
                    { name: 'subject', label: 'Subject', type: 'text', placeholder: 'Project Update', required: true },
                    { name: 'body', label: 'Body', type: 'textarea', placeholder: 'Here is the latest status...', required: true },
                ],
                buildPrompt: (v) => `@mathia send an email to ${v.to} subject ${v.subject} body ${v.body}`
            },
            {
                id: 'withdraw_check',
                label: 'Safe Withdraw Check',
                icon: 'fas fa-shield-alt',
                fields: [
                    { name: 'phone', label: 'Phone', type: 'text', placeholder: '+254700000000', required: true },
                    { name: 'amount', label: 'Amount', type: 'number', placeholder: '3000', required: true },
                ],
                buildPrompt: (v) => `@mathia check withdraw policy for ${v.phone} amount ${v.amount}`
            },
            {
                id: 'whatsapp',
                label: 'Send WhatsApp',
                icon: 'fab fa-whatsapp',
                fields: [
                    { name: 'phone', label: 'Phone', type: 'text', placeholder: 'whatsapp:+2547...', required: true },
                    { name: 'message', label: 'Message', type: 'textarea', placeholder: 'Hello, quick update...', required: true },
                ],
                buildPrompt: (v) => `@mathia send a whatsapp to ${v.phone} saying ${v.message}`
            },
        ];

        const promptsContainer = document.createElement('div');
        promptsContainer.className = 'mathia-quick-prompts';
        promptsContainer.innerHTML = `
            <div class="quick-prompts-header">
                <i class="fas fa-robot"></i>
                <span>Quick Actions (live)</span>
                <button class="btn-close-prompts" title="Hide">
                    <i class="fas fa-times"></i>
                </button>
            </div>
            <div class="quick-prompts-list"></div>
        `;

        // Insert before chat history
        const chatContainer = document.querySelector('.chat-container');
        if (chatContainer) {
            chatContainer.insertBefore(promptsContainer, this.chatHistory);
        }

        const listEl = promptsContainer.querySelector('.quick-prompts-list');
        ACTIONS.forEach(action => {
            const btn = document.createElement('button');
            btn.className = 'quick-prompt-btn';
            btn.dataset.actionId = action.id;
            btn.innerHTML = `<i class="${action.icon}"></i> ${action.label}`;
            btn.addEventListener('click', () => this.openActionForm(action, promptsContainer));
            listEl.appendChild(btn);
        });

        promptsContainer.classList.remove('active');

        this.messageInput.addEventListener('input', (e) => {
            if (e.target.value.toLowerCase().includes('@mathia')) {
                promptsContainer.classList.add('active');
            } else {
                promptsContainer.classList.remove('active');
            }
        });

        promptsContainer.querySelector('.btn-close-prompts').addEventListener('click', () => {
            promptsContainer.classList.remove('active');
        });
    }

    openActionForm(action, container) {
        const existing = document.querySelector('.mathia-action-modal');
        if (existing) existing.remove();

        const modal = document.createElement('div');
        modal.className = 'mathia-action-modal';
        const fieldsHtml = (action.fields || []).map(f => {
            const required = f.required ? 'required' : '';
            if (f.type === 'select') {
                const opts = (f.options || []).map(o => `<option value="${o}" ${f.default===o?'selected':''}>${o}</option>`).join('');
                return `<label>${f.label}<select name="${f.name}" ${required}>${opts}</select></label>`;
            }
            if (f.type === 'textarea') {
                return `<label>${f.label}<textarea name="${f.name}" placeholder="${f.placeholder||''}" ${required}></textarea></label>`;
            }
            return `<label>${f.label}<input name="${f.name}" type="${f.type||'text'}" placeholder="${f.placeholder||''}" value="${f.default||''}" ${required}/></label>`;
        }).join('');

        modal.innerHTML = `
            <div class="mathia-action-modal-content">
                <div class="modal-header">
                    <span>${action.label}</span>
                    <button class="close-modal">&times;</button>
                </div>
                <form class="mathia-action-form">
                    ${fieldsHtml || '<p>No inputs needed.</p>'}
                    <div class="modal-actions">
                        <button type="submit" class="btn-primary">Run</button>
                        <button type="button" class="btn-secondary close-modal">Cancel</button>
                    </div>
                </form>
            </div>
        `;

        modal.querySelectorAll('.close-modal').forEach(btn => btn.addEventListener('click', () => modal.remove()));

        modal.querySelector('form').addEventListener('submit', (e) => {
            e.preventDefault();
            const formData = new FormData(e.target);
            const values = {};
            formData.forEach((v, k) => values[k] = v.toString().trim());
            const prompt = action.buildPrompt(values);
            this.sendToMathia(prompt);
            modal.remove();
            container.classList.remove('active');
        });

        document.body.appendChild(modal);
    }

    sendToMathia(message) {
        const fullMessage = `@mathia ${message}`;
        this.messageInput.value = fullMessage;

        // Trigger send
        document.getElementById('chat-message-submit').click();
    }

    getCurrentMessageList() {
        const roomId = window.currentRoomId || (typeof roomName !== 'undefined' ? roomName : null);
        if (roomId) {
            return document.getElementById(`messages-room-${roomId}`);
        }
        return null;
    }

    // ============================================
    // AI TYPING INDICATOR
    // ============================================
    showAIThinking() {
        if (this.isThinking || document.querySelector('.mathia-thinking')) return;
        this.isThinking = true;

        const thinkingIndicator = document.createElement('li');
        thinkingIndicator.className = 'clearfix mathia-thinking';
        thinkingIndicator.innerHTML = `
            <div class="message-data">
                <div class="time-label">${new Date().toLocaleTimeString()}</div>
            </div>
            <div class="message other-message mathia-message">
                <div class="ai-thinking-animation">
                    <i class="fas fa-robot"></i>
                    <span class="thinking-text">Mathia is thinking</span>
                    <div class="thinking-dots">
                        <span></span><span></span><span></span>
                    </div>
                </div>
            </div>
        `;

        const chatList = this.getCurrentMessageList();
        if (chatList) {
            chatList.appendChild(thinkingIndicator);
            chatList.scrollTop = chatList.scrollHeight;
        }
    }

    hideAIThinking() {
        this.isThinking = false;
        const thinkingElement = document.querySelector('.mathia-thinking');
        if (thinkingElement) {
            thinkingElement.remove();
        }
    }

    // ============================================
    // LISTEN FOR AI MESSAGES
    // ============================================
    // ============================================
    // HANDLE AI MESSAGES (Called from main.js)
    // ============================================
    handleMessage(data) {
        // Handle streaming chunks
        if (data.command === 'ai_stream') {
            this.handleStreamChunk(data.chunk, data.is_final);
            return;
        }

        // NEW: Handle saved message after streaming completes
        if (data.command === 'ai_message_saved') {
            console.log('📝 AI message saved event received');
            this.isThinking = false;

            // Remove ALL temporary AI states from THIS room
            const chatList = this.getCurrentMessageList();
            if (chatList) {
                // Remove any streaming containers
                chatList.querySelectorAll('.ai-stream-container').forEach(el => el.remove());
                // Remove any thinking indicators
                chatList.querySelectorAll('.mathia-thinking').forEach(el => el.remove());
            }

            // Use createMessage() for proper markdown rendering and dropdown
            if (typeof createMessage === 'function') {
                const roomId = window.currentRoomId || (typeof roomName !== 'undefined' ? roomName : null);
                createMessage(data.message, roomId);
            }
            return;
        }

        // Handle Mathia voice ready
        if (data.command === 'ai_voice_ready') {
            console.log('🎙️ AI voice response ready');
            const roomId = window.currentRoomId || (typeof roomName !== 'undefined' ? roomName : null);
            const msgContainer = document.querySelector(`[data-message-id="${data.message_id}"] .mathia-message`);
            if (msgContainer && typeof renderVoiceBubble === 'function') {
                renderVoiceBubble(msgContainer, {
                    id: data.message_id,
                    audio_url: data.audio_url,
                    has_ai_voice: true,
                    member: 'mathia'
                });
            }
            return;
        }

        // Handle complete AI message (legacy/fallback)
        if (data.command === 'ai_message') {
            this.hideAIThinking();
            this.displayAIMessage(data.message);
            return;
        }
    }

    checkForTrigger(data) {
        if (data.command === 'new_message' &&
            data.message?.content?.toLowerCase().includes('@mathia') &&
            data.message?.member === (this.username || window.usernameGlobal)) {
            this.showAIThinking();
        }
    }

    handleStreamChunk(chunk, isFinal) {
        this.hideAIThinking();

        let streamContainer = document.querySelector('.ai-stream-container');

        if (!streamContainer) {
            // Create streaming message container
            const chatList = this.getCurrentMessageList();
            if (!chatList) return;

            const msgListTag = document.createElement('li');
            msgListTag.className = 'clearfix mathia-message-item ai-stream-container';

            msgListTag.innerHTML = `
            <div class="message-data">
                <div class="time-label">${new Date().toLocaleTimeString()}</div>
            </div>
            <div class="message other-message mathia-message">
                <div class="mathia-badge">
                    <i class="fas fa-robot"></i>
                    <span>Mathia AI</span>
                </div>
                <div class="mathia-content stream-content"></div>
            </div>
        `;

            chatList.appendChild(msgListTag);
            streamContainer = msgListTag;
        }

        // Append chunk to content (use innnerText to avoid HTML injection but keep line breaks)
        const contentDiv = streamContainer.querySelector('.stream-content');
        contentDiv.innerText += chunk;

        // Scroll to bottom
        const chatList = this.getCurrentMessageList();
        if (chatList) chatList.scrollTop = chatList.scrollHeight;

        // DO NOT remove ai-stream-container class here. 
        // We need it so ai_message_saved can find and remove it later.
    }

    displayAIMessage(messageData) {
        const chatList = this.getCurrentMessageList();
        if (!chatList) return;

        const formattedTime = new Date(messageData.timestamp).toLocaleTimeString();

        const msgListTag = document.createElement('li');
        msgListTag.className = 'clearfix mathia-message-item';

        msgListTag.innerHTML = `
            <div class="message-data">
                <div class="time-label">${formattedTime}</div>
            </div>
            <div class="message other-message mathia-message">
                <div class="mathia-badge">
                    <i class="fas fa-robot"></i>
                    <span>Mathia AI</span>
                </div>
                <div class="mathia-content">${messageData.content}</div>
            </div>
        `;

        chatList.appendChild(msgListTag);
        chatList.scrollTop = chatList.scrollHeight;

        // Animate entrance
        setTimeout(() => {
            msgListTag.classList.add('slide-in');
        }, 10);
    }

    // ============================================
    // ERROR HANDLING
    // ============================================
    showAIError(errorMessage = "I'm having trouble right now. Please try again! 🤖") {
        this.hideAIThinking();

        const errorMsg = {
            member: 'mathia',
            content: `<div class="ai-error"><i class="fas fa-exclamation-triangle"></i> ${errorMessage}</div>`,
            timestamp: new Date().toISOString()
        };

        this.displayAIMessage(errorMsg);
    }
}

// ============================================
// INITIALIZE ON PAGE LOAD
// ============================================
document.addEventListener('DOMContentLoaded', function () {
    const initMathia = setInterval(() => {
        const uName = window.usernameGlobal || (typeof username !== 'undefined' ? username : null);
        if (uName) {
            clearInterval(initMathia);
            window.mathiaAssistant = new MathiaAssistant(uName);
            console.log('✅ Mathia AI Assistant ready');
        }
    }, 100);
});

// Expose for debugging
window.MathiaAssistant = MathiaAssistant;
