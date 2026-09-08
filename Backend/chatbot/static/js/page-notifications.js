/* Notification center: read / dismiss / mark-all interactions. */
(function () {
    'use strict';

    function csrfToken() {
        return window.AppShell ? window.AppShell.csrfToken() : '';
    }

    function showToast(message) {
        var toast = document.getElementById('appToast');
        if (!toast) return;
        toast.textContent = message;
        toast.classList.add('show');
        setTimeout(function () { toast.classList.remove('show'); }, 2400);
    }

    function post(url) {
        var csrf = csrfToken();
        return fetch(url, {
            method: 'POST',
            headers: csrf ? { 'X-CSRFToken': csrf } : {},
            credentials: 'same-origin'
        });
    }

    function init() {
        var list = document.getElementById('notifList');
        var markAllBtn = document.getElementById('markAllReadBtn');
        var unreadCountEl = document.querySelector('.app-tab.active .app-tab-count');

        function decrementUnread(el) {
            var wasUnread = el && el.classList.contains('unread');
            if (wasUnread && unreadCountEl) {
                unreadCountEl.textContent = Math.max(0, parseInt(unreadCountEl.textContent, 10) - 1);
            }
            return wasUnread;
        }

        if (list) {
            list.addEventListener('click', function (event) {
                var btn = event.target.closest('[data-action]');
                if (!btn) return;
                var item = btn.closest('.notif-item');
                var id = btn.dataset.id;

                if (btn.dataset.action === 'read') {
                    decrementUnread(item);
                    item.classList.remove('unread');
                    var dot = item.querySelector('.unread-dot');
                    if (dot) dot.remove();
                    btn.remove();
                    post('/notifications/api/' + id + '/read/')
                        .catch(function () { showToast('Could not update - try again'); });
                } else if (btn.dataset.action === 'dismiss') {
                    decrementUnread(item);
                    item.remove();
                    post('/notifications/api/' + id + '/dismiss/')
                        .catch(function () { showToast('Could not update - try again'); });
                    if (!list.querySelector('.notif-item')) {
                        window.location.reload();
                    }
                }
            });
        }

        if (markAllBtn) {
            markAllBtn.addEventListener('click', function () {
                post('/notifications/api/read-all/')
                    .then(function () {
                        if (list) {
                            list.querySelectorAll('.notif-item').forEach(function (item) {
                                item.classList.remove('unread');
                                var dot = item.querySelector('.unread-dot');
                                if (dot) dot.remove();
                                var readBtn = item.querySelector('[data-action="read"]');
                                if (readBtn) readBtn.remove();
                            });
                        }
                        if (unreadCountEl) unreadCountEl.textContent = '0';
                        showToast('All notifications marked as read');
                    })
                    .catch(function () { showToast('Could not update - try again'); });
            });
        }
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', init);
    } else {
        init();
    }
})();
