// Global variables
// MULTI-ROOM ARCHITECTURE
const activeRooms = {}; // { roomId: { socket: WebSocket, initialized: bool } }
let currentRoomId = roomName; // roomName defined in template (initial room)
let usernameGlobal = username; // username defined in template
const userPresenceMap = new Map();

// Helper function to get current socket
function getCurrentSocket() {
    const room = activeRooms[currentRoomId];
    return room ? room.socket : null;
}

// Initialize
initRoom(roomName);

/**
 * Initialize a room (UI + Socket)
 */
function initRoom(roomId) {
    console.log(`🚀 Initializing room ${roomId}`);

    // 1. Create UI Container if missing
    getOrCreateRoomUI(roomId);

    // 2. Connect Socket if missing
    if (!activeRooms[roomId] || !activeRooms[roomId].socket) {
        connectToChat(roomId);
    }

    // 3. Set as Active
    activateRoomUI(roomId);
}

/**
 * Ensure the DOM elements for this room exist
 */
function getOrCreateRoomUI(roomId) {
    const container = document.getElementById('chat-rooms-container');
    let roomDiv = document.getElementById(`room-container-${roomId}`);

    if (!roomDiv) {
        // Create wrapper
        roomDiv = document.createElement('div');
        roomDiv.id = `room-container-${roomId}`;
        roomDiv.className = 'room-view';
        roomDiv.style.display = 'none';

        // Create Message List
        const ul = document.createElement('ul');
        ul.id = `messages-room-${roomId}`;
        ul.className = 'list-unstyled mb-0';

        // Create Typing Indicator specific to this room
        const typingDiv = document.createElement('div');
        typingDiv.className = 'typing-indicator';
        typingDiv.id = `typing-indicator-${roomId}`;
        typingDiv.style.display = 'none';
        typingDiv.innerHTML = '<em class="user-name"></em><span></span><span></span><span></span>';

        roomDiv.appendChild(ul);
        roomDiv.appendChild(typingDiv);
        container.appendChild(roomDiv);
    }
    return roomDiv;
}

/**
 * Switch valid visible room
 */
function activateRoomUI(roomId) {
    // Hide all rooms
    document.querySelectorAll('.room-view').forEach(el => el.style.display = 'none');

    // Show target room
    const target = getOrCreateRoomUI(roomId);
    target.style.display = 'block';

    // Scroll to bottom
    const messagesList = document.getElementById(`messages-room-${roomId}`);
    if (messagesList) {
        messagesList.scrollTop = messagesList.scrollHeight;
    }
}

function connectToChat(roomId) {
    if (activeRooms[roomId] && activeRooms[roomId].socket) {
        console.log(`Checking connection for ${roomId}: ${activeRooms[roomId].socket.readyState}`);
        if (activeRooms[roomId].socket.readyState === WebSocket.OPEN) return;
    }

    console.log(`🔌 Connecting socket for room ${roomId}...`);
    const socket = new ReconnectingWebSocket(
        'ws://' + window.location.host + '/ws/chat/' + roomId + '/'
    );

    activeRooms[roomId] = { socket: socket, initialized: false };

    // Initialize chat connection
    socket.onopen = function (e) {
        console.log('✅ WebSocket connection established for room ' + roomId);
        const ul = document.getElementById(`messages-room-${roomId}`);
        if (ul && ul.children.length === 0) {
            FetchMessages(roomId);
        }
    };

    socket.onclose = function (e) {
        console.error(`❌ Chat socket closed for room ${roomId}`);
    };

    // WebSocket message handling
    socket.onmessage = function (e) {
        const data = JSON.parse(e.data);
        const rId = roomId;

        if (data.command === 'typing' && data.from !== usernameGlobal) {
            const indicator = document.getElementById(`typing-indicator-${rId}`);
            if (indicator) {
                indicator.querySelector('.user-name').textContent = data.from;
                indicator.style.display = 'flex';
                setTimeout(() => indicator.style.display = 'none', 3000);
            }
            return;
        }
        if (data.command === 'presence_snapshot' || data.command === 'presence') {
            handlePresenceUpdate(data);
            return;
        }
        if (data.command === 'messages') {
            for (let i = data.messages.length - 1; i >= 0; i--) {
                createMessage(data.messages[i], rId);
            }
            scrollToLastMessage(rId);
            return;
        }
        if (data.command === 'new_message') {
            createMessage(data.message, rId);
            scrollToLastMessage(rId);
            return;
        }
        if (data.command === 'error') {
            console.error('Error from server:', data.message);
            return;
        }
    };
}

/**
 * Switch Room Function (SPA Style + Concurrency)
 */
function switchRoom(event, roomId, roomDisplayName) {
    event.preventDefault();
    console.log(`🔄 Switching to room ${roomId} (${roomDisplayName})`);

    if (currentRoomId === roomId) return;

    // 1. Update UI Sidebar State
    document.querySelectorAll('.chat-list li').forEach(li => li.classList.remove('active'));
    document.getElementById(`room-li-${roomId}`)?.classList.add('active');

    // Update Header
    document.querySelector('.chat-header h6').textContent = roomDisplayName;

    // 2. Update URL (without reload)
    const newUrl = `/chatbot/home/${roomId}/`;
    window.history.pushState({ path: newUrl, roomId: roomId }, '', newUrl);

    // 3. Multi-Room Switch
    currentRoomId = roomId;
    initRoom(roomId);
}

// Handle Browser Back Button
window.addEventListener('popstate', function (event) {
    if (event.state && event.state.roomId) {
        location.reload();
    }
});

// Hamburger menu toggle
(function () {
    let menuOpen = false;

    window.addEventListener('load', function () {
        const menuToggle = document.getElementById('menuToggle');
        if (menuToggle) {
            menuToggle.onclick = function (e) {
                e.preventDefault();
                e.stopPropagation();
                menuOpen = !menuOpen;
                document.body.classList.toggle('people-open', menuOpen);
                return false;
            };
        }

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

// Configure Marked options
if (typeof marked !== 'undefined') {
    marked.use({
        breaks: true,
        gfm: true
    });
}

// FIX: createMessage now accepts roomId parameter
function createMessage(data, roomId) {
    if (!data || !data.member || !data.content || !data.timestamp) {
        console.error('Invalid message data:', data);
        return;
    }

    // Use provided roomId or fall back to currentRoomId
    const targetRoomId = roomId || currentRoomId;
    const messagesList = document.getElementById(`messages-room-${targetRoomId}`);

    if (!messagesList) {
        console.error(`Messages list not found for room ${targetRoomId}`);
        return;
    }

    const formattedTime = new Date(data.timestamp).toLocaleTimeString();
    const time = `<span class="time-label">${formattedTime}</span>`;

    const msgListTag = document.createElement('li');
    msgListTag.classList.add(data.member === username ? 'my' : 'other');
    msgListTag.className = 'clearfix';
    msgListTag.id = 'tracker';

    const msgDivTag = document.createElement('div');
    const msgdivtag = document.createElement('p');
    const msgSpanTag = document.createElement('span');
    const msgpTag = document.createElement('div');
    const msgTextTag = document.createElement('div');

    // MARKDOWN & SECURITY PROCESSING
    let rawContent = data.content;
    let safeHtml = rawContent;

    if (typeof marked !== 'undefined' && typeof DOMPurify !== 'undefined') {
        try {
            const parsedHtml = marked.parse(rawContent);
            safeHtml = DOMPurify.sanitize(parsedHtml, {
                ADD_TAGS: ['img', 'code', 'pre'],
                ADD_ATTR: ['src', 'alt', 'class', 'style', 'target']
            });
        } catch (e) {
            console.error('Markdown processing error:', e);
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

    // Syntax Highlighting & Copy Buttons
    if (typeof hljs !== 'undefined') {
        msgTextTag.querySelectorAll('pre code').forEach((block) => {
            try {
                hljs.highlightElement(block);
            } catch (e) {
                console.warn('Highlight warning:', e);
            }

            const pre = block.parentElement;
            if (pre && !pre.querySelector('.copy-btn')) {
                if (window.getComputedStyle(pre).position === 'static') {
                    pre.style.position = 'relative';
                }

                const btn = document.createElement('button');
                btn.className = 'copy-btn';
                btn.innerHTML = '<i class="fas fa-copy"></i> Copy';
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

    messagesList.appendChild(msgListTag);

    // Auto-scroll if we're viewing this room
    if (targetRoomId === currentRoomId) {
        messagesList.scrollTop = messagesList.scrollHeight;
    }
}

// FIX: FetchMessages now uses room-specific socket
function FetchMessages(roomId) {
    const targetRoom = roomId || currentRoomId;
    const room = activeRooms[targetRoom];

    if (!room || !room.socket) {
        console.error(`No socket available for room ${targetRoom}`);
        return;
    }

    room.socket.send(JSON.stringify({
        "command": "fetch_messages",
        "chatid": targetRoom
    }));
}

// FIX: scrollToLastMessage now room-specific
function scrollToLastMessage(roomId) {
    const targetRoom = roomId || currentRoomId;
    const messagesList = document.getElementById(`messages-room-${targetRoom}`);
    if (messagesList) {
        messagesList.scrollTop = messagesList.scrollHeight;
    }
}

function getCookie(name) {
    const cookies = document.cookie.split(';');
    for (let cookie of cookies) {
        cookie = cookie.trim();
        if (cookie.startsWith(name + '=')) return cookie.substring(name.length + 1);
    }
    return null;
}

// FIX: Typing indicator now uses current socket
let typingTimer;
const chatInput = document.querySelector('#chat-message-input');
if (chatInput) {
    chatInput.addEventListener('input', function () {
        const socket = getCurrentSocket();
        if (socket && socket.readyState === WebSocket.OPEN) {
            socket.send(JSON.stringify({
                'command': 'typing',
                'from': username,
                "chatid": currentRoomId
            }));
        }
    });

    chatInput.addEventListener('keyup', function (e) {
        if (e.keyCode === 13) {
            document.querySelector('#chat-message-submit')?.click();
        }
    });
}

// FIX: Submit button now uses current socket
const chatSubmit = document.querySelector('#chat-message-submit');
if (chatSubmit) {
    chatSubmit.addEventListener('click', function () {
        const messageInputDom = document.querySelector('#chat-message-input');
        const message = messageInputDom?.value?.trim();

        if (message) {
            const socket = getCurrentSocket();
            if (socket && socket.readyState === WebSocket.OPEN) {
                socket.send(JSON.stringify({
                    'message': message,
                    'from': username,
                    'command': 'new_message',
                    "chatid": currentRoomId
                }));
                if (messageInputDom) messageInputDom.value = '';
            } else {
                console.error('Socket not ready for room:', currentRoomId);
            }
        }
    });
}

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
            reader.onload = function (e) {
                previewContent.innerHTML = `<img src="${e.target.result}" alt="preview">`;
            };
            reader.readAsDataURL(file);
        } else if (previewContent) {
            previewContent.innerHTML = `<i class="fas fa-file fa-3x"></i>`;
        }

        const formData = new FormData();
        formData.append('file', file);
        const csrfToken = getCookie('csrftoken');

        let progress = 0;
        const progressInterval = setInterval(() => {
            progress += 10;
            if (progressBar) progressBar.style.width = `${progress}%`;
            if (progress >= 100) {
                clearInterval(progressInterval);
                setTimeout(() => {
                    if (filePreview) filePreview.style.display = 'none';
                }, 500);
            }
        }, 200);

        fetch('/uploads/', {
            method: 'POST',
            body: formData,
            headers: { 'X-CSRFToken': csrfToken }
        })
            .then(response => response.json())
            .then(data => {
                const messageHtml = file.type.startsWith('image/')
                    ? `<img src="${data.fileUrl}" alt="uploaded image" />`
                    : `<a href="${data.fileUrl}" target="_blank">${file.name}</a>`;

                const socket = getCurrentSocket();
                if (socket && socket.readyState === WebSocket.OPEN) {
                    socket.send(JSON.stringify({
                        message: messageHtml,
                        from: username,
                        command: 'new_message',
                        chatid: currentRoomId
                    }));
                }
            })
            .catch(error => console.error('Error uploading file:', error));
    });

    const closePreview = document.querySelector('.close-preview');
    if (closePreview) {
        closePreview.addEventListener('click', function () {
            const fp = document.querySelector('.file-preview');
            if (fp) fp.style.display = 'none';
            const fi = document.querySelector('#fileInput');
            if (fi) fi.value = '';
        });
    }
}

// PRESENCE MANAGEMENT SYSTEM
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

function updateUserPresence(user, status, lastSeen) {
    console.log(`🔍 Updating presence for ${user}: ${status}`);

    userPresenceMap.set(user, { status, lastSeen });

    const dots = document.querySelectorAll(`[data-user="${user}"], .sidebar-dot[data-user="${user}"]`);
    dots.forEach(dot => {
        dot.classList.toggle('online', status === 'online');
        dot.classList.toggle('offline', status !== 'online');
        if (lastSeen) {
            dot.setAttribute('title', `Last seen: ${new Date(lastSeen).toLocaleString()}`);
        }
    });

    if (typeof otherUser !== 'undefined' && user === otherUser) {
        updateHeaderPresence(user, status, lastSeen);
    }

    logAllUserStates();
}

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
}

function handlePresenceUpdate(data) {
    console.log('📡 handlePresenceUpdate called with:', data);

    if (data.command === 'presence_snapshot') {
        const presenceList = data.presence || [];
        userPresenceMap.clear();
        presenceList.forEach(entry => {
            updateUserPresence(entry.user, entry.status, entry.last_seen);
        });
        updateGroupChatHeader();
    } else if (data.command === 'presence') {
        updateUserPresence(data.user, data.status, data.last_seen);
        updateGroupChatHeader();
    }
}

function updateGroupChatHeader() {
    if (typeof otherUser !== 'undefined') return;

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
}

function getUserPresence(username) {
    return userPresenceMap.get(username) || { status: 'offline', lastSeen: null };
}

function isUserOnline(username) {
    const presence = userPresenceMap.get(username);
    return presence ? presence.status === 'online' : false;
}

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

// Debug commands
window.presenceDebug = {
    logAllStates: logAllUserStates,
    getPresence: getUserPresence,
    isOnline: isUserOnline,
    getUserMap: () => userPresenceMap,
    forceUpdate: (user, status, lastSeen) => {
        updateUserPresence(user, status, lastSeen);
    }
};

// PEOPLE SEARCH
function levenshtein(a, b) { a = String(a || ''); b = String(b || ''); if (a === b) return 0; if (!a.length) return b.length; if (!b.length) return a.length; let prev = new Array(b.length + 1).fill(0).map((_, i) => i); for (let i = 0; i < a.length; i++) { let curr = [i + 1]; for (let j = 0; j < b.length; j++) { curr[j + 1] = Math.min(curr[j] + 1, prev[j + 1] + 1, prev[j] + (a[i] === b[j] ? 0 : 1)); } prev = curr; } return prev[b.length]; }

function animateItemVisibility(li, show) { li.classList.add('people-anim'); if (show) { li.style.display = ''; requestAnimationFrame(() => li.classList.remove('fade-hidden')); } else { li.classList.add('fade-hidden'); li.addEventListener('transitionend', function onEnd(e) { if (e.propertyName === 'opacity') { li.style.display = 'none'; li.removeEventListener('transitionend', onEnd); } }); } }

function toggleEmojiPicker() { const element = document.querySelector('em-emoji-picker'); if (!element) return; element.style.display = element.style.display === 'none' ? '' : 'none'; }

function initPeopleSearch() {
    const input = document.getElementById('search');
    if (!input) return;
    let timeout;
    input.addEventListener('input', (e) => {
        clearTimeout(timeout);
        timeout = setTimeout(() => performPeopleSearch(e.target.value.trim()), 180);
    });
    input.addEventListener('search', (e) => {
        if (!e.target.value) performPeopleSearch('');
    });
    document.addEventListener('keydown', (e) => {
        if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 'k') {
            e.preventDefault();
            input.focus();
        }
    });
}

function performPeopleSearch(query) {
    const raw = (query || '').trim();
    const q = raw.toLowerCase();
    const tokenMatch = raw.match(/^(u:|e:)(.*)$/i);
    let token = null;
    let tokenQuery = '';
    if (tokenMatch) {
        token = tokenMatch[1].toLowerCase().replace(':', '');
        tokenQuery = tokenMatch[2].trim().toLowerCase();
    }
    document.querySelectorAll('.chat-list li').forEach(li => {
        const nameEl = li.querySelector('.fw-bold');
        if (!nameEl) return;
        if (!nameEl.dataset.originalName) {
            const orig = Array.from(nameEl.childNodes).filter(n => n.nodeType === Node.TEXT_NODE).map(n => n.textContent).join('').trim();
            nameEl.dataset.originalName = orig;
        }
        const original = nameEl.dataset.originalName || '';
        if (!q) {
            restoreNameText(nameEl);
            animateItemVisibility(li, true);
            return;
        }
        const searchable = [original.toLowerCase()];
        const anchor = li.querySelector('a');
        const datasetSource = (li.dataset && Object.keys(li.dataset).length) ? li.dataset : (anchor?.dataset || {});
        if (datasetSource.username) searchable.push(String(datasetSource.username).toLowerCase());
        if (datasetSource.email) searchable.push(String(datasetSource.email).toLowerCase());
        if (datasetSource.user) searchable.push(String(datasetSource.user).toLowerCase());
        let matches = false;
        if (token) {
            const field = token === 'u' ? (datasetSource.username || datasetSource.user || '') : (datasetSource.email || '');
            matches = field ? field.toLowerCase().includes(tokenQuery) : original.toLowerCase().includes(tokenQuery);
        } else {
            if (searchable.some(s => s.includes(q))) matches = true;
            else if (q.length >= 3) {
                const best = Math.max(...searchable.map(s => {
                    const dist = levenshtein(q, s);
                    return 1 - (dist / Math.max(q.length, s.length, 1));
                }));
                matches = best >= 0.45;
            }
        }
        if (matches) {
            highlightNameText(nameEl, q);
            animateItemVisibility(li, true);
        } else {
            restoreNameText(nameEl);
            animateItemVisibility(li, false);
        }
    });
}

function restoreNameText(nameEl) {
    const original = nameEl.dataset.originalName || '';
    Array.from(nameEl.childNodes).filter(n => n.nodeType === Node.TEXT_NODE).forEach(n => n.remove());
    if (original) nameEl.insertBefore(document.createTextNode(original + ' '), nameEl.firstChild || null);
}

function highlightNameText(nameEl, query) {
    const original = nameEl.dataset.originalName || '';
    const regex = new RegExp(escapeRegex(query), 'ig');
    const textNodes = Array.from(nameEl.childNodes).filter(n => n.nodeType === Node.TEXT_NODE);
    const text = textNodes.map(n => n.textContent).join('');
    if (!regex.test(text)) {
        restoreNameText(nameEl);
        nameEl.classList.add('fuzzy-match');
        setTimeout(() => nameEl.classList.remove('fuzzy-match'), 900);
        return;
    }
    const parts = text.split(regex);
    const matches = text.match(regex) || [];
    const frag = document.createDocumentFragment();
    for (let i = 0; i < parts.length; i++) {
        frag.appendChild(document.createTextNode(parts[i]));
        if (i < matches.length) {
            const span = document.createElement('span');
            span.className = 'search-highlight';
            span.textContent = matches[i];
            frag.appendChild(span);
        }
    }
    textNodes.forEach(n => n.remove());
    nameEl.insertBefore(frag, nameEl.firstChild || null);
    if (nameEl.firstChild?.nodeType === Node.TEXT_NODE && !/\s$/.test(nameEl.firstChild.textContent)) nameEl.firstChild.textContent += ' ';
}

function escapeRegex(string) {
    return string.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
}

// Emoji & init
function initEmojiPicker() {
    const savedTheme = localStorage.getItem('theme') || 'light';
    document.body.setAttribute('data-theme', savedTheme);
    const pickerOptions = {
        onEmojiSelect: function (emoji) {
            const input = document.getElementById('chat-message-input');
            if (input) input.value += emoji.native;
            toggleEmojiPicker();
        },
        theme: savedTheme
    };
    try {
        const picker = new EmojiMart.Picker(pickerOptions);
        picker.style.display = 'none';
        document.body.appendChild(picker);
    } catch (e) {
        console.log('Emoji picker not loaded');
    }
    document.getElementById('emoji')?.addEventListener('click', toggleEmojiPicker);
    document.getElementById('darkModeToggle')?.addEventListener('click', () => {
        const currentTheme = document.body.getAttribute('data-theme');
        const newTheme = currentTheme === 'dark' ? 'light' : 'dark';
        document.body.setAttribute('data-theme', newTheme);
        localStorage.setItem('theme', newTheme);
        document.querySelector('em-emoji-picker')?.setAttribute('theme', newTheme);
    });
}

document.addEventListener('DOMContentLoaded', function () {
    initEmojiPicker();
    initFileHandlers();
    initPeopleSearch();
});