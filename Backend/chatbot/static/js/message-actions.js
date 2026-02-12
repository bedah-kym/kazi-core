/**
 * Message Actions - Dropdown menu and action handlers
 * Handles pin, reply, retry, and document upload functionality
 */

class MessageActions {
    constructor() {
        this.activeDropdown = null;
        this.uploadModal = null;
        this.currentQuota = null;
        this.init();
    }

    init() {
        // Close dropdown when clicking outside
        document.addEventListener('click', (e) => {
            if (this.activeDropdown && !e.target.closest('.message-actions-dropdown')) {
                this.closeDropdown();
            }
        });

        // Create upload modal
        this.createUploadModal();

        // Fetch initial quota
        this.fetchQuota();

        // Delegated click handling for message action buttons and menu items.
        // Using delegation avoids attaching listeners per-message and prevents
        // issues where only the first message's listeners worked.
        document.addEventListener('click', (e) => {
            const btn = e.target.closest('.message-actions-btn');
            if (btn) {
                e.stopPropagation();
                const messageId = btn.dataset.messageId;
                this.toggleDropdown(messageId);
                return;
            }

            const item = e.target.closest('.message-actions-item');
            if (item) {
                e.stopPropagation();
                const action = item.dataset.action;
                // Prefer explicit data-message-id on the item, fall back to parent button
                const msgId = item.dataset.messageId || item.closest('.message-actions-dropdown')?.querySelector('.message-actions-btn')?.dataset.messageId;

                switch (action) {
                    case 'pin':
                        this.pinMessage(msgId, item.dataset.messageContent || null);
                        break;
                    case 'reply':
                        this.replyToMessage(msgId, item.dataset.messageContent || null);
                        break;
                    case 'retry':
                        this.retryMessage(msgId);
                        break;
                    case 'upload':
                        this.openUploadModal();
                        break;
                }
                return;
            }
        });
    }

    /**
     * Add dropdown to AI message
     */
    addDropdownToMessage(messageElement, messageId, messageContent, isAIMessage, isFailed) {
        if (!isAIMessage) return; // Only add to AI messages

        const roomId = window.currentRoomId || 'room';
        const menuId = `${roomId}-${messageId}`;

        // Ensure parent has position relative for proper dropdown positioning
        messageElement.style.position = 'relative';

        const dropdown = document.createElement('div');
        dropdown.className = 'message-actions-dropdown';
        dropdown.innerHTML = `
            <button class="message-actions-btn" aria-label="Message actions" data-message-id="${messageId}" data-room-id="${roomId}">
                <i class="fas fa-ellipsis-v"></i>
            </button>
            <div class="message-actions-menu" id="actions-menu-${menuId}" data-menu-id="${menuId}">
                <button class="message-actions-item" data-action="pin" data-message-id="${messageId}" data-room-id="${roomId}">
                    <i class="fas fa-thumbtack"></i>
                    Pin to Notes
                </button>
                <button class="message-actions-item" data-action="reply" data-message-id="${messageId}" data-room-id="${roomId}">
                    <i class="fas fa-reply"></i>
                    Reply
                </button>
                ${isFailed ? `
                <button class="message-actions-item danger" data-action="retry" data-message-id="${messageId}" data-room-id="${roomId}">
                    <i class="fas fa-redo"></i>
                    Retry
                </button>
                ` : ''}
                <button class="message-actions-item" data-action="upload" data-room-id="${roomId}">
                    <i class="fas fa-upload"></i>
                    Upload Document
                </button>
            </div>
        `;

        // Attach the dropdown DOM to the message element. Event handling is
        // done via delegation (document click listener in init()) so we don't
        // add per-message listeners here.
        // Add message content as a data attribute on menu items where needed
        // to allow the delegated handler to access it.
        // Ensure action items carry the message id/content where appropriate.
        const menu = dropdown.querySelector('.message-actions-menu');
        if (menu) {
            menu.querySelectorAll('.message-actions-item').forEach(item => {
                if (!item.dataset.messageId) item.dataset.messageId = messageId;
                if (!item.dataset.messageContent) item.dataset.messageContent = messageContent || '';
                if (!item.dataset.roomId) item.dataset.roomId = roomId;
            });
        }

        messageElement.appendChild(dropdown);
    }


    toggleDropdown(messageId) {
        const roomId = window.currentRoomId || 'room';
        const menu = document.querySelector(`.message-actions-menu[data-menu-id="${roomId}-${messageId}"]`);
        if (!menu) return;

        if (this.activeDropdown && this.activeDropdown !== menu) {
            this.closeDropdown();
        }

        menu.classList.toggle('show');
        this.activeDropdown = menu.classList.contains('show') ? menu : null;
    }

    closeDropdown() {
        if (this.activeDropdown) {
            this.activeDropdown.classList.remove('show');
            this.activeDropdown = null;
        }
    }

    /**
     * Pin message to notes
     */
    async pinMessage(messageId, messageContent) {
        if (!window.currentRoomId) return;
        this.closeDropdown();

        try {
            const response = await fetch(`/chatbot/api/rooms/${window.currentRoomId}/messages/${messageId}/pin/`, {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                    'X-CSRFToken': getCookie('csrftoken')
                },
                body: JSON.stringify({
                    message_content: messageContent  // Send decrypted content
                })
            });

            const data = await response.json();

            if (response.ok) {
                this.showToast('Message pinned to notes!', 'success');

                // Refresh context panel if it's open
                if (window.contextPanel && window.contextPanel.isOpen) {
                    window.contextPanel.loadContext();
                }
            } else {
                this.showToast(data.error || 'Failed to pin message', 'error');
            }
        } catch (error) {
            console.error('Pin error:', error);
            this.showToast('Network error. Please try again.', 'error');
        }
    }

    /**
     * Reply to message
     */
    async replyToMessage(messageId, messageContent) {
        if (!window.currentRoomId) return;
        this.closeDropdown();

        try {
            const response = await fetch(`/chatbot/api/rooms/${window.currentRoomId}/messages/${messageId}/reply/`, {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                    'X-CSRFToken': getCookie('csrftoken')
                },
                body: JSON.stringify({
                    message_content: messageContent  // Send decrypted content
                })
            });

            const data = await response.json();

            if (response.ok) {
                // Pre-fill input with reply
                const input = document.getElementById('chat-message-input');
                if (input) {
                    input.value = data.reply_prefix;
                    input.focus();
                }
                window.replyToMessageId = messageId;
            } else {
                this.showToast(data.error || 'Failed to create reply', 'error');
            }
        } catch (error) {
            console.error('Reply error:', error);
            this.showToast('Network error. Please try again.', 'error');
        }
    }

    /**
     * Retry failed AI message
     */
    async retryMessage(messageId) {
        if (!window.currentRoomId) return;
        this.closeDropdown();

        try {
            const response = await fetch(`/chatbot/api/rooms/${window.currentRoomId}/messages/${messageId}/retry/`, {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                    'X-CSRFToken': getCookie('csrftoken')
                }
            });

            const data = await response.json();

            if (response.ok) {
                this.showToast('Retrying AI response...', 'success');
                // TODO: Trigger WebSocket retry in consumers.py
            } else {
                this.showToast(data.error || 'Failed to retry', 'error');
            }
        } catch (error) {
            console.error('Retry error:', error);
            this.showToast('Network error. Please try again.', 'error');
        }
    }

    /**
     * Create upload modal
     */
    createUploadModal() {
        const modal = document.createElement('div');
        modal.className = 'upload-modal';
        modal.id = 'uploadModal';
        modal.innerHTML = `
            <div class="upload-modal-content">
                <div class="upload-modal-header">
                    <h3 class="upload-modal-title">Upload Document</h3>
                    <button class="upload-modal-close" onclick="window.messageActions.closeUploadModal()">
                        <i class="fas fa-times"></i>
                    </button>
                </div>
                
                <div class="upload-quota-info">
                    <span class="upload-quota-label">Uploads remaining:</span>
                    <span class="upload-quota-count" id="quotaCount">Loading...</span>
                </div>
                
                <div class="upload-restrictions">
                    <strong>Supported files:</strong> PDFs (max 10MB) and Images (max 5MB)
                </div>
                
                <div class="upload-dropzone" id="uploadDropzone">
                    <i class="fas fa-cloud-upload-alt"></i>
                    <div class="upload-dropzone-text">Click to upload or drag and drop</div>
                    <div class="upload-dropzone-hint">PDF, JPG, PNG, GIF, WebP</div>
                </div>
                
                <input type="file" id="fileUploadInput" accept=".pdf,.jpg,.jpeg,.png,.gif,.webp" style="display: none;">
                
                <div class="upload-preview" id="uploadPreview" style="display: none;"></div>
            </div>
        `;

        document.body.appendChild(modal);
        this.uploadModal = modal;

        // Setup dropzone
        const dropzone = document.getElementById('uploadDropzone');
        const fileInput = document.getElementById('fileUploadInput');

        dropzone.addEventListener('click', () => fileInput.click());

        fileInput.addEventListener('change', (e) => {
            if (e.target.files.length > 0) {
                this.handleFileSelect(e.target.files[0]);
            }
        });

        // Drag and drop
        dropzone.addEventListener('dragover', (e) => {
            e.preventDefault();
            dropzone.classList.add('drag-over');
        });

        dropzone.addEventListener('dragleave', () => {
            dropzone.classList.remove('drag-over');
        });

        dropzone.addEventListener('drop', (e) => {
            e.preventDefault();
            dropzone.classList.remove('drag-over');
            if (e.dataTransfer.files.length > 0) {
                this.handleFileSelect(e.dataTransfer.files[0]);
            }
        });

        // Close on background click
        modal.addEventListener('click', (e) => {
            if (e.target === modal) {
                this.closeUploadModal();
            }
        });
    }

    /**
     * Open upload modal
     */
    async openUploadModal() {
        this.closeDropdown();
        await this.fetchQuota();
        this.uploadModal.classList.add('show');
    }

    /**
     * Close upload modal
     */
    closeUploadModal() {
        this.uploadModal.classList.remove('show');
        document.getElementById('uploadPreview').style.display = 'none';
        document.getElementById('fileUploadInput').value = '';
    }

    /**
     * Fetch upload quota
     */
    async fetchQuota() {
        if (!window.currentRoomId) return;
        try {
            const response = await fetch(`/chatbot/api/rooms/${window.currentRoomId}/documents/quota/`, {
                headers: {
                    'X-CSRFToken': getCookie('csrftoken')
                }
            });

            if (response.ok) {
                const data = await response.json();
                this.currentQuota = data;
                this.updateQuotaDisplay(data);
            }
        } catch (error) {
            console.error('Quota fetch error:', error);
        }
    }

    /**
     * Update quota display
     */
    updateQuotaDisplay(quota) {
        const countEl = document.getElementById('quotaCount');
        if (!countEl) return;

        countEl.textContent = `${quota.remaining} / ${quota.total}`;

        // Color coding
        countEl.classList.remove('warning', 'danger');
        if (quota.remaining === 0) {
            countEl.classList.add('danger');
        } else if (quota.remaining <= quota.total * 0.2) {
            countEl.classList.add('warning');
        }
    }

    /**
     * Handle file selection
     */
    async handleFileSelect(file) {
        // Validate file type
        const validTypes = {
            'application/pdf': 'pdf',
            'image/jpeg': 'image',
            'image/jpg': 'image',
            'image/png': 'image',
            'image/gif': 'image',
            'image/webp': 'image'
        };

        if (!validTypes[file.type]) {
            this.showToast('Only PDFs and images are supported', 'error');
            return;
        }

        // Validate file size
        const maxSizes = {
            'pdf': 10 * 1024 * 1024,
            'image': 5 * 1024 * 1024
        };

        const fileType = validTypes[file.type];
        if (file.size > maxSizes[fileType]) {
            const maxMB = maxSizes[fileType] / (1024 * 1024);
            this.showToast(`${fileType.toUpperCase()} files must be under ${maxMB}MB`, 'error');
            return;
        }

        // Show preview
        this.showUploadPreview(file);

        // Upload
        await this.uploadFile(file);
    }

    /**
     * Show upload preview
     */
    showUploadPreview(file) {
        const preview = document.getElementById('uploadPreview');
        const icon = file.type.startsWith('image/') ? 'fa-file-image' : 'fa-file-pdf';
        const sizeKB = (file.size / 1024).toFixed(1);

        preview.innerHTML = `
            <div class="upload-preview-item">
                <div class="upload-preview-icon">
                    <i class="fas ${icon}"></i>
                </div>
                <div class="upload-preview-info">
                    <div class="upload-preview-name">${file.name}</div>
                    <div class="upload-preview-size">${sizeKB} KB</div>
                    <div class="upload-progress">
                        <div class="upload-progress-bar" id="uploadProgressBar" style="width: 0%"></div>
                    </div>
                </div>
            </div>
        `;
        preview.style.display = 'block';
    }

    /**
     * Upload file
     */
    async uploadFile(file) {
        if (!window.currentRoomId) return;
        const formData = new FormData();
        formData.append('file', file);

        const progressBar = document.getElementById('uploadProgressBar');

        try {
            // Simulate progress
            let progress = 0;
            const progressInterval = setInterval(() => {
                progress += 10;
                if (progressBar) progressBar.style.width = `${Math.min(progress, 90)}%`;
                if (progress >= 90) clearInterval(progressInterval);
            }, 100);

            const response = await fetch(`/chatbot/api/rooms/${window.currentRoomId}/documents/upload/`, {
                method: 'POST',
                headers: {
                    'X-CSRFToken': getCookie('csrftoken')
                },
                body: formData
            });

            clearInterval(progressInterval);
            if (progressBar) progressBar.style.width = '100%';

            const data = await response.json();

            if (response.ok) {
                this.showToast('Document uploaded! Mathia is indexing it now.', 'success');
                setTimeout(() => {
                    this.closeUploadModal();
                }, 1500);

                // Update quota
                await this.fetchQuota();
            } else {
                if (response.status === 429) {
                    this.showToast(`Upload limit reached. Resets in ${data.resets_in_hours} hours.`, 'warning');
                } else {
                    this.showToast(data.error || 'Upload failed', 'error');
                }
            }
        } catch (error) {
            console.error('Upload error:', error);
            this.showToast('Network error. Please try again.', 'error');
        }
    }

    /**
     * Show toast notification
     */
    showToast(message, type = 'success') {
        const toast = document.createElement('div');
        toast.className = `action-toast ${type}`;

        const icons = {
            success: 'fa-check-circle',
            error: 'fa-exclamation-circle',
            warning: 'fa-exclamation-triangle'
        };

        toast.innerHTML = `
            <i class="fas ${icons[type]}"></i>
            <div class="action-toast-message">${message}</div>
        `;

        document.body.appendChild(toast);

        setTimeout(() => {
            toast.remove();
        }, 4000);
    }

    /**
     * Escape HTML for safe insertion
     */
    escapeHtml(text) {
        const div = document.createElement('div');
        div.textContent = text;
        return div.innerHTML.replace(/'/g, "\\'");
    }
}

// Initialize on page load
document.addEventListener('DOMContentLoaded', () => {
    window.messageActions = new MessageActions();
});
