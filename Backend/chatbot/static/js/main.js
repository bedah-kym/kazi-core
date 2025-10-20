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
});

// Toggle emoji picker visibility
function toggleEmojiPicker() {
    const element = document.querySelector('em-emoji-picker');
    if (!element) return;
    element.style.display = element.style.display === 'none' ? '' : 'none';
}
