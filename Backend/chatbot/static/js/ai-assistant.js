// ============================================
// MATHIA AI ASSISTANT - Frontend Integration
// ============================================

class MathiaAssistant {
    constructor(chatSocket, username) {
        this.chatSocket = chatSocket;
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
        this.listenForAIMessages();
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
        // Check if quick prompts already exist
        if (document.querySelector('.mathia-quick-prompts')) return;

        const promptsContainer = document.createElement('div');
        promptsContainer.className = 'mathia-quick-prompts';
        promptsContainer.innerHTML = `
            <div class="quick-prompts-header">
                <i class="fas fa-robot"></i>
                <span>Quick AI Actions</span>
                <button class="btn-close-prompts" title="Hide">
                    <i class="fas fa-times"></i>
                </button>
            </div>
            <div class="quick-prompts-list">
                <button class="quick-prompt-btn" data-prompt="Summarize the last few messages">
                    <i class="fas fa-compress"></i> Summarize Chat
                </button>
                <button class="quick-prompt-btn" data-prompt="Help me understand this topic">
                    <i class="fas fa-question-circle"></i> Explain
                </button>
                <button class="quick-prompt-btn" data-prompt="What are the key points from this conversation?">
                    <i class="fas fa-list"></i> Key Points
                </button>
                <button class="quick-prompt-btn" data-prompt="Can you help me with">
                    <i class="fas fa-hands-helping"></i> Get Help
                </button>
            </div>
        `;

        // Insert before chat history
        const chatContainer = document.querySelector('.chat-container');
        if (chatContainer) {
            chatContainer.insertBefore(promptsContainer, this.chatHistory);
        }

        // Hide by default (we use CSS transitions)
        promptsContainer.classList.remove('active');

        // Toggle quick prompts visibility when @mathia is typed
        this.messageInput.addEventListener('input', (e) => {
            if (e.target.value.includes('@mathia')) {
                promptsContainer.classList.add('active');
            } else {
                promptsContainer.classList.remove('active');
            }
        });


        // Handle prompt clicks
        promptsContainer.querySelectorAll('.quick-prompt-btn').forEach(btn => {
            btn.addEventListener('click', () => {
                const prompt = btn.dataset.prompt;
                this.sendToMathia(prompt);
                promptsContainer.classList.remove('active');

            });
        });

        // Close button
        promptsContainer.querySelector('.btn-close-prompts').addEventListener('click', () => {
            promptsContainer.classList.remove('active');
        });
    }

    sendToMathia(message) {
        const fullMessage = `@mathia ${message}`;
        this.messageInput.value = fullMessage;

        // Trigger send
        document.getElementById('chat-message-submit').click();
    }

    // ============================================
    // AI TYPING INDICATOR
    // ============================================
    showAIThinking() {
        if (this.isThinking) return;
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

        const chatList = document.getElementById('top-chat');
        if (chatList) {
            chatList.appendChild(thinkingIndicator);
            this.chatHistory.scrollTop = this.chatHistory.scrollHeight;
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
    listenForAIMessages() {
        const originalOnMessage = this.chatSocket.onmessage;

        this.chatSocket.onmessage = (e) => {
            const data = JSON.parse(e.data);

            // Call original handler FIRST to display user message
            if (originalOnMessage) {
                originalOnMessage.call(this.chatSocket, e);
            }

            // Handle streaming chunks
            if (data.command === 'ai_stream') {
                this.handleStreamChunk(data.chunk, data.is_final);
                return;
            }

            // Handle complete AI message
            if (data.command === 'ai_message') {
                this.hideAIThinking();
                this.displayAIMessage(data.message);
                return;
            }

            // Detect @mathia trigger
            if (data.command === 'new_message' &&
                data.message?.content?.includes('@mathia') &&
                data.message?.member === this.username) {
                this.showAIThinking();
            }
        };
    }

    handleStreamChunk(chunk, isFinal) {
        this.hideAIThinking();

        let streamContainer = document.querySelector('.ai-stream-container');

        if (!streamContainer) {
            // Create streaming message container
            const chatList = document.getElementById('top-chat');
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

        // Append chunk to content
        const contentDiv = streamContainer.querySelector('.stream-content');
        contentDiv.textContent += chunk;

        // Scroll to bottom
        this.chatHistory.scrollTop = this.chatHistory.scrollHeight;

        if (isFinal) {
            streamContainer.classList.remove('ai-stream-container');
        }
    }

    displayAIMessage(messageData) {
        const chatList = document.getElementById('top-chat');
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
        this.chatHistory.scrollTop = this.chatHistory.scrollHeight;

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
    // Wait for chatSocket to be defined in main.js
    const initMathia = setInterval(() => {
        if (typeof chatSocket !== 'undefined' && typeof username !== 'undefined') {
            clearInterval(initMathia);
            window.mathiaAssistant = new MathiaAssistant(chatSocket, username);
            console.log('✅ Mathia AI Assistant ready');
        }
    }, 100);
});

// Expose for debugging
window.MathiaAssistant = MathiaAssistant;