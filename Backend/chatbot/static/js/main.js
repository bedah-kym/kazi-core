// Global variables
let chatSocket = null;
let currentRoomId = roomName; // roomName defined in template
let otherUserMember = otherUser; // otherUser defined in template

// Initialize first connection
connectToChat(roomName);

function connectToChat(roomId) {
    if (chatSocket) {
        chatSocket.close();
    }

    console.log(`🔌 Connecting to room ${roomId}...`);
    chatSocket = new ReconnectingWebSocket(
        'ws://' + window.location.host + '/ws/chat/' + roomId + '/'
    );

    // Initialize chat connection
    chatSocket.onopen = function (e) {
        console.log('✅ WebSocket connection established for room ' + roomId);
        document.querySelectorAll('.status-dot').forEach(dot => {
            dot.classList.remove('online', 'offline');
        });
        FetchMessages(roomId);
    };

    chatSocket.onclose = function (e) {
        console.error('❌ Chat socket closed');
        document.querySelectorAll('.status-dot').forEach(dot => {
            dot.classList.remove('online');
            dot.classList.add('offline');
        });
    };

    // WebSocket message handling
    chatSocket.onmessage = function (e) {
        const data = JSON.parse(e.data);
        if (data.command === 'typing' && data.from !== username) {
            document.getElementById('typing-user').textContent = data.from;
            showTypingIndicator();
            clearTimeout(typingTimer);
            typingTimer = setTimeout(hideTypingIndicator, 3000);
            return;
        }
        if (data.command === 'presence_snapshot' || data.command === 'presence') { handlePresenceUpdate(data); return; }
        if (data.command === 'messages') {
            // Clear existing messages first if it's a fresh fetch
            // But usually fetch_messages command clears lazily or we clear on switch
            for (let i = data.messages.length - 1; i >= 0; i--) createMessage(data.messages[i]);
            scrollToLastMessage();
            return;
        }
        if (data.command === 'new_message') { createMessage(data.message); scrollToLastMessage(); return; }
        if (data.command === 'error') { console.error('Error from server:', data.message); return; }
        console.warn('Unknown command:', data);
    };
}

/**
 * Switch Room Function (SPA Style)
 */
function switchRoom(event, roomId, roomDisplayName) {
    event.preventDefault();
    console.log(`🔄 Switching to room ${roomId} (${roomDisplayName})`);

    if (currentRoomId === roomId) return; // Already here

    // 1. Update UI State
    document.querySelectorAll('.chat-list li').forEach(li => li.classList.remove('active'));
    document.getElementById(`room-li-${roomId}`)?.classList.add('active');

    // Update Header
    document.querySelector('.chat-header h6').textContent = roomDisplayName;
    // Update global var for other logic
    otherUserMember = roomDisplayName;
    if (typeof otherUser !== 'undefined') {
        otherUser = roomDisplayName;
    }

    // Clear Chat History
    document.getElementById('top-chat').innerHTML = '';

    // 2. Update URL (without reload)
    const newUrl = `/chatbot/home/${roomId}/`;
    window.history.pushState({ path: newUrl, roomId: roomId }, '', newUrl);

    // 3. Reconnect WebSocket
    currentRoomId = roomId;
    roomName = roomId; // Update global legacy var
    connectToChat(roomId);
}

// Handle Browser Back Button
window.addEventListener('popstate', function (event) {
    if (event.state && event.state.roomId) {
        // We could store names in state to fully restore, 
        // but for now just reloading might be safer to sync everything, 
        // or we can just reconnect
        location.reload();
    }
});

// Hamburger menu toggle
(function () {
    let menuOpen = false;

    window.addEventListener('load', function () {
        const menuToggle = document.getElementById('menuToggle');
        console.log('Menu toggle button found:', menuToggle);
        if (menuToggle) {
            menuToggle.onclick = function (e) {
                e.preventDefault();
                e.stopPropagation();
                menuOpen = !menuOpen;
                document.body.classList.toggle('people-open', menuOpen);
                return false;
            };
        }

        // Close menu when clicking outside
        document.body.onclick = function (e) {
            if (menuOpen) {
                const peopleList = document.querySelector('.people-list');
                const menuToggle = document.getElementById('menuToggle');
                if (peopleList && menuToggle) {
                    const clickedInsideMenu = peopleList.contains(e.target);
                    const clickedToggle = menuToggle.contains(e.target);
                    if (!clickedInsideMenu && !clickedToggle) {
                        menuOpen = false;
                        document.body.classList.remove('people-open');
                    }
                }
            }
        };
    });
})();

// ... (Rest of existing code: createMessage, FetchMessages, etc.)

// Message handling
// Configure Marked options
if (typeof marked !== 'undefined') {
    marked.use({
        breaks: true,
        gfm: true
    });
}

function createMessage(data) {
    if (!data || !data.member || !data.content || !data.timestamp) {
        console.error('Invalid message data:', data);
        return;
    }
    const chatHistory = document.querySelector('.chat-history');
    const formattedTime = new Date(data.timestamp).toLocaleTimeString();
    const time = `<span class="time-label">${formattedTime}</span>`;

    const msgListTag = document.createElement('li');
    msgListTag.classList.add(data.member === username ? 'my' : 'other');
    msgListTag.className = 'clearfix';
    msgListTag.id = 'tracker';

    const msgDivTag = document.createElement('div');
    const msgdivtag = document.createElement('p'); // User name
    const msgSpanTag = document.createElement('span'); // Container for message content
    const msgpTag = document.createElement('div'); // Time wrapper
    const msgTextTag = document.createElement('div'); // Actual message content

    // MARKDOWN & SECURITY PROCESSING
    let rawContent = data.content;
    let safeHtml = rawContent;

    // Check if libraries are loaded
    if (typeof marked !== 'undefined' && typeof DOMPurify !== 'undefined') {
        try {
            // 1. Parse Markdown
            const parsedHtml = marked.parse(rawContent);

            // 2. Sanitize HTML (PREVENT XSS)
            // Allow img tags but sanitise their src
            safeHtml = DOMPurify.sanitize(parsedHtml, {
                ADD_TAGS: ['img', 'code', 'pre'],
                ADD_ATTR: ['src', 'alt', 'class', 'style', 'target']
            });
        } catch (e) {
            console.error('Markdown processing error:', e);
            // Fallback to text content if error
            safeHtml = rawContent.replace(/</g, "&lt;").replace(/>/g, "&gt;");
        }
    }

    msgTextTag.innerHTML = safeHtml;
    msgdivtag.innerHTML = data.member;
    msgpTag.innerHTML = time;

    if (data.member === username) {
        msgDivTag.className = 'message-data text-end';
        msgTextTag.className = 'message my-message';
    } else {
        msgDivTag.className = 'message-data';
        msgTextTag.className = 'message other-message';
    }

    // Add sentimage class if it contains an image (legacy check + new markdown check)
    if (data.content.includes('<img') || (safeHtml && safeHtml.includes('<img'))) {
        msgTextTag.classList.add('sentimage');
    }

    msgdivtag.className = 'user-name';
    msgSpanTag.className = 'message-data-time';
    msgpTag.className = 'time-label';

    msgListTag.appendChild(msgDivTag);
    msgDivTag.appendChild(msgpTag);
    msgDivTag.appendChild(msgSpanTag);
    msgSpanTag.appendChild(msgTextTag);
    msgTextTag.appendChild(msgdivtag);

    // Apply Syntax Highlighting & Copy Buttons
    if (typeof hljs !== 'undefined') {
        msgTextTag.querySelectorAll('pre code').forEach((block) => {
            try {
                // Force highlight even if language class is missing
                hljs.highlightElement(block);
            } catch (e) {
                console.warn('Highlight warning:', e);
            }

            // Add Copy Button
            const pre = block.parentElement;
            if (pre && !pre.querySelector('.copy-btn')) {
                // Ensure positioning
                if (window.getComputedStyle(pre).position === 'static') {
                    pre.style.position = 'relative';
                }

                const btn = document.createElement('button');
                btn.className = 'copy-btn';
                btn.innerHTML = '<i class="fas fa-copy"></i> Copy';
                // Inline styles for safety
                btn.style.cssText = 'position: absolute; top: 5px; right: 5px; padding: 4px 8px; font-size: 12px; border: none; border-radius: 4px; cursor: pointer; background: rgba(255,255,255,0.1); color: #fff; z-index: 10;';

                btn.onclick = (e) => {
                    e.preventDefault();
                    e.stopPropagation();
                    navigator.clipboard.writeText(block.textContent).then(() => {
                        btn.innerHTML = '<i class="fas fa-check"></i> Copied!';
                        setTimeout(() => btn.innerHTML = '<i class="fas fa-copy"></i> Copy', 2000);
                    }).catch(err => console.error('Copy failed:', err));
                };
                pre.appendChild(btn);
            }
        });
    }

    document.querySelector('#top-chat').appendChild(msgListTag);
    chatHistory.scrollTop = chatHistory.scrollHeight;
}

function FetchMessages(rId) {
    // Use rId if provided, otherwise global roomName
    const targetRoom = rId || roomName;
    chatSocket.send(JSON.stringify({
        "command": "fetch_messages",
        "chatid": targetRoom
    }));
}

function scrollToLastMessage() {
    const elements = document.querySelectorAll('#tracker');
    if (elements.length > 0) elements[elements.length - 1].scrollIntoView();
}

// Typing indicator
function showTypingIndicator() { document.querySelector('.typing-indicator')?.style.setProperty('display', 'flex'); }
function hideTypingIndicator() { document.querySelector('.typing-indicator')?.style.setProperty('display', 'none'); }

function getCookie(name) {
    const cookies = document.cookie.split(';');
    for (let cookie of cookies) {
        cookie = cookie.trim();
        if (cookie.startsWith(name + '=')) return cookie.substring(name.length + 1);
    }
    return null;
}

let typingTimer;
const chatInput = document.querySelector('#chat-message-input');
if (chatInput) {
    chatInput.addEventListener('input', function () {
        chatSocket.send(JSON.stringify({ 'command': 'typing', 'from': username, "chatid": roomName }));
    });
    chatInput.addEventListener('keyup', function (e) { if (e.keyCode === 13) document.querySelector('#chat-message-submit')?.click(); });
}

const chatSubmit = document.querySelector('#chat-message-submit');
if (chatSubmit) chatSubmit.addEventListener('click', function () {
    const messageInputDom = document.querySelector('#chat-message-input');
    const message = messageInputDom?.value?.trim();
    if (message) {
        chatSocket.send(JSON.stringify({ 'message': message, 'from': username, 'command': 'new_message', "chatid": roomName }));
        if (messageInputDom) messageInputDom.value = '';
    }
});

// File handling
function initFileHandlers() {
    const uploadBtn = document.querySelector('#upload');
    const fileInputEl = document.querySelector('#fileInput');
    if (uploadBtn) uploadBtn.addEventListener('click', () => fileInputEl?.click());
    if (!fileInputEl) return;

    fileInputEl.addEventListener('change', function (event) {
        const file = event.target.files[0];
        if (!file) return;
        const filePreview = document.querySelector('.file-preview');
        const previewContent = filePreview?.querySelector('.preview-content');
        const fileName = filePreview?.querySelector('.file-name');
        const progressBar = filePreview?.querySelector('.upload-progress-bar');
        if (fileName) fileName.textContent = file.name;
        if (previewContent) previewContent.innerHTML = '';
        if (progressBar) progressBar.style.width = '0%';
        if (filePreview) filePreview.style.display = 'block';

        if (file.type.startsWith('image/') && previewContent) {
            const reader = new FileReader();
            reader.onload = function (e) { previewContent.innerHTML = `<img src="${e.target.result}" alt="preview">`; };
            reader.readAsDataURL(file);
        } else if (previewContent) {
            previewContent.innerHTML = `<i class="fas fa-file fa-3x"></i>`;
        }

        const formData = new FormData(); formData.append('file', file);
        const csrfToken = getCookie('csrftoken');

        let progress = 0;
        const progressInterval = setInterval(() => {
            progress += 10; if (progressBar) progressBar.style.width = `${progress}%`;
            if (progress >= 100) { clearInterval(progressInterval); setTimeout(() => { if (filePreview) filePreview.style.display = 'none'; }, 500); }
        }, 200);

        fetch('/uploads/', { method: 'POST', body: formData, headers: { 'X-CSRFToken': csrfToken } })
            .then(response => response.json())
            .then(data => {
                const messageHtml = file.type.startsWith('image/')
                    ? `<img src="${data.fileUrl}" alt="uploaded image" />`
                    : `<a href="${data.fileUrl}" target="_blank">${file.name}</a>`;

                chatSocket.send(JSON.stringify({
                    message: messageHtml,
                    from: username,
                    command: 'new_message',
                    chatid: roomName
                }));
            })
            .catch(error => console.error('Error uploading file:', error));
    });

    const closePreview = document.querySelector('.close-preview');
    if (closePreview) closePreview.addEventListener('click', function () {
        const fp = document.querySelector('.file-preview');
        if (fp) fp.style.display = 'none';
        const fi = document.querySelector('#fileInput');
        if (fi) fi.value = '';
    });
}

// WebSocket message handling
chatSocket.onmessage = function (e) {
    const data = JSON.parse(e.data);
    if (data.command === 'typing' && data.from !== username) { document.getElementById('typing-user').textContent = data.from; showTypingIndicator(); clearTimeout(typingTimer); typingTimer = setTimeout(hideTypingIndicator, 3000); return; }
    if (data.command === 'presence_snapshot' || data.command === 'presence') { handlePresenceUpdate(data); return; }
    if (data.command === 'messages') { for (let i = data.messages.length - 1; i >= 0; i--) createMessage(data.messages[i]); scrollToLastMessage(); return; }
    if (data.command === 'new_message') { createMessage(data.message); scrollToLastMessage(); return; }
    if (data.command === 'error') { console.error('Error from server:', data.message); return; }
    console.warn('Unknown command:', data);
};
// ============================================
// PRESENCE MANAGEMENT SYSTEM
// ============================================

// Track all user statuses globally
const userPresenceMap = new Map();

/**
 * Log all current user states - useful for debugging
 */
function logAllUserStates() {
    console.log('=== ALL USER PRESENCE STATES ===');
    if (userPresenceMap.size === 0) {
        console.log('No users tracked yet');
        return;
    }

    userPresenceMap.forEach((presence, username) => {
        const statusIcon = presence.status === 'online' ? '🟢' : '🔴';
        const lastSeenText = presence.lastSeen
            ? humanizeLastSeen(new Date(presence.lastSeen))
            : 'never';
        console.log(`${statusIcon} ${username}: ${presence.status} (last seen: ${lastSeenText})`);
    });

    const onlineCount = Array.from(userPresenceMap.values())
        .filter(p => p.status === 'online').length;
    console.log(`Total: ${userPresenceMap.size} users, ${onlineCount} online`);
    console.log('================================');
}

/**
 * Update presence for a specific user
 */
function updateUserPresence(user, status, lastSeen) {
    console.log(`📍 Updating presence for ${user}: ${status}`);

    // Update the global presence map
    userPresenceMap.set(user, { status, lastSeen });

    // Update sidebar dots for this specific user
    const dots = document.querySelectorAll(`[data-user="${user}"], .sidebar-dot[data-user="${user}"]`);
    console.log(`Found ${dots.length} sidebar dots for ${user}`);

    dots.forEach(dot => {
        dot.classList.toggle('online', status === 'online');
        dot.classList.toggle('offline', status !== 'online');
        if (lastSeen) {
            dot.setAttribute('title', `Last seen: ${new Date(lastSeen).toLocaleString()}`);
        }
    });

    // Update header ONLY if this is the specific user for this chat
    // For 1-on-1 chats, otherUser is defined
    // For group chats, you might want different logic
    if (typeof otherUser !== 'undefined' && user === otherUser) {
        console.log(`📌 Updating header for ${user} (this is otherUser)`);
        updateHeaderPresence(user, status, lastSeen);
    } else {
        console.log(`⏭️  Skipping header update for ${user} (otherUser is: ${typeof otherUser !== 'undefined' ? otherUser : 'undefined'})`);
    }

    // Log all states after each update
    logAllUserStates();
}

/**
 * Update the header presence indicator
 */
function updateHeaderPresence(user, status, lastSeen) {
    const headerDot = document.getElementById('header-presence');
    const headerLast = document.getElementById('header-lastseen');

    if (headerDot) {
        headerDot.classList.toggle('online', status === 'online');
        headerDot.classList.toggle('offline', status !== 'online');
    }

    if (headerLast) {
        if (status === 'online') {
            headerLast.textContent = 'Online now';
            headerLast.style.color = '#4caf50';
        } else if (lastSeen) {
            headerLast.textContent = humanizeLastSeen(new Date(lastSeen));
            headerLast.style.color = '#999';
        } else {
            headerLast.textContent = 'Offline';
            headerLast.style.color = '#999';
        }
    }

    console.log(`✅ Header updated for ${user}: ${status}`);
}

/**
 * Handle incoming presence updates from WebSocket
 */
function handlePresenceUpdate(data) {
    console.log('🔔 handlePresenceUpdate called with:', data);

    if (data.command === 'presence_snapshot') {
        const presenceList = data.presence || [];
        console.log(`📸 Processing presence snapshot (${presenceList.length} users)`);

        // Clear existing presence data
        userPresenceMap.clear();

        // Process all users in the snapshot
        presenceList.forEach(entry => {
            console.log(`  📦 Snapshot: ${entry.user} -> ${entry.status}`);
            updateUserPresence(entry.user, entry.status, entry.last_seen);
        });

        // After processing snapshot, update group chat header if needed
        updateGroupChatHeader();

    } else if (data.command === 'presence') {
        console.log(`🔄 Processing individual presence update: ${data.user} -> ${data.status}`);
        updateUserPresence(data.user, data.status, data.last_seen);

        // Update group header after individual update
        updateGroupChatHeader();
    }
}

/**
 * For GROUP CHATS: Show count of online users
 */
function updateGroupChatHeader() {
    // Skip if this is a 1-on-1 chat
    if (typeof otherUser !== 'undefined') {
        console.log('⏭️  Skipping group header update (this is 1-on-1 chat)');
        return;
    }

    const headerLast = document.getElementById('header-lastseen');
    if (!headerLast) return;

    const onlineCount = Array.from(userPresenceMap.values())
        .filter(p => p.status === 'online').length;

    const totalCount = userPresenceMap.size;

    if (onlineCount > 0) {
        headerLast.textContent = `${onlineCount} of ${totalCount} members online`;
        headerLast.style.color = '#4caf50';
    } else {
        headerLast.textContent = `${totalCount} members`;
        headerLast.style.color = '#999';
    }

    console.log(`👥 Group header updated: ${onlineCount}/${totalCount} online`);
}

/**
 * Get current presence status for a user
 */
function getUserPresence(username) {
    return userPresenceMap.get(username) || { status: 'offline', lastSeen: null };
}

/**
 * Check if a specific user is online
 */
function isUserOnline(username) {
    const presence = userPresenceMap.get(username);
    return presence ? presence.status === 'online' : false;
}

/**
 * Convert a date to human-readable "last seen" format
 */
function humanizeLastSeen(date) {
    if (!date) return '';
    const now = new Date();
    const diff = Math.floor((now - date) / 1000);

    if (diff < 10) return 'just now';
    if (diff < 60) return `${diff}s ago`;
    if (diff < 3600) return `${Math.floor(diff / 60)}m ago`;
    if (diff < 86400) return `${Math.floor(diff / 3600)}h ago`;

    const days = Math.floor(diff / 86400);
    if (days === 1) return `Yesterday at ${date.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}`;
    if (days < 7) return `${days}d ago`;

    return date.toLocaleDateString();
}

// ============================================
// EXPOSE TO WINDOW FOR DEBUGGING
// ============================================
window.presenceDebug = {
    logAllStates: logAllUserStates,
    getPresence: getUserPresence,
    isOnline: isUserOnline,
    getUserMap: () => userPresenceMap,
    forceUpdate: (user, status, lastSeen) => {
        console.log(`🔧 Manual presence update triggered`);
        updateUserPresence(user, status, lastSeen);
    }
};

console.log('💡 Presence system loaded. Debug commands available via window.presenceDebug');
console.log('   - window.presenceDebug.logAllStates()');
console.log('   - window.presenceDebug.getPresence(username)');
console.log('   - window.presenceDebug.isOnline(username)');
console.log('   - window.presenceDebug.getUserMap()');
// ============================================
// PEOPLE SEARCH FUNCTIONALITY
// ============================================
function levenshtein(a, b) { a = String(a || ''); b = String(b || ''); if (a === b) return 0; if (!a.length) return b.length; if (!b.length) return a.length; let prev = new Array(b.length + 1).fill(0).map((_, i) => i); for (let i = 0; i < a.length; i++) { let curr = [i + 1]; for (let j = 0; j < b.length; j++) { curr[j + 1] = Math.min(curr[j] + 1, prev[j + 1] + 1, prev[j] + (a[i] === b[j] ? 0 : 1)); } prev = curr; } return prev[b.length]; }

function animateItemVisibility(li, show) { li.classList.add('people-anim'); if (show) { li.style.display = ''; requestAnimationFrame(() => li.classList.remove('fade-hidden')); } else { li.classList.add('fade-hidden'); li.addEventListener('transitionend', function onEnd(e) { if (e.propertyName === 'opacity') { li.style.display = 'none'; li.removeEventListener('transitionend', onEnd); } }); } }

function humanizeLastSeen(date) { if (!date) return ''; const now = new Date(); const diff = Math.floor((now - date) / 1000); if (diff < 10) return 'just now'; if (diff < 60) return `${diff}s ago`; if (diff < 3600) return `${Math.floor(diff / 60)}m ago`; if (diff < 86400) return `${Math.floor(diff / 3600)}h ago`; const days = Math.floor(diff / 86400); if (days === 1) return `Yesterday ${date.toLocaleTimeString()}`; return `${days}d ago`; }

function toggleEmojiPicker() { const element = document.querySelector('em-emoji-picker'); if (!element) return; element.style.display = element.style.display === 'none' ? '' : 'none'; }

function initPeopleSearch() {
    const input = document.getElementById('search'); if (!input) return; let timeout; input.addEventListener('input', (e) => { clearTimeout(timeout); timeout = setTimeout(() => performPeopleSearch(e.target.value.trim()), 180); }); input.addEventListener('search', (e) => { if (!e.target.value) performPeopleSearch(''); }); document.addEventListener('keydown', (e) => { if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 'k') { e.preventDefault(); input.focus(); } });
}

function performPeopleSearch(query) { const raw = (query || '').trim(); const q = raw.toLowerCase(); const tokenMatch = raw.match(/^(u:|e:)(.*)$/i); let token = null; let tokenQuery = ''; if (tokenMatch) { token = tokenMatch[1].toLowerCase().replace(':', ''); tokenQuery = tokenMatch[2].trim().toLowerCase(); } document.querySelectorAll('.chat-list li').forEach(li => { const nameEl = li.querySelector('.fw-bold'); if (!nameEl) return; if (!nameEl.dataset.originalName) { const orig = Array.from(nameEl.childNodes).filter(n => n.nodeType === Node.TEXT_NODE).map(n => n.textContent).join('').trim(); nameEl.dataset.originalName = orig; } const original = nameEl.dataset.originalName || ''; if (!q) { restoreNameText(nameEl); animateItemVisibility(li, true); return; } const searchable = [original.toLowerCase()]; const anchor = li.querySelector('a'); const datasetSource = (li.dataset && Object.keys(li.dataset).length) ? li.dataset : (anchor?.dataset || {}); if (datasetSource.username) searchable.push(String(datasetSource.username).toLowerCase()); if (datasetSource.email) searchable.push(String(datasetSource.email).toLowerCase()); if (datasetSource.user) searchable.push(String(datasetSource.user).toLowerCase()); let matches = false; if (token) { const field = token === 'u' ? (datasetSource.username || datasetSource.user || '') : (datasetSource.email || ''); matches = field ? field.toLowerCase().includes(tokenQuery) : original.toLowerCase().includes(tokenQuery); } else { if (searchable.some(s => s.includes(q))) matches = true; else if (q.length >= 3) { const best = Math.max(...searchable.map(s => { const dist = levenshtein(q, s); return 1 - (dist / Math.max(q.length, s.length, 1)); })); matches = best >= 0.45; } } if (matches) { highlightNameText(nameEl, q); animateItemVisibility(li, true); } else { restoreNameText(nameEl); animateItemVisibility(li, false); } }); }

function restoreNameText(nameEl) { const original = nameEl.dataset.originalName || ''; Array.from(nameEl.childNodes).filter(n => n.nodeType === Node.TEXT_NODE).forEach(n => n.remove()); if (original) nameEl.insertBefore(document.createTextNode(original + ' '), nameEl.firstChild || null); }

function highlightNameText(nameEl, query) { const original = nameEl.dataset.originalName || ''; const regex = new RegExp(escapeRegex(query), 'ig'); const textNodes = Array.from(nameEl.childNodes).filter(n => n.nodeType === Node.TEXT_NODE); const text = textNodes.map(n => n.textContent).join(''); if (!regex.test(text)) { restoreNameText(nameEl); nameEl.classList.add('fuzzy-match'); setTimeout(() => nameEl.classList.remove('fuzzy-match'), 900); return; } const parts = text.split(regex); const matches = text.match(regex) || []; const frag = document.createDocumentFragment(); for (let i = 0; i < parts.length; i++) { frag.appendChild(document.createTextNode(parts[i])); if (i < matches.length) { const span = document.createElement('span'); span.className = 'search-highlight'; span.textContent = matches[i]; frag.appendChild(span); } } textNodes.forEach(n => n.remove()); nameEl.insertBefore(frag, nameEl.firstChild || null); if (nameEl.firstChild?.nodeType === Node.TEXT_NODE && !/\s$/.test(nameEl.firstChild.textContent)) nameEl.firstChild.textContent += ' '; }

function escapeRegex(string) { return string.replace(/[.*+?^${}()|[\]\\]/g, '\\$&'); }

// Emoji & init
function initEmojiPicker() {
    const savedTheme = localStorage.getItem('theme') || 'light';
    document.body.setAttribute('data-theme', savedTheme);
    const pickerOptions = { onEmojiSelect: function (emoji) { const input = document.getElementById('chat-message-input'); if (input) input.value += emoji.native; toggleEmojiPicker(); }, theme: savedTheme };
    try { const picker = new EmojiMart.Picker(pickerOptions); picker.style.display = 'none'; document.body.appendChild(picker); } catch (e) { /* emoji lib may not be present */ }
    document.getElementById('emoji')?.addEventListener('click', toggleEmojiPicker);
    document.getElementById('darkModeToggle')?.addEventListener('click', () => {
        const currentTheme = document.body.getAttribute('data-theme');
        const newTheme = currentTheme === 'dark' ? 'light' : 'dark';
        document.body.setAttribute('data-theme', newTheme); localStorage.setItem('theme', newTheme); document.querySelector('em-emoji-picker')?.setAttribute('theme', newTheme);
    });
}

document.addEventListener('DOMContentLoaded', function () { initEmojiPicker(); initFileHandlers(); initPeopleSearch(); });

// placeholder
function placeholder() { return null; }
