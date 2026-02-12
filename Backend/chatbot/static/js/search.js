// Search functionality for MATHIA chat
(function () {
    'use strict';

    // Search state
    let searchState = {
        isActive: false,
        query: '',
        matches: [],
        currentMatchIndex: -1,
        dateFrom: null,
        dateTo: null
    };

    // Initialize search and mini-settings on page load
    document.addEventListener('DOMContentLoaded', function () {
        initializeSearch();
        initializeMiniSettings();
    });

    function initializeSearch() {
        const searchToggle = document.getElementById('searchToggle');
        const searchPanel = document.getElementById('searchPanel');
        const closeSearch = document.getElementById('closeSearch');
        const searchInput = document.getElementById('messageSearchInput');
        const dateFrom = document.getElementById('searchDateFrom');
        const dateTo = document.getElementById('searchDateTo');
        const clearFilters = document.getElementById('clearFilters');
        const prevMatch = document.getElementById('prevMatch');
        const nextMatch = document.getElementById('nextMatch');

        // Toggle search panel
        searchToggle.addEventListener('click', function () {
            toggleSearchPanel();
        });

        closeSearch.addEventListener('click', function () {
            closeSearchPanel();
        });

        // Search input with debounce
        let searchTimeout;
        searchInput.addEventListener('input', function () {
            clearTimeout(searchTimeout);
            searchTimeout = setTimeout(() => {
                performSearch();
            }, 300);
        });

        // Date filters
        dateFrom.addEventListener('change', performSearch);
        dateTo.addEventListener('change', performSearch);

        // Clear filters
        clearFilters.addEventListener('click', function () {
            searchInput.value = '';
            dateFrom.value = '';
            dateTo.value = '';
            performSearch();
        });

        // Navigation
        prevMatch.addEventListener('click', () => navigateMatches('prev'));
        nextMatch.addEventListener('click', () => navigateMatches('next'));

        // Keyboard shortcuts
        document.addEventListener('keydown', function (e) {
            // Ctrl/Cmd + F to open search
            if ((e.ctrlKey || e.metaKey) && e.key === 'f' && searchState.isActive) {
                e.preventDefault();
                searchInput.focus();
            }

            // Escape to close search
            if (e.key === 'Escape' && searchState.isActive) {
                closeSearchPanel();
            }

            // Arrow keys for navigation when search is focused
            if (searchState.isActive && searchState.matches.length > 0) {
                if (e.key === 'ArrowDown' || e.key === 'Enter') {
                    e.preventDefault();
                    navigateMatches('next');
                } else if (e.key === 'ArrowUp') {
                    e.preventDefault();
                    navigateMatches('prev');
                }
            }
        });
    }

    function toggleSearchPanel() {
        const panel = document.getElementById('searchPanel');
        const searchToggle = document.getElementById('searchToggle');
        const chatContainer = document.querySelector('.chat-container');

        if (!searchState.isActive) {
            // Open search
            searchState.isActive = true;
            panel.classList.add('active');
            searchToggle.classList.add('active');
            chatContainer.classList.add('search-active');

            // Focus search input
            setTimeout(() => {
                document.getElementById('messageSearchInput').focus();
            }, 300);
        } else {
            closeSearchPanel();
        }
    }

    function closeSearchPanel() {
        const panel = document.getElementById('searchPanel');
        const searchToggle = document.getElementById('searchToggle');
        const chatContainer = document.querySelector('.chat-container');

        searchState.isActive = false;
        panel.classList.remove('active');
        searchToggle.classList.remove('active');
        chatContainer.classList.remove('search-active');

        // Clear search
        clearSearchResults();
    }

    function performSearch() {
        const searchInput = document.getElementById('messageSearchInput');
        const dateFrom = document.getElementById('searchDateFrom').value;
        const dateTo = document.getElementById('searchDateTo').value;
        const query = searchInput.value.trim().toLowerCase();

        // Update state
        searchState.query = query;
        searchState.dateFrom = dateFrom ? new Date(dateFrom) : null;
        searchState.dateTo = dateTo ? new Date(dateTo + 'T23:59:59') : null;

        // Clear previous results
        clearSearchResults();

        // If no query and no dates, show all messages
        if (!query && !searchState.dateFrom && !searchState.dateTo) {
            showAllMessages();
            updateSearchCount(0);
            return;
        }

        // Get all messages - Target the dynamic room containers
        // If we have a currentRoomId, search only that room, otherwise search all
        const selector = window.currentRoomId ? `#messages-room-${window.currentRoomId} li` : '.chat-message-list li';
        const messages = document.querySelectorAll(selector);
        searchState.matches = [];

        messages.forEach((messageEl, index) => {
            // Find message content. In some versions it's .message, in others it's .content-text
            const contentEl = messageEl.querySelector('.message') || messageEl.querySelector('.content-text');
            if (!contentEl) return;

            // Get message data
            const messageText = contentEl.textContent.toLowerCase();
            const timestampEl = messageEl.querySelector('.time-label') || messageEl.querySelector('.message-time');
            const messageDate = timestampEl ? parseMessageDate(timestampEl.textContent) : null;

            // Check if message matches query
            const matchesQuery = !query || messageText.includes(query);

            // Check if message matches date range
            const matchesDateFrom = !searchState.dateFrom || (messageDate && messageDate >= searchState.dateFrom);
            const matchesDateTo = !searchState.dateTo || (messageDate && messageDate <= searchState.dateTo);

            if (matchesQuery && matchesDateFrom && matchesDateTo) {
                // Message matches - show and highlight
                messageEl.style.display = 'block';

                if (query) {
                    highlightText(contentEl, query);
                    searchState.matches.push(messageEl);
                }
            } else {
                // Message doesn't match - hide
                messageEl.style.display = 'none';
            }
        });

        // Update UI
        updateSearchCount(searchState.matches.length);

        if (searchState.matches.length > 0) {
            searchState.currentMatchIndex = 0;
            scrollToMatch(0);
        }
    }

    function highlightText(element, query) {
        // Get text content, preserving HTML structure
        const walker = document.createTreeWalker(
            element,
            NodeFilter.SHOW_TEXT,
            null,
            false
        );

        const textNodes = [];
        let node;
        while (node = walker.nextNode()) {
            if (node.textContent.trim()) {
                textNodes.push(node);
            }
        }

        textNodes.forEach(textNode => {
            const text = textNode.textContent;
            const lowerText = text.toLowerCase();
            const queryLower = query.toLowerCase();

            if (lowerText.includes(queryLower)) {
                const regex = new RegExp(`(${escapeRegex(query)})`, 'gi');
                const highlighted = text.replace(regex, '<span class="search-highlight">$1</span>');

                const span = document.createElement('span');
                span.innerHTML = highlighted;
                textNode.parentNode.replaceChild(span, textNode);
            }
        });
    }

    function escapeRegex(string) {
        return string.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
    }

    function clearSearchResults() {
        // Remove all highlights
        document.querySelectorAll('.search-highlight').forEach(highlight => {
            const text = highlight.textContent;
            highlight.replaceWith(text);
        });

        // Show all messages
        showAllMessages();

        // Reset state
        searchState.matches = [];
        searchState.currentMatchIndex = -1;
    }

    function showAllMessages() {
        const selector = window.currentRoomId ? `#messages-room-${window.currentRoomId} li` : '.chat-message-list li';
        document.querySelectorAll(selector).forEach(msg => {
            msg.style.display = 'block';
        });
    }

    function navigateMatches(direction) {
        if (searchState.matches.length === 0) return;

        // Remove active class from current match
        if (searchState.currentMatchIndex >= 0) {
            const currentMatch = searchState.matches[searchState.currentMatchIndex];
            currentMatch.querySelectorAll('.search-highlight').forEach(h => {
                h.classList.remove('active-match');
            });
        }

        // Update index
        if (direction === 'next') {
            searchState.currentMatchIndex = (searchState.currentMatchIndex + 1) % searchState.matches.length;
        } else {
            searchState.currentMatchIndex = searchState.currentMatchIndex <= 0
                ? searchState.matches.length - 1
                : searchState.currentMatchIndex - 1;
        }

        scrollToMatch(searchState.currentMatchIndex);
    }

    function scrollToMatch(index) {
        if (index < 0 || index >= searchState.matches.length) return;

        const matchElement = searchState.matches[index];

        // Add active class to current match
        matchElement.querySelectorAll('.search-highlight').forEach(h => {
            h.classList.add('active-match');
        });

        // Scroll to match
        matchElement.scrollIntoView({
            behavior: 'smooth',
            block: 'center'
        });

        // Update position indicator
        updateSearchPosition(index + 1, searchState.matches.length);
    }

    function updateSearchCount(count) {
        const searchCount = document.getElementById('searchCount');
        const searchNavigation = document.getElementById('searchNavigation');

        if (count === 0) {
            searchCount.textContent = 'No results';
            searchNavigation.style.display = 'none';
        } else {
            searchCount.textContent = `${count} result${count !== 1 ? 's' : ''}`;
            searchNavigation.style.display = 'flex';
            updateSearchPosition(1, count);
        }
    }

    function updateSearchPosition(current, total) {
        const searchPosition = document.getElementById('searchPosition');
        searchPosition.textContent = `${current}/${total}`;
    }

    function parseMessageDate(timeString) {
        // Parse time string from message (e.g., "2:30 PM" or "14:30:00")
        try {
            // This is a simple parser - adjust based on your actual time format
            const today = new Date();
            const timeParts = timeString.match(/(\d+):(\d+)(?::(\d+))?\s*(AM|PM)?/i);

            if (!timeParts) return today;

            let hours = parseInt(timeParts[1]);
            const minutes = parseInt(timeParts[2]);
            const seconds = timeParts[3] ? parseInt(timeParts[3]) : 0;
            const meridiem = timeParts[4];

            if (meridiem) {
                if (meridiem.toUpperCase() === 'PM' && hours < 12) hours += 12;
                if (meridiem.toUpperCase() === 'AM' && hours === 12) hours = 0;
            }

            today.setHours(hours, minutes, seconds, 0);
            return today;
        } catch (e) {
            console.error('Error parsing date:', e);
            return new Date();
        }
    }

    /* ------------------ Mini quick settings panel ------------------ */
    function initializeMiniSettings() {
        try {
            const settings = loadMiniSettings();
            applyMiniSettings(settings);
            const overlay = createMiniPanel();
            wireMiniPanel(overlay, settings);
            hookGearButton(overlay);
        } catch (e) {
            console.warn('Mini settings failed to initialize', e);
        }
    }

    const MINI_STORAGE_KEY = 'mini_chat_settings_v1';

    function loadMiniSettings() {
        const defaults = {
            theme: document.documentElement.getAttribute('data-theme') === 'dark' ? 'dark' : 'light',
            fontSize: parseInt(getComputedStyle(document.documentElement).getPropertyValue('--chat-font-size')) || 14,
            density: document.body.getAttribute('data-message-density') || 'comfortable'
        };
        try {
            const raw = localStorage.getItem(MINI_STORAGE_KEY);
            if (!raw) return defaults;
            const parsed = JSON.parse(raw);
            return Object.assign({}, defaults, parsed);
        } catch (e) {
            console.warn('Failed to load mini settings', e);
            return defaults;
        }
    }

    function saveMiniSettings(s) {
        try {
            localStorage.setItem(MINI_STORAGE_KEY, JSON.stringify(s));
        } catch (e) {
            console.warn('Failed to save mini settings', e);
        }
    }

    function applyMiniSettings(s) {
        if (s.theme === 'dark') {
            document.documentElement.setAttribute('data-theme', 'dark');
        } else {
            document.documentElement.removeAttribute('data-theme');
        }
        document.documentElement.style.setProperty('--chat-font-size', (s.fontSize || 14) + 'px');
        document.body.setAttribute('data-message-density', s.density || 'comfortable');
    }

    function createMiniPanel() {
        const overlay = document.createElement('div');
        overlay.className = 'mini-settings-overlay';
        overlay.innerHTML = `
            <div class="mini-settings" role="dialog" aria-label="Quick settings">
                <div class="mini-settings-header">
                    <strong>Quick settings</strong>
                    <button class="mini-settings-close" aria-label="Close">&times;</button>
                </div>
                <div class="mini-settings-body">
                    <div class="form-group">
                        <label>Theme</label>
                        <div>
                            <button type="button" class="btn btn-sm btn-outline-secondary theme-btn" data-theme="light">Light</button>
                            <button type="button" class="btn btn-sm btn-outline-secondary theme-btn" data-theme="dark">Dark</button>
                        </div>
                    </div>
                    <div class="form-group">
                        <label>Font size</label>
                        <input type="range" min="12" max="20" value="14" class="form-range mini-font" />
                        <div class="mini-font-value">14px</div>
                    </div>
                    <div class="form-group">
                        <label>Density</label>
                        <div>
                            <select class="form-select form-select-sm mini-density">
                                <option value="comfortable">Comfortable</option>
                                <option value="compact">Compact</option>
                                <option value="spacious">Spacious</option>
                            </select>
                        </div>
                    </div>
                    <div class="form-group">
                        <a href="#" class="btn btn-link" id="open-main-settings">Open full settings</a>
                    </div>
                </div>
            </div>
        `;
        document.body.appendChild(overlay);
        return overlay;
    }

    function wireMiniPanel(overlay, initial) {
        const panel = overlay.querySelector('.mini-settings');
        const closeBtn = overlay.querySelector('.mini-settings-close');
        const themeBtns = overlay.querySelectorAll('.theme-btn');
        const fontRange = overlay.querySelector('.mini-font');
        const fontValue = overlay.querySelector('.mini-font-value');
        const densitySelect = overlay.querySelector('.mini-density');
        const openMain = overlay.querySelector('#open-main-settings');

        // apply initial
        fontRange.value = initial.fontSize || 14;
        fontValue.textContent = (initial.fontSize || 14) + 'px';
        densitySelect.value = initial.density || 'comfortable';
        themeBtns.forEach(b => b.classList.toggle('active', b.getAttribute('data-theme') === initial.theme));

        closeBtn.addEventListener('click', () => closeMini(overlay, panel));
        overlay.addEventListener('click', (e) => { if (e.target === overlay) closeMini(overlay, panel); });
        document.addEventListener('keydown', (e) => { if (e.key === 'Escape' && overlay.classList.contains('open')) closeMini(overlay, panel); });

        themeBtns.forEach(b => b.addEventListener('click', () => {
            const theme = b.getAttribute('data-theme');
            initial.theme = theme;
            applyMiniSettings(initial);
            saveMiniSettings(initial);
            themeBtns.forEach(btn => btn.classList.toggle('active', btn === b));
        }));

        fontRange.addEventListener('input', (e) => {
            const val = parseInt(e.target.value, 10);
            fontValue.textContent = val + 'px';
            initial.fontSize = val;
            applyMiniSettings(initial);
        });
        fontRange.addEventListener('change', () => saveMiniSettings(initial));

        densitySelect.addEventListener('change', (e) => {
            initial.density = e.target.value;
            applyMiniSettings(initial);
            saveMiniSettings(initial);
        });

        openMain.addEventListener('click', (e) => { e.preventDefault(); window.location.href = '/settings/'; });
    }

    function hookGearButton(overlay) {
        const listbottom = document.querySelector('.listbottom');
        if (!listbottom) return;
        const btns = listbottom.querySelectorAll('button, a');
        let gear = null;
        for (const b of btns) {
            if (b.querySelector && b.querySelector('.fa-cog')) { gear = b; break; }
        }
        if (!gear) return;
        gear.addEventListener('click', (e) => {
            e.preventDefault();
            e.stopPropagation();
            const panel = overlay.querySelector('.mini-settings');
            if (overlay.classList.contains('open')) {
                closeMini(overlay, panel);
            } else {
                openMini(overlay, panel);
            }
        });
    }


    function openMini(overlay, panel) {
        overlay.classList.add('open');
        panel.classList.add('open');
        setTimeout(() => {
            const first = panel.querySelector('button, [href], input, select');
            if (first) first.focus();
        }, 120);
    }

    function closeMini(overlay, panel) {
        overlay.classList.remove('open');
        panel.classList.remove('open');
    }

    // Export for use in other scripts if needed
    window.chatSearch = {
        performSearch,
        closeSearchPanel
    };
})();