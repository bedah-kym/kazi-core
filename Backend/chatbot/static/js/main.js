var chatSocket = new ReconnectingWebSocket(
    'ws://' + window.location.host + '/ws/chat/' + roomName + '/');
// Hamburger menu toggle - Wait for DOM to load
(function () {
    let menuOpen = false;

    window.addEventListener('load', function () {
        const menuToggle = document.getElementById('menuToggle');
        console.log('Menu toggle button found:', menuToggle);
        console.log('Initial menuOpen state:', menuOpen);

        if (menuToggle) {
            menuToggle.onclick = function (e) {
                e.preventDefault();
                e.stopPropagation();

                menuOpen = !menuOpen;
                console.log('Menu state changed to:', menuOpen);

                if (menuOpen) {
                    document.body.classList.add('people-open');
                    console.log('Added people-open class');
                } else {
                    document.body.classList.remove('people-open');
                    console.log('Removed people-open class');
                }

                console.log('Body classes now:', document.body.className);
                return false;
            };
        } else {
            console.error('Menu toggle button NOT found!');
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
                        console.log('Menu closed by clicking outside');
                    }
                }
            }
        };
    });
})();

// Initialize chat connection
chatSocket.onopen = function (e) {
    FetchMessages();
};

chatSocket.onclose = function (e) {
    console.error('Chat socket closed unexpectedly');
};

// Message handling functions
function createMessage(data) {
    if (!data || !data.member || !data.content || !data.timestamp) {
        console.error('Invalid message data:', data);
        return;
    }
    const chatHistory = document.querySelector('.chat-history');
    var message = data;
    var author = data['member'];
    var formattedTime = new Date(message.timestamp).toLocaleTimeString();
    var time = `<span class="time-label">${formattedTime}</span>`;
    var msgListTag = document.createElement('li');
    msgListTag.classList.add(message.member === username ? 'my' : 'other');

    var msgDivTag = document.createElement('div');
    var msgdivtag = document.createElement('p');
    var msgSpanTag = document.createElement('span');
    var msgpTag = document.createElement('div');
    var msgTextTag = document.createElement('div');

    msgTextTag.innerHTML = message.content;
    msgdivtag.innerHTML = author;
    msgpTag.innerHTML += time;

    msgListTag.className = 'clearfix';
    msgListTag.id = 'tracker';

    if (message.member === username) {
        msgDivTag.className = 'message-data text-end';
        msgTextTag.className = 'message my-message';
        msgpTag.className = 'time-label';
    } else {
        msgDivTag.className = 'message-data';
        msgTextTag.className = 'message other-message';
        msgpTag.className = 'time-label';
    }
    // Add sentimage class if message contains an image
    if (message.content.includes('<img')) {
        msgTextTag.classList.add('sentimage');
    }
    msgdivtag.className = 'user-name';
    msgSpanTag.className = 'message-data-time';

    msgListTag.appendChild(msgDivTag);
    msgDivTag.appendChild(msgpTag);
    msgDivTag.appendChild(msgSpanTag);
    msgSpanTag.appendChild(msgTextTag);
    msgTextTag.appendChild(msgdivtag);

    document.querySelector('#top-chat').appendChild(msgListTag);
    chatHistory.scrollTop = chatHistory.scrollHeight;
}

function FetchMessages() {
    chatSocket.send(JSON.stringify({
        "command": "fetch_messages",
        "chatid": roomName
    }));
}

function scrollToLastMessage() {
    const elements = document.querySelectorAll('#tracker');
    if (elements.length > 0) {
        const lastElement = elements[elements.length - 1];
        lastElement.scrollIntoView();
    }
}

// Typing indicator functions
function showTypingIndicator() {
    document.querySelector('.typing-indicator').style.display = 'flex';
}

function hideTypingIndicator() {
    document.querySelector('.typing-indicator').style.display = 'none';
}

// Utility functions
function getCookie(name) {
    const cookies = document.cookie.split(';');
    for (let cookie of cookies) {
        cookie = cookie.trim();
        if (cookie.startsWith(name + '=')) {
            return cookie.substring(name.length + 1);
        }
    }
    return null;
}

// Event listeners
let typingTimer;
document.querySelector('#chat-message-input').addEventListener('input', function () {
    chatSocket.send(JSON.stringify({
        'command': 'typing',
        'from': username,
        "chatid": roomName
    }));
});

document.querySelector('#chat-message-input').addEventListener('keyup', function (e) {
    if (e.keyCode === 13) {
        document.querySelector('#chat-message-submit').click();
    }
});

document.querySelector('#chat-message-submit').addEventListener('click', function (e) {
    var messageInputDom = document.querySelector('#chat-message-input');
    var message = messageInputDom.value;
    if (message.trim()) {
        chatSocket.send(JSON.stringify({
            'message': message,
            'from': username,
            'command': 'new_message',
            "chatid": roomName
        }));
        messageInputDom.value = '';
    }
});

// File handling
document.querySelector('#upload').addEventListener('click', function () {
    document.querySelector('#fileInput').click();
});

document.querySelector('#fileInput').addEventListener('change', function (event) {
    const file = event.target.files[0];
    if (!file) return;

    const filePreview = document.querySelector('.file-preview');
    const previewContent = filePreview.querySelector('.preview-content');
    const fileName = filePreview.querySelector('.file-name');
    const progressBar = filePreview.querySelector('.upload-progress-bar');

    fileName.textContent = file.name;
    previewContent.innerHTML = '';
    progressBar.style.width = '0%';
    filePreview.style.display = 'block';

    if (file.type.startsWith('image/')) {
        const reader = new FileReader();
        reader.onload = function (e) {
            previewContent.innerHTML = `<img src="${e.target.result}" alt="preview">`;
        };
        reader.readAsDataURL(file);
    } else {
        previewContent.innerHTML = `<i class="fas fa-file fa-3x"></i>`;
    }

    const formData = new FormData();
    formData.append('file', file);
    const csrfToken = getCookie('csrftoken');

    let progress = 0;
    const progressInterval = setInterval(() => {
        progress += 10;
        progressBar.style.width = `${progress}%`;

        if (progress >= 100) {
            clearInterval(progressInterval);
            setTimeout(() => {
                filePreview.style.display = 'none';
            }, 500);
        }
    }, 200);

    fetch('/uploads/', {
        method: 'POST',
        body: formData,
        headers: {
            'X-CSRFToken': csrfToken
        }
    })
        .then(response => response.json())
        .then(data => {
            chatSocket.send(JSON.stringify({
                'message': file.type.startsWith('image/')
                    ? `<img src="${data.fileUrl}" alt="uploaded image" />`
                    : `<a href="${data.fileUrl}" target="_blank">${file.name}</a>`,
                'from': username,
                'command': 'new_message',
                "chatid": roomName
            }));
        })
        .catch(error => console.error('Error uploading file:', error));
});

document.querySelector('.close-preview').addEventListener('click', function () {
    document.querySelector('.file-preview').style.display = 'none';
    document.querySelector('#fileInput').value = '';
});

// WebSocket message handling
chatSocket.onmessage = function (e) {
    var data = JSON.parse(e.data);

    if (data.command === 'typing' && data.from !== username) {
        document.getElementById('typing-user').textContent = data.from;
        showTypingIndicator();
        clearTimeout(typingTimer);
        typingTimer = setTimeout(hideTypingIndicator, 3000);
    }
    else if (data['command'] == 'messages') {
        for (let i = (data['messages'].length) - 1; i >= 0; i--) {
            createMessage(data['messages'][i]);
        }
        scrollToLastMessage();
    }
    else if (data['command'] == 'new_message') {
        createMessage(data['message']);
        scrollToLastMessage();
    }
    else if (data.command === 'presence') {
        const who = data.user;
        const state = data.status;

        if (who === otherUser) {
            const dot = document.getElementById('header-presence');
            dot.classList.toggle('online', state === 'online');
            dot.classList.toggle('offline', state === 'offline');
        }

        document.querySelectorAll(`.sidebar-dot[data-user="${who}"]`)
            .forEach(el => {
                el.classList.toggle('online', state === 'online');
                el.classList.toggle('offline', state === 'offline');
            });
    }
    else if (data.command === 'presence_snapshot') {
        const online = new Set(data.online || []);

        const headerDot = document.getElementById('header-presence');
        if (headerDot && otherUser) {
            const on = online.has(otherUser);
            headerDot.classList.toggle('online', on);
            headerDot.classList.toggle('offline', !on);
        }

        document.querySelectorAll('.sidebar-dot[data-user]').forEach(el => {
            const who = el.dataset.user;
            const on = online.has(who);
            el.classList.toggle('online', on);
            el.classList.toggle('offline', !on);
        });
    }
    else if (data.command === 'error') {
        console.error('Error from server:', data.message);
    }
    else {
        console.warn('Unknown command:', data);
    }
};

// Emoji picker initialization
document.addEventListener('DOMContentLoaded', function () {
    // Load saved theme or fallback to light
    const savedTheme = localStorage.getItem('theme') || 'light';
    document.body.setAttribute('data-theme', savedTheme);

    // Create picker with correct theme
    const pickerOptions = {
        onEmojiSelect: function (emoji) {
            const input = document.getElementById('chat-message-input');
            input.value += emoji.native;
            toggleEmojiPicker();
        },
        theme: savedTheme   // set emoji picker theme
    };

    // Initialize emoji picker
    const picker = new EmojiMart.Picker(pickerOptions);
    picker.style.display = 'none';
    document.body.appendChild(picker);

    // Toggle button for showing/hiding the picker
    document.getElementById('emoji').addEventListener('click', toggleEmojiPicker);

    // Handle dark mode toggle and sync emoji picker theme
    const toggleButton = document.getElementById('darkModeToggle');
    toggleButton.addEventListener('click', () => {
        const currentTheme = document.body.getAttribute('data-theme');
        const newTheme = currentTheme === 'dark' ? 'light' : 'dark';

        document.body.setAttribute('data-theme', newTheme);
        localStorage.setItem('theme', newTheme);

        // Update emoji picker theme as well
        const pickerElement = document.querySelector('em-emoji-picker');
        if (pickerElement) {
            pickerElement.setAttribute('theme', newTheme);
        }
    });

    // Initialize people search in the sidebar (search who you've been talking to)
    (function initPeopleSearch() {
        const input = document.getElementById('search');
        if (!input) return;

        let timeout;
        input.addEventListener('input', function (e) {
            clearTimeout(timeout);
            const q = e.target.value.trim();
            timeout = setTimeout(() => performPeopleSearch(q), 180);
        });

        // Clear highlights and show all when input cleared
        input.addEventListener('search', function (e) {
            if (!e.target.value) performPeopleSearch('');
        });

        // Focus with Ctrl/Cmd+K
        document.addEventListener('keydown', function (e) {
            if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 'k') {
                e.preventDefault();
                input.focus();
            }
        });
    })();
});

// People search helpers
function performPeopleSearch(query) {
    const raw = (query || '').trim();
    const q = raw.toLowerCase();
    // token search: u:username, e:email - allow quick targeting
    let token = null;
    let tokenQuery = '';
    const tokenMatch = raw.match(/^(u:|e:)(.*)$/i);
    if (tokenMatch) {
        token = tokenMatch[1].toLowerCase().replace(':', '');
        tokenQuery = tokenMatch[2].trim().toLowerCase();
    }
    const listItems = document.querySelectorAll('.chat-list li');

    if (!listItems) return;

    listItems.forEach(li => {
        const nameEl = li.querySelector('.fw-bold');
        if (!nameEl) return;

        // Extract and cache original name (text nodes only)
        if (!nameEl.dataset.originalName) {
            const orig = Array.from(nameEl.childNodes)
                .filter(n => n.nodeType === Node.TEXT_NODE)
                .map(n => n.textContent)
                .join('')
                .trim();
            nameEl.dataset.originalName = orig;
        }

        const original = nameEl.dataset.originalName || '';

        if (!q) {
            // show all and restore original text
            restoreNameText(nameEl);
            animateItemVisibility(li, true);
            return;
        }

        // collect searchable fields
        const searchable = [original.toLowerCase()];
        // data attributes if present - e.g., data-username, data-email on the li or anchor
        const anchor = li.querySelector('a');
        const datasetSource = (li.dataset && Object.keys(li.dataset).length) ? li.dataset : (anchor && anchor.dataset ? anchor.dataset : {});
        if (datasetSource.username) searchable.push(String(datasetSource.username).toLowerCase());
        if (datasetSource.email) searchable.push(String(datasetSource.email).toLowerCase());
        if (datasetSource.user) searchable.push(String(datasetSource.user).toLowerCase());

        let matches = false;

        // If token search in use, prioritize that field
        if (token) {
            const field = token === 'u' ? (datasetSource.username || datasetSource.user || '') : (datasetSource.email || '');
            if (field) {
                matches = field.toLowerCase().includes(tokenQuery);
            } else {
                // fallback to original
                matches = original.toLowerCase().includes(tokenQuery);
            }
        } else {
            // substring fast-pass
            if (searchable.some(s => s.includes(q))) {
                matches = true;
            } else {
                // fuzzy check for longer queries
                if (q.length >= 3) {
                    // compute best normalized similarity across fields
                    let best = 0;
                    searchable.forEach(s => {
                        const dist = levenshtein(q, s);
                        const norm = 1 - (dist / Math.max(q.length, s.length, 1));
                        if (norm > best) best = norm;
                    });
                    // threshold for fuzzy match
                    matches = best >= 0.45; // permissive
                }
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
    // remove all text nodes and re-insert original text as a single text node at start
    // but leave other child elements (like status-dot) intact
    // remove existing text nodes
    Array.from(nameEl.childNodes).forEach(n => {
        if (n.nodeType === Node.TEXT_NODE) n.remove();
    });
    // insert original text node at beginning
    if (original) nameEl.insertBefore(document.createTextNode(original + ' '), nameEl.firstChild || null);
}

function highlightNameText(nameEl, query) {
    const original = nameEl.dataset.originalName || '';
    const regex = new RegExp(escapeRegex(query), 'ig');

    // remove existing text nodes
    const textNodes = Array.from(nameEl.childNodes).filter(n => n.nodeType === Node.TEXT_NODE);
    const text = textNodes.map(n => n.textContent).join('');

    // If query not present as substring, try to highlight best fuzzy chunk (approximate)
    if (!regex.test(text)) {
        // for fuzzy matches we won't try to pick exact chars — just show a subtle pulse on the name
        restoreNameText(nameEl);
        nameEl.classList.add('fuzzy-match');
        setTimeout(() => nameEl.classList.remove('fuzzy-match'), 900);
        return;
    }

    // Build new HTML for the text portion only
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

    // remove existing text nodes
    textNodes.forEach(n => n.remove());
    // insert the highlighted fragment at the start
    nameEl.insertBefore(frag, nameEl.firstChild || null);
    // ensure spacing
    if (nameEl.firstChild && nameEl.firstChild.nodeType === Node.TEXT_NODE && !/\s$/.test(nameEl.firstChild.textContent)) {
        nameEl.firstChild.textContent = nameEl.firstChild.textContent + ' ';
    }
}

function escapeRegex(string) {
    return string.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
}

// Simple Levenshtein distance (iterative, optimized for short strings)
function levenshtein(a, b) {
    a = String(a || '');
    b = String(b || '');
    if (a === b) return 0;
    const al = a.length, bl = b.length;
    if (al === 0) return bl;
    if (bl === 0) return al;

    let v0 = new Array(bl + 1), v1 = new Array(bl + 1);
    for (let j = 0; j <= bl; j++) v0[j] = j;

    for (let i = 0; i < al; i++) {
        v1[0] = i + 1;
        const ai = a.charAt(i);
        for (let j = 0; j < bl; j++) {
            const cost = ai === b.charAt(j) ? 0 : 1;
            v1[j + 1] = Math.min(v1[j] + 1, v0[j + 1] + 1, v0[j] + cost);
        }
        const tmp = v0; v0 = v1; v1 = tmp;
    }
    return v0[bl];
}

// Animate show/hide with a CSS class; keeps layout stable
function animateItemVisibility(li, show) {
    // ensure animatable class
    li.classList.add('people-anim');
    if (show) {
        li.style.display = '';
        // trigger reflow then remove hidden class
        requestAnimationFrame(() => {
            li.classList.remove('fade-hidden');
        });
    } else {
        // add hidden class to animate out then hide
        li.classList.add('fade-hidden');
        li.addEventListener('transitionend', function onEnd(e) {
            if (e.propertyName === 'opacity') {
                li.style.display = 'none';
                li.removeEventListener('transitionend', onEnd);
            }
        });
    }
}

// Toggle emoji picker visibility
function toggleEmojiPicker() {
    const element = document.querySelector('em-emoji-picker');
    if (!element) return;
    element.style.display = element.style.display === 'none' ? '' : 'none';
}
