// Search functionality for MATHIA chat
(function() {
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

    // Initialize search on page load
    document.addEventListener('DOMContentLoaded', function() {
        initializeSearch();
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
        searchToggle.addEventListener('click', function() {
            toggleSearchPanel();
        });

        closeSearch.addEventListener('click', function() {
            closeSearchPanel();
        });

        // Search input with debounce
        let searchTimeout;
        searchInput.addEventListener('input', function() {
            clearTimeout(searchTimeout);
            searchTimeout = setTimeout(() => {
                performSearch();
            }, 300);
        });

        // Date filters
        dateFrom.addEventListener('change', performSearch);
        dateTo.addEventListener('change', performSearch);

        // Clear filters
        clearFilters.addEventListener('click', function() {
            searchInput.value = '';
            dateFrom.value = '';
            dateTo.value = '';
            performSearch();
        });

        // Navigation
        prevMatch.addEventListener('click', () => navigateMatches('prev'));
        nextMatch.addEventListener('click', () => navigateMatches('next'));

        // Keyboard shortcuts
        document.addEventListener('keydown', function(e) {
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

        // Get all messages
        const messages = document.querySelectorAll('#top-chat li');
        searchState.matches = [];

        messages.forEach((messageEl, index) => {
            const contentEl = messageEl.querySelector('.message');
            if (!contentEl) return;

            // Get message data
            const messageText = contentEl.textContent.toLowerCase();
            const timestampEl = messageEl.querySelector('.time-label');
            const messageDate = timestampEl ? parseMessageDate(timestampEl.textContent) : null;

            // Check if message matches query
            const matchesQuery = !query || messageText.includes(query);

            // Check if message matches date range
            const matchesDateFrom = !searchState.dateFrom || (messageDate && messageDate >= searchState.dateFrom);
            const matchesDateTo = !searchState.dateTo || (messageDate && messageDate <= searchState.dateTo);

            if (matchesQuery && matchesDateFrom && matchesDateTo) {
                // Message matches - show and highlight
                messageEl.classList.remove('message-hidden');

                if (query) {
                    highlightText(contentEl, query);
                    searchState.matches.push(messageEl);
                }
            } else {
                // Message doesn't match - hide
                messageEl.classList.add('message-hidden');
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
        document.querySelectorAll('#top-chat li').forEach(msg => {
            msg.classList.remove('message-hidden');
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

    // Export for use in other scripts if needed
    window.chatSearch = {
        performSearch,
        closeSearchPanel
    };
})();