(() => {
    const unreadBadge = document.getElementById('unreadRoomsBadge');
    const remindersBadge = document.getElementById('pendingRemindersBadge');
    const totalBadge = document.getElementById('notificationTotalBadge');
    const mobileUnread = document.getElementById('unreadRoomsBadgeMobile');
    const mobileReminders = document.getElementById('pendingRemindersBadgeMobile');
    const menuButton = document.getElementById('notificationMenuButton');
    const notificationList = document.getElementById('notificationList');
    const noNotifications = document.getElementById('noNotifications');

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
    let notifSocket = null;
    let reconnectTimer = null;
    let usePolling = false;
    let unreadNotifCount = 0;

    // ------------------------------------------------------------------ //
    //  Badge helpers                                                      //
    // ------------------------------------------------------------------ //

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

    const updateUnifiedBadge = (count) => {
        unreadNotifCount = count;
        // Unified count is added to the total badge alongside legacy counts
        const total = lastUnread + lastReminders + count;
        updateBadgeVisibility(totalBadge, total);
    };

    // ------------------------------------------------------------------ //
    //  Sound                                                              //
    // ------------------------------------------------------------------ //

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
            // Ignore audio failures
        }
    };

    // ------------------------------------------------------------------ //
    //  CSRF                                                               //
    // ------------------------------------------------------------------ //

    const getCsrfToken = () => {
        if (typeof window.getCookie === 'function') {
            return window.getCookie('csrftoken');
        }
        return null;
    };

    // ------------------------------------------------------------------ //
    //  Notification list rendering                                        //
    // ------------------------------------------------------------------ //

    const escapeHtml = (str) => {
        const div = document.createElement('div');
        div.textContent = str;
        return div.innerHTML;
    };

    const timeAgo = (isoStr) => {
        try {
            const diff = (Date.now() - new Date(isoStr).getTime()) / 1000;
            if (diff < 60) return 'just now';
            if (diff < 3600) return `${Math.floor(diff / 60)}m ago`;
            if (diff < 86400) return `${Math.floor(diff / 3600)}h ago`;
            return `${Math.floor(diff / 86400)}d ago`;
        } catch {
            return '';
        }
    };

    const severityIcon = (severity) => {
        const map = { success: 'fa-check-circle text-success', warning: 'fa-exclamation-triangle text-warning', error: 'fa-times-circle text-danger' };
        return map[severity] || 'fa-info-circle text-info';
    };

    const renderNotificationItem = (n) => {
        const item = document.createElement('div');
        item.className = `notification-item d-flex align-items-start gap-2 px-3 py-2 border-bottom${n.is_read ? '' : ' bg-light'}`;
        item.dataset.id = n.id;
        item.style.cursor = 'pointer';
        item.innerHTML = `
            <i class="fas ${severityIcon(n.severity)} mt-1" style="font-size: 0.9rem;"></i>
            <div class="flex-grow-1" style="min-width: 0;">
                <div class="fw-semibold text-truncate" style="font-size: 0.85rem;">${escapeHtml(n.title)}</div>
                ${n.body ? `<div class="text-muted text-truncate" style="font-size: 0.8rem;">${escapeHtml(n.body)}</div>` : ''}
                <small class="text-muted">${timeAgo(n.created_at)}</small>
            </div>
            <button class="btn btn-sm btn-link text-muted p-0 ms-1 notif-dismiss" title="Dismiss" style="font-size: 0.75rem;">
                <i class="fas fa-times"></i>
            </button>
        `;

        // Click to mark read
        item.addEventListener('click', (e) => {
            if (e.target.closest('.notif-dismiss')) return;
            markNotificationRead(n.id);
            item.classList.remove('bg-light');
        });

        // Dismiss button
        item.querySelector('.notif-dismiss').addEventListener('click', (e) => {
            e.stopPropagation();
            dismissNotification(n.id);
            item.remove();
            checkEmptyState();
        });

        return item;
    };

    const checkEmptyState = () => {
        if (!notificationList) return;
        const items = notificationList.querySelectorAll('.notification-item');
        if (noNotifications) {
            noNotifications.style.display = items.length === 0 ? 'block' : 'none';
        }
    };

    const loadNotificationList = () => {
        if (!notificationList) return;
        fetch('/notifications/api/?per_page=20', { credentials: 'same-origin' })
            .then((res) => (res.ok ? res.json() : null))
            .then((data) => {
                if (!data || !data.notifications) return;
                // Clear existing items (keep the empty state placeholder)
                notificationList.querySelectorAll('.notification-item').forEach((el) => el.remove());
                data.notifications.forEach((n) => {
                    notificationList.appendChild(renderNotificationItem(n));
                });
                checkEmptyState();
            })
            .catch(() => {});
    };

    // Load notification list when dropdown opens
    if (menuButton) {
        menuButton.addEventListener('click', () => {
            loadNotificationList();
        });
    }

    // ------------------------------------------------------------------ //
    //  WebSocket connection                                               //
    // ------------------------------------------------------------------ //

    const connectNotifSocket = () => {
        try {
            const wsScheme = window.location.protocol === 'https:' ? 'wss' : 'ws';
            const wsUrl = `${wsScheme}://${window.location.host}/ws/notifications/`;
            notifSocket = new WebSocket(wsUrl);

            notifSocket.onopen = () => {
                usePolling = false;
                if (reconnectTimer) {
                    clearTimeout(reconnectTimer);
                    reconnectTimer = null;
                }
            };

            notifSocket.onmessage = (event) => {
                try {
                    const data = JSON.parse(event.data);

                    if (data.type === 'init') {
                        updateUnifiedBadge(data.unread_count);
                    } else if (data.type === 'notification') {
                        // New notification arrived
                        unreadNotifCount++;
                        updateUnifiedBadge(unreadNotifCount);
                        playSound();

                        // Prepend to dropdown if open
                        if (notificationList) {
                            const item = renderNotificationItem({
                                id: data.id,
                                event_type: data.event_type,
                                severity: data.severity,
                                title: data.title,
                                body: data.body,
                                created_at: data.created_at,
                                is_read: false,
                            });
                            notificationList.prepend(item);
                            checkEmptyState();
                        }
                    } else if (data.type === 'ack') {
                        if (data.action === 'mark_all_read') {
                            unreadNotifCount = 0;
                            updateUnifiedBadge(0);
                        }
                    }
                } catch {
                    // Ignore parse errors
                }
            };

            notifSocket.onclose = () => {
                usePolling = true;
                notifSocket = null;
                reconnectTimer = setTimeout(connectNotifSocket, 5000);
            };

            notifSocket.onerror = () => {
                // onclose will fire after onerror
            };
        } catch {
            usePolling = true;
        }
    };

    // ------------------------------------------------------------------ //
    //  Actions via WebSocket (with REST fallback)                         //
    // ------------------------------------------------------------------ //

    const markNotificationRead = (id) => {
        if (notifSocket && notifSocket.readyState === WebSocket.OPEN) {
            notifSocket.send(JSON.stringify({ action: 'mark_read', id }));
        } else {
            const csrf = getCsrfToken();
            fetch(`/notifications/api/${id}/read/`, {
                method: 'POST',
                headers: csrf ? { 'X-CSRFToken': csrf } : {},
                credentials: 'same-origin',
            }).catch(() => {});
        }
    };

    const markAllRead = () => {
        if (notifSocket && notifSocket.readyState === WebSocket.OPEN) {
            notifSocket.send(JSON.stringify({ action: 'mark_all_read' }));
        } else {
            const csrf = getCsrfToken();
            fetch('/notifications/api/read-all/', {
                method: 'POST',
                headers: csrf ? { 'X-CSRFToken': csrf } : {},
                credentials: 'same-origin',
            }).catch(() => {});
        }

        // Optimistic UI update
        if (notificationList) {
            notificationList.querySelectorAll('.notification-item.bg-light').forEach((el) => {
                el.classList.remove('bg-light');
            });
        }
        unreadNotifCount = 0;
        updateUnifiedBadge(0);
    };

    const dismissNotification = (id) => {
        if (notifSocket && notifSocket.readyState === WebSocket.OPEN) {
            notifSocket.send(JSON.stringify({ action: 'dismiss', id }));
        } else {
            const csrf = getCsrfToken();
            fetch(`/notifications/api/${id}/dismiss/`, {
                method: 'POST',
                headers: csrf ? { 'X-CSRFToken': csrf } : {},
                credentials: 'same-origin',
            }).catch(() => {});
        }
    };

    // ------------------------------------------------------------------ //
    //  Legacy polling (fallback when WebSocket is down)                   //
    // ------------------------------------------------------------------ //

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

    // ------------------------------------------------------------------ //
    //  Initialize                                                         //
    // ------------------------------------------------------------------ //

    updateCounts(lastUnread, lastReminders);
    fetchCounts();

    // Connect notification WebSocket
    connectNotifSocket();

    // Polling runs as fallback — check flag each interval
    setInterval(() => {
        if (usePolling) fetchCounts();
    }, 30000);

    // Public API
    window.notificationCenter = {
        refresh: fetchCounts,
        markRoomRead,
        markAllRead,
    };
})();
