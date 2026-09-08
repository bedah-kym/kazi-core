/* Live dashboard: polls /api/dashboard/ and hydrates the widgets. */
(function () {
    'use strict';

    var API_URL = '/api/dashboard/';
    var POLL_MS = 15000;
    var loaded = false;

    function el(id) {
        return document.getElementById(id);
    }

    function setText(id, value) {
        var node = el(id);
        if (node) node.textContent = value;
    }

    function formatNumber(value) {
        var n = Number(value);
        if (!isFinite(n)) return '\u2014';
        return n.toLocaleString();
    }

    function formatMoney(balance, currency) {
        var n = Number(balance);
        if (!isFinite(n)) return '\u2014';
        var amount = n.toLocaleString(undefined, { maximumFractionDigits: 0 });
        return (currency || '') + ' ' + amount;
    }

    function timeAgo(iso) {
        if (!iso) return '';
        var diff = Date.now() - new Date(iso).getTime();
        var sec = Math.floor(diff / 1000);
        if (sec < 60) return 'just now';
        var min = Math.floor(sec / 60);
        if (min < 60) return min + 'm ago';
        var hr = Math.floor(min / 60);
        if (hr < 24) return hr + 'h ago';
        var days = Math.floor(hr / 24);
        if (days < 7) return days + 'd ago';
        return new Date(iso).toLocaleDateString();
    }

    var ICONS = {
        message: 'fa-comment',
        reminder: 'fa-clock',
        workflow: 'fa-diagram-project'
    };

    function statusClass(status) {
        if (status === 'completed' || status === 'sent') return 'dash-status-success';
        if (status === 'failed' || status === 'cancelled' || status === 'error') return 'dash-status-error';
        if (status === 'waiting' || status === 'pending') return 'dash-status-warning';
        if (status === 'running') return 'dash-status-active';
        return 'dash-status-muted';
    }

    function statusLabel(status) {
        return String(status || '');
    }

    function renderStats(stats) {
        setText('statTotalMessages', formatNumber(stats.total_messages));
        setText('statActiveRooms', formatNumber(stats.active_rooms));
        setText('statUnreadRooms', formatNumber(stats.unread_rooms));
        setText('statPendingReminders', formatNumber(stats.pending_reminders));
        setText('statUpcomingToday', formatNumber(stats.upcoming_today));
    }

    function renderWallet(wallet) {
        setText('statWalletBalance', formatMoney(wallet.balance, wallet.currency));
    }

    function renderBadges(stats) {
        var shell = window.AppShell;
        if (!shell) return;
        shell.setBadge('navUnreadRooms', stats.unread_rooms);
        shell.setBadge('navPendingReminders', stats.pending_reminders);
        shell.setBadge('navUnreadNotifications', stats.unread_notifications);
        var combined = (stats.unread_rooms || 0) + (stats.pending_reminders || 0) + (stats.unread_notifications || 0);
        shell.setBadge('topbarUnreadBadge', combined);
    }

    function quotaClass(status) {
        if (status === 'good' || status === 'green') return 'good';
        if (status === 'warning' || status === 'yellow') return 'warning';
        if (status === 'critical' || status === 'orange') return 'critical';
        return 'exhausted';
    }

    function renderQuota(quota) {
        var container = el('quotaList');
        if (!container) return;
        var keys = Object.keys(quota || {});
        if (keys.length === 0) {
            container.innerHTML = '<div class="dash-empty"><i class="fas fa-chart-pie"></i><p>No quota information.</p></div>';
            return;
        }
        container.textContent = '';
        keys.forEach(function (key) {
            var q = quota[key];
            var pct = q.limit ? Math.min((q.used / q.limit) * 100, 100) : 0;

            var row = document.createElement('div');
            row.className = 'dash-quota-row';

            var top = document.createElement('div');
            top.className = 'dash-quota-top';

            var name = document.createElement('span');
            name.className = 'dash-quota-name';
            name.textContent = q.name;

            var value = document.createElement('span');
            value.className = 'dash-quota-value';
            value.textContent = q.used + ' / ' + q.limit + ' ' + (q.unit || '');

            top.appendChild(name);
            top.appendChild(value);

            var bar = document.createElement('div');
            bar.className = 'dash-quota-bar';
            var fill = document.createElement('div');
            fill.className = 'dash-quota-fill ' + quotaClass(q.status);
            fill.style.width = pct + '%';
            bar.appendChild(fill);

            row.appendChild(top);
            row.appendChild(bar);
            container.appendChild(row);
        });
    }

    function renderWorkflows(workflows) {
        var container = el('workflowSummary');
        if (!container) return;
        container.textContent = '';

        var meta = document.createElement('div');
        meta.className = 'dash-quota-top';
        meta.style.margin = '8px 0';

        var counts = document.createElement('span');
        counts.className = 'dash-quota-value';
        counts.textContent = workflows.active + ' active \u00b7 ' + workflows.running + ' running';

        meta.appendChild(counts);
        container.appendChild(meta);

        if (!workflows.recent || workflows.recent.length === 0) {
            var empty = document.createElement('div');
            empty.className = 'dash-empty';
            empty.innerHTML = '<i class="fas fa-diagram-project"></i><p>No workflow runs yet.</p>';
            container.appendChild(empty);
            return;
        }

        workflows.recent.forEach(function (run) {
            var item = document.createElement('div');
            item.className = 'dash-activity-item';

            var icon = document.createElement('span');
            icon.className = 'dash-activity-icon';
            icon.innerHTML = '<i class="fas fa-diagram-project"></i>';

            var body = document.createElement('div');
            body.className = 'dash-activity-body';

            var text = document.createElement('p');
            text.className = 'dash-activity-text';
            text.textContent = run.workflow;

            var metaText = document.createElement('div');
            metaText.className = 'dash-activity-meta';
            metaText.textContent = run.current_step || timeAgo(run.started_at);

            body.appendChild(text);
            body.appendChild(metaText);

            var status = document.createElement('span');
            status.className = 'dash-activity-status ' + statusClass(run.status);
            status.textContent = statusLabel(run.status);

            item.appendChild(icon);
            item.appendChild(body);
            item.appendChild(status);
            container.appendChild(item);
        });
    }

    function renderActivity(activity) {
        var container = el('activityFeed');
        if (!container) return;

        if (!activity || activity.length === 0) {
            container.innerHTML = '<div class="dash-empty"><i class="fas fa-inbox"></i><p>No recent activity. Start chatting with Mathia!</p></div>';
            return;
        }

        container.textContent = '';
        activity.forEach(function (item) {
            var row = document.createElement('div');
            row.className = 'dash-activity-item';

            var icon = document.createElement('span');
            icon.className = 'dash-activity-icon';
            icon.innerHTML = '<i class="fas ' + (ICONS[item.kind] || 'fa-circle') + '"></i>';

            var body = document.createElement('div');
            body.className = 'dash-activity-body';

            var text = document.createElement('p');
            text.className = 'dash-activity-text';
            text.textContent = item.text;

            var meta = document.createElement('div');
            meta.className = 'dash-activity-meta';
            meta.textContent = timeAgo(item.ts);

            body.appendChild(text);
            body.appendChild(meta);

            row.appendChild(icon);
            row.appendChild(body);

            if (item.status) {
                var status = document.createElement('span');
                status.className = 'dash-activity-status ' + statusClass(item.status);
                status.textContent = statusLabel(item.status);
                row.appendChild(status);
            }

            container.appendChild(row);
        });
    }

    function render(data) {
        if (!data) return;
        loaded = true;
        renderStats(data.stats || {});
        renderWallet(data.wallet || {});
        renderBadges(data.stats || {});
        renderQuota(data.quota || {});
        renderWorkflows(data.workflows || {});
        renderActivity(data.activity || []);
    }

    function load() {
        fetch(API_URL, { credentials: 'same-origin', headers: { 'X-Requested-With': 'XMLHttpRequest' } })
            .then(function (res) {
                if (!res.ok) throw new Error('HTTP ' + res.status);
                return res.json();
            })
            .then(render)
            .catch(function () {
                if (loaded) return;
                var feed = el('activityFeed');
                if (feed) {
                    feed.innerHTML = '<div class="dash-empty"><i class="fas fa-wifi"></i><p>Could not reach the dashboard API. Retrying&hellip;</p></div>';
                }
            });
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', function () {
            load();
            setInterval(load, POLL_MS);
        });
    } else {
        load();
        setInterval(load, POLL_MS);
    }
})();
