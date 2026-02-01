// Global variables
// MULTI-ROOM ARCHITECTURE
const activeRooms = {}; // { roomId: { socket, initialized, oldestMsgId, hasMore, isLoadingHistory } }
window.currentRoomId = typeof roomName !== 'undefined' ? roomName : null;
window.usernameGlobal = typeof username !== 'undefined' ? username : null;
window.replyToMessageId = null;
const userPresenceMap = new Map();

// Helper function to get current socket
function getCurrentSocket() {
    const room = activeRooms[window.currentRoomId];
    return room ? room.socket : null;
}
window.getCurrentSocket = getCurrentSocket;

// Initialize when DOM is ready
document.addEventListener('DOMContentLoaded', () => {
    const initialRoomId = window.currentRoomId || (typeof roomName !== 'undefined' ? roomName : null);
    if (initialRoomId) {
        initRoom(initialRoomId);
    } else {
        console.warn('⚠️ No initial room ID found for initialization');
    }
});

/**
 * Room Loader Management
 */
function showRoomLoader(roomId) {
    const container = document.getElementById(`room-container-${roomId}`);
    if (!container) return;

    // Remove existing if any
    hideRoomLoader(roomId);

    const loader = document.createElement('div');
    loader.className = 'room-loader-overlay';
    loader.id = `loader-room-${roomId}`;
    loader.innerHTML = `
        <div class="room-loader-content">
            <div class="room-loader-spinner"></div>
            <div class="room-loader-text">Loading Room...</div>
        </div>
    `;
    container.appendChild(loader);
}

function hideRoomLoader(roomId) {
    const loader = document.getElementById(`loader-room-${roomId}`);
    if (loader) {
        loader.classList.add('loader-hidden');
        setTimeout(() => loader.remove(), 400);
    }
}
window.showRoomLoader = showRoomLoader;
window.hideRoomLoader = hideRoomLoader;

/**
 * Initialize a room (UI + Socket)
 */
function initRoom(roomId) {
    console.log(`🚀 Initializing room ${roomId}`);

    // 1. Create UI Container if missing
    getOrCreateRoomUI(roomId);

    // 2. Room Switch / Loader Logic
    const roomRecord = activeRooms[roomId];

    if (roomRecord && roomRecord.socket && roomRecord.socket.readyState === WebSocket.OPEN) {
        console.log(`♻️ Room ${roomId} already connected, skipping init`);
        activateRoomUI(roomId);
        // Ensure loader is hidden if it was somehow left over
        hideRoomLoader(roomId);
        // Refresh messages for consistency
        FetchMessages(roomId);
    } else {
        // Show Loader for new/reconnecting rooms
        showRoomLoader(roomId);
        connectToChat(roomId);
        activateRoomUI(roomId);
    }
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

        // Create history loader (shown when loading older messages)
        const historyLoader = document.createElement('div');
        historyLoader.id = `history-loader-${roomId}`;
        historyLoader.className = 'history-loader';
        historyLoader.style.display = 'none';
        historyLoader.innerHTML = '<div class="history-loader-dot"></div><div class="history-loader-dot"></div><div class="history-loader-dot"></div>';

        // Create sentinel for infinite scroll detection
        const sentinel = document.createElement('div');
        sentinel.id = `history-sentinel-${roomId}`;
        sentinel.className = 'history-sentinel';

        // Append to UL so they are part of scroll flow
        // Order: Sentinel -> Loader -> Messages
        ul.appendChild(sentinel);
        ul.appendChild(historyLoader);

        roomDiv.appendChild(ul);
        roomDiv.appendChild(typingDiv);
        container.appendChild(roomDiv);

        // Setup infinite scroll observer after DOM is ready
        setTimeout(() => setupHistoryObserver(roomId), 100);
    }
    return roomDiv;
}

/**
 * Switch valid visible room
 */
function activateRoomUI(roomId) {
    if (!roomId) return;

    // Update global state
    window.currentRoomId = roomId;

    // Hide all rooms
    document.querySelectorAll('.room-view').forEach(el => el.style.display = 'none');

    // Show target room
    const target = getOrCreateRoomUI(roomId);
    if (target) target.style.display = 'block';

    // Scroll to bottom
    const messagesList = document.getElementById(`messages-room-${roomId}`);
    if (messagesList) {
        messagesList.scrollTop = messagesList.scrollHeight;
    }

    // Update Context Panel
    if (window.contextPanel) {
        window.contextPanel.updateRoom(roomId);
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

    activeRooms[roomId] = {
        socket: socket,
        initialized: false,
        oldestMsgId: null,
        hasMore: true,
        isLoadingHistory: false
    };

    // Initialize chat connection
    socket.onopen = function (e) {
        console.log('✅ WebSocket connection established for room ' + roomId);
        // Always fetch messages on open to ensure sync and hide loader
        FetchMessages(roomId);
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
            const room = activeRooms[rId];
            const isHistoryLoad = room && room.isLoadingHistory;
            const messagesList = document.getElementById(`messages-room-${rId}`);

            // Record scroll position before prepending (for history loads)
            const prevScrollHeight = messagesList ? messagesList.scrollHeight : 0;

            // For history loads, prepend messages at the TOP
            if (isHistoryLoad && messagesList) {
                // Create messages in correct order (oldest first for prepending)
                for (let i = 0; i < data.messages.length; i++) {
                    createMessage(data.messages[i], rId, true); // true = prepend
                }

                // Restore scroll position so view doesn't jump
                if (messagesList && room.scrollRestoreData) {
                    const newScrollHeight = messagesList.scrollHeight;
                    messagesList.scrollTop = newScrollHeight - room.scrollRestoreData.prevScrollHeight;
                }
            } else {
                // Initial load - append normally (newest at bottom)
                for (let i = data.messages.length - 1; i >= 0; i--) {
                    createMessage(data.messages[i], rId);
                }
                scrollToLastMessage(rId);
            }

            // Update pagination state from response
            if (room) {
                room.hasMore = data.has_more !== false;
                if (data.oldest_id) room.oldestMsgId = data.oldest_id;
                room.isLoadingHistory = false;
                room.scrollRestoreData = null;
            }

            // Hide loaders
            hideRoomLoader(rId);
            const historyLoader = document.getElementById(`history-loader-${rId}`);
            if (historyLoader) historyLoader.style.display = 'none';

            return;
        }
        if (data.command === 'new_message') {
            createMessage(data.message, rId);
            scrollToLastMessage(rId);
            if (window.mathiaAssistant) {
                window.mathiaAssistant.checkForTrigger(data);
            }
            return;
        }
        // AI Integration
        if (data.command === 'ai_stream' || data.command === 'ai_message' || data.command === 'ai_message_saved' || data.command === 'ai_voice_ready') {
            if (window.mathiaAssistant) {
                window.mathiaAssistant.handleMessage(data);
            }
            return;
        }
        if (data.command === 'voice_transcription_ready') {
            const msgEl = document.querySelector(`[data-message-id="${data.message_id}"] .voice-transcript`);
            if (msgEl) {
                msgEl.textContent = data.transcript;
                msgEl.style.display = 'block';
            }
            return;
        }
        if (data.command === 'user_quotas') {
            if (typeof updateQuotaUI === 'function') {
                updateQuotaUI(data.quotas);
            }
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
    window.currentRoomId = roomId;
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

                // Also toggle the people list panel and backdrop to keep UI in sync
                const peopleList = document.querySelector('.people-list');
                const peopleBackdrop = document.getElementById('peopleBackdrop');
                if (peopleList) peopleList.classList.toggle('open', menuOpen);
                if (peopleBackdrop) peopleBackdrop.classList.toggle('show', menuOpen);

                return false;
            };
        }

        document.body.onclick = function (e) {
            if (menuOpen) {
                const peopleList = document.querySelector('.people-list');
                const menuToggle = document.getElementById('menuToggle');
                const peopleBackdrop = document.getElementById('peopleBackdrop');
                if (peopleList && menuToggle) {
                    const clickedInsideMenu = peopleList.contains(e.target);
                    const clickedToggle = menuToggle.contains(e.target);
                    if (!clickedInsideMenu && !clickedToggle) {
                        menuOpen = false;
                        document.body.classList.remove('people-open');
                        // Ensure visual panels are closed
                        peopleList.classList.remove('open');
                        if (peopleBackdrop) peopleBackdrop.classList.remove('show');
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

// FIX: createMessage now accepts roomId parameter and prepend flag
function createMessage(data, roomId, prepend = false) {
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
    const currentUsername = window.usernameGlobal || (typeof username !== 'undefined' ? username : null);
    msgListTag.classList.add(data.member === currentUsername ? 'my' : 'other');
    msgListTag.className = 'clearfix';
    // Give each message LI a unique id to avoid duplicate IDs in the DOM
    // (previously every li used 'tracker' which can cause query/getElementById
    // ambiguity and unexpected behavior).
    if (data.id) {
        msgListTag.id = `message-${data.id}`;
    } else {
        msgListTag.id = `message-temp-${Date.now()}`;
    }

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

    // SPECIAL STYLING FOR MATHIA
    const isMathia = data.member && (
        data.member.toLowerCase().includes('mathia')
    );

    if (isMathia) {
        msgTextTag.classList.add('mathia-message');
        // Add Badge to the bubble
        const badge = document.createElement('div');
        badge.className = 'mathia-badge';
        badge.innerHTML = '<i class="fas fa-robot"></i> <span>Mathia AI</span>';
        msgTextTag.insertBefore(badge, msgTextTag.firstChild);
    }

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

                // Ensure the <pre> has an id so the delegated handler can find it
                if (!pre.id) {
                    pre.id = `codeblock-${Date.now()}-${Math.random().toString(36).slice(2, 7)}`;
                }
                btn.dataset.copyTarget = pre.id;
                pre.appendChild(btn);
            }
        });
    }

    // DATE SEPARATOR LOGIC
    const msgDate = getMessageDate(data.timestamp);

    if (prepend && messagesList.firstChild) {
        // Prepending: Check if the *next* message (current firstChild) has a different date
        // If so, it means the current firstChild is the start of a new day relative to us
        // So we need to put a separator *above* the current firstChild

        let refNode = messagesList.firstChild;
        // Skip existing separators or loaders to find actual message
        while (refNode && (refNode.classList.contains('date-separator') || refNode.classList.contains('history-loader'))) {
            refNode = refNode.nextSibling;
        }

        if (refNode && refNode.dataset && refNode.dataset.timestamp) {
            const nextMsgDate = getMessageDate(parseInt(refNode.dataset.timestamp)); // timestamp stored on li?
            // Actually, createMessage doesn't store timestamp on li yet. Let's fix that below.

            // Wait, we can't easily get timestamp from DOM unless we store it.
            // Strategy: We rely on the fact that refNode implies a date.
            // Better: Store timestamp on the li element always.
        }
    }

    // Let's modify the li creation to store timestamp for this logic
    msgListTag.dataset.timestamp = new Date(data.timestamp).getTime();

    if (prepend) {
        // PREPEND LOGIC
        if (messagesList.firstChild) {
            // Check the node that is currently at the top
            let nextNode = messagesList.firstChild;
            // If the top node is a loader or sentinel, skip it
            while (nextNode && (nextNode.classList.contains('history-loader') || nextNode.classList.contains('history-sentinel'))) {
                nextNode = nextNode.nextSibling;
            }

            // If the top node is a date separator, that separator belongs to the group below it.
            // We are inserting *above* that separator.
            // So we just insert our message.
            // BUT: If the top node is a MESSAGE, we compare dates.

            if (nextNode && nextNode.tagName === 'LI' && nextNode.dataset.timestamp) {
                const nextDate = getMessageDate(parseInt(nextNode.dataset.timestamp));
                if (msgDate.getTime() !== nextDate.getTime()) {
                    // Different days. The message BELOW us needs a separator above it.
                    // Insert Separator BEFORE nextNode
                    const sep = createDateSeparator(nextDate);
                    messagesList.insertBefore(sep, nextNode);
                }
            }
        }
        // Always insert the message at top (before whatever is first now)
        // If we just inserted a sep, it is now first. We insert before it. 
        // Logic: [Msg_A] -> Prepend Msg_B (diff date)
        // 1. Insert Sep_A before Msg_A. List: [Sep_A, Msg_A]
        // 2. Insert Msg_B before Sep_A. List: [Msg_B, Sep_A, Msg_A] -> Correct.

        // Wait, insertBefore(newNode, referenceNode).
        // If we insert Sep, reference matches NextNode.
        // Then we insert Msg, reference matches (Sep? No, we want Msg to be above Sep? No.)
        // Visual:
        // [Jan 2 Msg]
        // Prepend Jan 1 Msg.
        // Result: [Jan 1 Msg] [Sep Jan 2] [Jan 2 Msg].

        let targetNode = messagesList.firstChild;
        // Ensure we insert after the loader if it exists/visible?
        // Actually loader is usually hidden or removed.
        // But if we have one, we want to insert AFTER it (visually below).
        // But prepend adds to TOP.

        // Simplified Prepend:
        // 1. If date(next_msg) != date(us), inject Sep(date(next_msg)) above next_msg.
        // 2. Inject us at very top (below loader if exists).

        // Find the effective "top" message
        let topNode = messagesList.firstChild;
        while (topNode && (topNode.classList.contains('history-loader') || topNode.classList.contains('history-sentinel'))) {
            topNode = topNode.nextSibling;
        }

        if (topNode && topNode.tagName === 'LI' && topNode.dataset.timestamp) {
            const topDate = getMessageDate(parseInt(topNode.dataset.timestamp));
            if (msgDate.getTime() !== topDate.getTime()) {
                const sep = createDateSeparator(topDate);
                messagesList.insertBefore(sep, topNode);
            }
        }

        // Now find insertion point again (might have changed if we added sep)
        // actually we want to insert 'msgListTag' BEFORE 'topNode' (which might be the sep we just added? No).
        // If we added Sep, 'topNode' is still the message. Sep is prevSibling of topNode.
        // We want Msg to be before Sep. 
        // So we insert before (Sep if existed, else topNode).

        // Let's restart logic cleanly:
        // We want: [Msg] [Sep?] [OldTop]

        let insertionPoint = messagesList.firstChild;
        // Skip loader and sentinel logic for date separator insertion point
        while (insertionPoint && (insertionPoint.classList.contains('history-loader') || insertionPoint.classList.contains('history-sentinel'))) {
            insertionPoint = insertionPoint.nextSibling;
        }

        if (insertionPoint && insertionPoint.tagName === 'LI' && insertionPoint.dataset.timestamp) {
            const topDate = getMessageDate(parseInt(insertionPoint.dataset.timestamp));
            // If dates different, we need a separator for the OLD top message
            if (msgDate.getTime() !== topDate.getTime()) {
                const sep = createDateSeparator(topDate);
                messagesList.insertBefore(sep, insertionPoint);
                // Now insertionPoint is still the old message.
                // The Sep is at insertionPoint.previousSibling
                // We want our new message to be BEFORE the Sep? 
                // [Msg] [Sep] [OldMsg]
                // So we insert before Sep.
                insertionPoint = sep;
            }
        } else if (insertionPoint && insertionPoint.classList.contains('date-separator')) {
            // If top is already a separator (e.g. from previous batch), it belongs to the message below it.
            // We are adding a message ABOVE it.
            // Does that separator date match our new message?
            const sepDateLabel = insertionPoint.textContent; // "Today", etc.
            // Hard to compare text.
            // But valid logic: If a separator is there, we assume it separates the day below from the day above.
            // Since we are adding above, we are in "day above".
            // So we just insert before it. 
            // IF dates match, we might have a double separator?
            // e.g. [Sep Jan 1] [Msg Jan 1 A].
            // We add [Msg Jan 1 B].
            // We insert before Sep? -> [Msg B] [Sep] [Msg A]. -> Wrong visually.

            // Issue: Prepend logic relies on the assumption that we are traversing BACKWARDS in time.
            // If we have [Sep Jan 2] [Msg Jan 2].
            // We add [Msg Jan 1]. Different day.
            // Reference is [Sep Jan 2].
            // We insert [Msg Jan 1] before [Sep Jan 2].
            // Result: [Msg Jan 1] [Sep Jan 2] [Msg Jan 2]. -> Correct!

            // Case: [Sep Jan 1] [Msg Jan 1 A].
            // We add [Msg Jan 1 B]. Same day.
            // We insert before [Sep Jan 1].
            // Result: [Msg Jan 1 B] [Sep Jan 1] [Msg Jan 1 A]. -> WRONG.
            // Separator should be above the GROUP.
            // Correct: [Sep Jan 1] [Msg B] [Msg A].

            // So, if top is a separator:
            // Check if `date(Msg)` matches `date(Separator)`.
            // IF MATCH: We should insert AFTER separator?
            //    Only if we want the separator to stay at top.
            //    If we insert after separtor: [Sep] [Msg B] [Msg A]. -> Correct.

            // Strategy: Store timestamp on Separator too.
            if (insertionPoint.dataset.timestamp) {
                const sepDate = getMessageDate(parseInt(insertionPoint.dataset.timestamp));
                if (msgDate.getTime() === sepDate.getTime()) {
                    // Same day! The separator is already here.
                    // We put our message BELOW the separator (so separator stays top).
                    if (insertionPoint.nextSibling) {
                        messagesList.insertBefore(msgListTag, insertionPoint.nextSibling);
                    } else {
                        messagesList.appendChild(msgListTag);
                    }
                    return; // Done
                }
            }
        }

        messagesList.insertBefore(msgListTag, insertionPoint);

    } else {
        // APPEND LOGIC (Normal)
        // Check last child
        const lastNode = messagesList.lastChild;
        if (lastNode && lastNode.tagName === 'LI' && lastNode.dataset.timestamp) {
            const lastDate = getMessageDate(parseInt(lastNode.dataset.timestamp));
            if (msgDate.getTime() !== lastDate.getTime()) {
                const sep = createDateSeparator(msgDate);
                messagesList.appendChild(sep);
            }
        } else if (!lastNode) {
            // First message ever? Show separator?
            // Usually yes for "Today".
            const sep = createDateSeparator(msgDate);
            messagesList.appendChild(sep);
        }

        messagesList.appendChild(msgListTag);
    }

    // Add dropdown menu to AI messages
    if (window.messageActions && data.id) {
        const isAIMessage = data.member && (data.member.toLowerCase() === 'mathia' || data.member.toLowerCase() === '@mathia');
        const isFailed = data.status === 'failed' || data.error === true;

        if (isAIMessage) {
            // Wait for DOM to be ready
            setTimeout(() => {
                window.messageActions.addDropdownToMessage(
                    msgTextTag,  // message element
                    data.id,     // message ID
                    data.content, // message content
                    true,        // isAIMessage
                    isFailed     // isFailed
                );
            }, 50);
        }
    }

    // Auto-scroll only if strictly necessary (not prepending)
    if (!prepend) {
        const currentRid = window.currentRoomId || (typeof roomName !== 'undefined' ? roomName : null);
        if (targetRoomId === currentRid) {
            messagesList.scrollTop = messagesList.scrollHeight;
        }
    }
}

/**
 * Render a premium voice bubble with WaveSurfer
 */
function renderVoiceBubble(container, data) {
    const audioUrl = data.audio_url || '';
    const currentUname = window.usernameGlobal || (typeof username !== 'undefined' ? username : null);
    const transcript = data.voice_transcript || (data.member === currentUname ? 'Transcribing...' : '');

    container.innerHTML = `
        <div class="voice-bubble">
            <button class="play-btn" id="play-${data.id}">
                <i class="fas fa-play"></i>
            </button>
            <div id="waveform-${data.id}" class="voice-waveform"></div>
            <span class="voice-duration" id="duration-${data.id}">0:00</span>
        </div>
        <div class="voice-transcript" style="${transcript ? '' : 'display:none;'}">
            ${transcript}
        </div>
    `;

    if (!audioUrl) return;

    // Use a small timeout to ensure container is in DOM
    setTimeout(() => {
        const wavesurfer = WaveSurfer.create({
            container: `#waveform-${data.id}`,
            waveColor: '#667eea',
            progressColor: '#5a3a82',
            cursorColor: 'transparent',
            barWidth: 2,
            barRadius: 3,
            responsive: true,
            height: 25,
            url: audioUrl.startsWith('/') ? audioUrl : `/media/${audioUrl}`,
        });

        const playBtn = document.getElementById(`play-${data.id}`);
        const durationSpan = document.getElementById(`duration-${data.id}`);

        playBtn.onclick = () => wavesurfer.playPause();

        wavesurfer.on('play', () => playBtn.innerHTML = '<i class="fas fa-pause"></i>');
        wavesurfer.on('pause', () => playBtn.innerHTML = '<i class="fas fa-play"></i>');
        wavesurfer.on('finish', () => playBtn.innerHTML = '<i class="fas fa-play"></i>');

        wavesurfer.on('ready', () => {
            const duration = wavesurfer.getDuration();
            const mins = Math.floor(duration / 60);
            const secs = Math.floor(duration % 60);
            durationSpan.textContent = `${mins}:${secs.toString().padStart(2, '0')}`;
        });

        wavesurfer.on('audioprocess', () => {
            const current = wavesurfer.getCurrentTime();
            const mins = Math.floor(current / 60);
            const secs = Math.floor(current % 60);
            durationSpan.textContent = `${mins}:${secs.toString().padStart(2, '0')}`;
        });
    }, 50);
}

/**
 * Infinite Scroll: Setup IntersectionObserver to trigger loading older messages
 */
function setupHistoryObserver(roomId) {
    const sentinel = document.getElementById(`history-sentinel-${roomId}`);
    if (!sentinel) return;

    const observer = new IntersectionObserver((entries) => {
        if (entries[0].isIntersecting) {
            loadOlderMessages(roomId);
        }
    }, { threshold: 0.1 });

    observer.observe(sentinel);
    console.log(`📜 History observer setup for room ${roomId}`);
}

/**
 * Infinite Scroll: Load older messages with scroll position lock
 */
function loadOlderMessages(roomId) {
    const room = activeRooms[roomId];
    if (!room || !room.hasMore || room.isLoadingHistory) return;

    room.isLoadingHistory = true;

    // Show history loader
    const loader = document.getElementById(`history-loader-${roomId}`);
    if (loader) loader.style.display = 'flex';

    // Record scroll position before loading
    const messagesList = document.getElementById(`messages-room-${roomId}`);
    const prevScrollHeight = messagesList ? messagesList.scrollHeight : 0;

    console.log(`📜 Loading older messages for room ${roomId}, before_id: ${room.oldestMsgId}`);

    // Request older messages via WebSocket
    room.socket.send(JSON.stringify({
        command: 'fetch_messages',
        chatid: roomId,
        before_id: room.oldestMsgId
    }));

    // Store scroll restoration data for message handler
    room.scrollRestoreData = { prevScrollHeight, messagesList };
}

// FIX: FetchMessages now uses room-specific socket
function FetchMessages(roomId) {
    const targetRoom = roomId || window.currentRoomId || (typeof roomName !== 'undefined' ? roomName : null);
    if (!targetRoom) return;
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
function getCurrentMessageList() {
    const roomId = window.currentRoomId || (typeof roomName !== 'undefined' ? roomName : null);
    if (roomId) {
        return document.getElementById(`messages-room-${roomId}`);
    }
    return null;
}

function scrollToLastMessage(roomId) {
    const targetRoom = roomId || window.currentRoomId || (typeof roomName !== 'undefined' ? roomName : null);
    if (!targetRoom) return;
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
                'from': window.usernameGlobal,
                "chatid": window.currentRoomId
            }));
        }
    });

    chatInput.addEventListener('keyup', function (e) {
        if (e.keyCode === 13) {
            document.querySelector('#chat-message-submit')?.click();
        }
    });
}

// Date Separator Helpers
function getMessageDate(timestamp) {
    if (!timestamp) return null;
    const d = new Date(timestamp);
    return new Date(d.getFullYear(), d.getMonth(), d.getDate());
}

function formatDateLabel(dateObj) {
    const today = new Date();
    today.setHours(0, 0, 0, 0);

    const yesterday = new Date(today);
    yesterday.setDate(yesterday.getDate() - 1);

    if (dateObj.getTime() === today.getTime()) return 'Today';
    if (dateObj.getTime() === yesterday.getTime()) return 'Yesterday';

    return dateObj.toLocaleDateString('en-US', { weekday: 'short', month: 'short', day: 'numeric' });
}

function createDateSeparator(dateObj) {
    const div = document.createElement('div');
    div.className = 'date-separator';
    // Store timestamp for potential comparisons
    div.dataset.timestamp = dateObj.getTime();
    div.innerHTML = `<span class="separator-text">${formatDateLabel(dateObj)}</span>`;
    return div;
}

// Delegated handler for copy buttons (created dynamically in createMessage)
document.addEventListener('click', function (e) {
    const cb = e.target.closest('.copy-btn');
    if (!cb) return;
    e.preventDefault();
    e.stopPropagation();
    const targetId = cb.dataset.copyTarget;
    if (!targetId) return;
    const src = document.getElementById(targetId);
    if (!src) return;
    const text = src.textContent || src.innerText || '';
    navigator.clipboard.writeText(text).then(() => {
        const old = cb.innerHTML;
        cb.innerHTML = '<i class="fas fa-check"></i> Copied!';
        setTimeout(() => cb.innerHTML = old, 2000);
    }).catch(err => {
        console.error('Copy failed:', err);
    });
});

// FIX: Submit button now uses current socket
const chatSubmit = document.querySelector('#chat-message-submit');
if (chatSubmit) {
    chatSubmit.addEventListener('click', function () {
        const messageInputDom = document.querySelector('#chat-message-input');
        const message = messageInputDom?.value?.trim();

        if (message) {
            const socket = getCurrentSocket();
            if (socket && socket.readyState === WebSocket.OPEN) {
                // INSTANT THINKING: Disabled to prevent ordering race condition (Bubble appearing before User Msg)
                // if (message.toLowerCase().includes('@mathia') && window.mathiaAssistant) {
                //     window.mathiaAssistant.showAIThinking();
                // }

                socket.send(JSON.stringify({
                    'message': message,
                    'from': window.usernameGlobal,
                    'command': 'new_message',
                    "chatid": window.currentRoomId,
                    "reply_to": window.replyToMessageId || null
                }));
                if (messageInputDom) messageInputDom.value = '';
                window.replyToMessageId = null;
            } else {
                console.error('Socket not ready for room:', window.currentRoomId);
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
                    const currentUname = window.usernameGlobal || (typeof username !== 'undefined' ? username : null);
                    const currentRid = window.currentRoomId || (typeof roomName !== 'undefined' ? roomName : null);

                    socket.send(JSON.stringify({
                        message: messageHtml,
                        from: currentUname,
                        command: 'new_message',
                        chatid: currentRid,
                        reply_to: window.replyToMessageId || null
                    }));
                    window.replyToMessageId = null;
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

// Small accessibility improvements: ensure key interactive buttons have aria-labels
document.addEventListener('DOMContentLoaded', () => {
    const ensureAria = (selector, label) => {
        const el = document.querySelector(selector);
        if (el && !el.getAttribute('aria-label')) el.setAttribute('aria-label', label);
    };

    ensureAria('#voice-record-btn', 'Record or stop voice message');
    ensureAria('#chat-message-submit', 'Send message');
    ensureAria('#upload', 'Upload file');
    ensureAria('#emoji', 'Emoji picker');
    ensureAria('#search', 'Search people');
    // Mobile people list toggle wiring (only for chat page)
    if (document.body && document.body.classList && document.body.classList.contains('chat-page')) {
        const peopleToggle = document.getElementById('peopleToggle');
        const peopleList = document.querySelector('.people-list');
        const peopleBackdrop = document.getElementById('peopleBackdrop');

        if (peopleToggle && peopleList) {
            peopleToggle.addEventListener('click', () => {
                const open = peopleList.classList.toggle('open');
                if (peopleBackdrop) peopleBackdrop.classList.toggle('show', open);
            });
        }

        if (peopleBackdrop && peopleList) {
            peopleBackdrop.addEventListener('click', () => {
                peopleList.classList.remove('open');
                peopleBackdrop.classList.remove('show');
            });
        }
    }
});
