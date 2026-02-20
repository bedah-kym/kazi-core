(() => {
    const unreadBadge = document.getElementById('unreadRoomsBadge');
    const remindersBadge = document.getElementById('pendingRemindersBadge');
    const totalBadge = document.getElementById('notificationTotalBadge');
    const mobileUnread = document.getElementById('unreadRoomsBadgeMobile');
    const mobileReminders = document.getElementById('pendingRemindersBadgeMobile');
    const menuButton = document.getElementById('notificationMenuButton');

    if (!unreadBadge || !remindersBadge || !totalBadge) {
        return;
    }

    const parseCount = (value) => {
        const parsed = parseInt(value, 10);
        return Number.isFinite(parsed) ? parsed : 0;
    };

    let lastUnread = parseCount(menuButton?.dataset?.unread || unreadBadge.textContent);
    let lastReminders = parseCount(menuButton?.dataset?.reminders || remindersBadge.textContent);
    let lastTotal = lastUnread + lastReminders;
    let lastMarkedRoom = null;
    let lastMarkedAt = 0;
    let markTimer = null;

    const updateBadgeVisibility = (el, value) => {
        if (!el) return;
        el.textContent = value;
        if (value > 0) {
            el.classList.remove('is-hidden');
        } else {
            el.classList.add('is-hidden');
        }
    };

    const updateCounts = (unread, reminders) => {
        const total = unread + reminders;
        updateBadgeVisibility(unreadBadge, unread);
        updateBadgeVisibility(remindersBadge, reminders);
        updateBadgeVisibility(totalBadge, total);

        if (mobileUnread) mobileUnread.textContent = unread;
        if (mobileReminders) mobileReminders.textContent = reminders;

        lastTotal = total;
    };

    const playSound = () => {
        try {
            const AudioContextClass = window.AudioContext || window.webkitAudioContext;
            if (!AudioContextClass) return;
            const ctx = new AudioContextClass();
            const oscillator = ctx.createOscillator();
            const gain = ctx.createGain();
            oscillator.type = 'sine';
            oscillator.frequency.value = 880;
            gain.gain.setValueAtTime(0.001, ctx.currentTime);
            gain.gain.exponentialRampToValueAtTime(0.2, ctx.currentTime + 0.02);
            gain.gain.exponentialRampToValueAtTime(0.001, ctx.currentTime + 0.18);
            oscillator.connect(gain);
            gain.connect(ctx.destination);
            oscillator.start();
            oscillator.stop(ctx.currentTime + 0.2);
        } catch (err) {
            // Ignore audio failures (autoplay restrictions, etc.)
        }
    };

    const getCsrfToken = () => {
        if (typeof window.getCookie === 'function') {
            return window.getCookie('csrftoken');
        }
        return null;
    };

    const fetchCounts = () => {
        const currentRoom = window.currentRoomId || '';
        fetch(`/chatbot/api/notifications/status/?exclude_room_id=${currentRoom}`, {
            credentials: 'same-origin',
        })
            .then((res) => (res.ok ? res.json() : null))
            .then((payload) => {
                if (!payload) return;
                const unread = parseCount(payload.unread_rooms);
                const reminders = parseCount(payload.pending_reminders);

                if (unread > lastUnread || reminders > lastReminders) {
                    playSound();
                }

                lastUnread = unread;
                lastReminders = reminders;
                updateCounts(unread, reminders);
            })
            .catch(() => {});
    };

    const markRoomRead = (roomId) => {
        if (!roomId) return;
        const now = Date.now();
        if (lastMarkedRoom === roomId && now - lastMarkedAt < 15000) {
            return;
        }

        lastMarkedRoom = roomId;
        lastMarkedAt = now;

        if (markTimer) clearTimeout(markTimer);
        markTimer = setTimeout(() => {
            const csrfToken = getCsrfToken();
            fetch(`/chatbot/api/rooms/${roomId}/read/`, {
                method: 'POST',
                headers: csrfToken ? { 'X-CSRFToken': csrfToken } : {},
                credentials: 'same-origin',
            }).catch(() => {});
        }, 400);
    };

    updateCounts(lastUnread, lastReminders);

    fetchCounts();
    setInterval(fetchCounts, 30000);

    window.notificationCenter = {
        refresh: fetchCounts,
        markRoomRead,
    };
})();
