/* App shell behaviour: theme toggle + mobile drawer.
   Plain JS, exposes window.AppShell for page scripts. */
(function () {
    'use strict';

    var THEME_KEY = 'theme';

    function applyTheme(theme) {
        var resolved = theme === 'dark' ? 'dark' : 'light';
        document.body.setAttribute('data-theme', resolved);
        document.documentElement.setAttribute('data-theme', resolved);
        document.body.classList.toggle('dark-mode', resolved === 'dark');
        try {
            localStorage.setItem(THEME_KEY, resolved);
        } catch (e) {
            /* storage unavailable — non-fatal */
        }
        var icon = document.querySelector('#darkModeToggle i');
        if (icon) {
            icon.className = resolved === 'dark' ? 'fas fa-sun' : 'fas fa-moon';
        }
    }

    function readTheme() {
        try {
            return localStorage.getItem(THEME_KEY) || 'light';
        } catch (e) {
            return 'light';
        }
    }

    function toggleTheme() {
        var current = document.body.getAttribute('data-theme') === 'dark' ? 'dark' : 'light';
        applyTheme(current === 'dark' ? 'light' : 'dark');
    }

    function toggleDrawer(open) {
        var sidebar = document.getElementById('appSidebar');
        var backdrop = document.getElementById('appBackdrop');
        var show = open !== undefined ? open : !sidebar.classList.contains('open');
        if (sidebar) sidebar.classList.toggle('open', show);
        if (backdrop) backdrop.classList.toggle('show', show);
    }

    function setBadge(id, count) {
        var el = document.getElementById(id);
        if (!el) return;
        var n = Number(count) || 0;
        if (n > 0) {
            el.textContent = n > 99 ? '99+' : String(n);
            el.classList.remove('d-none');
        } else {
            el.classList.add('d-none');
        }
    }

    function csrfToken() {
        var cookies = document.cookie.split(';');
        for (var i = 0; i < cookies.length; i++) {
            var c = cookies[i].trim();
            if (c.indexOf('csrftoken=') === 0) {
                return c.substring('csrftoken='.length);
            }
        }
        return '';
    }

    function initToastDismiss() {
        document.querySelectorAll('[data-auto-dismiss]').forEach(function (el) {
            setTimeout(function () {
                el.style.opacity = '0';
                el.style.transform = 'translateY(8px)';
                setTimeout(function () { el.remove(); }, 300);
            }, 3600);
        });
    }

    function init() {
        applyTheme(readTheme());

        var toggle = document.getElementById('darkModeToggle');
        if (toggle) toggle.addEventListener('click', toggleTheme);

        var burger = document.getElementById('appBurger');
        if (burger) burger.addEventListener('click', function () { toggleDrawer(); });

        var backdrop = document.getElementById('appBackdrop');
        if (backdrop) backdrop.addEventListener('click', function () { toggleDrawer(false); });

        initToastDismiss();
    }

    window.AppShell = {
        applyTheme: applyTheme,
        toggleTheme: toggleTheme,
        setBadge: setBadge,
        csrfToken: csrfToken
    };

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', init);
    } else {
        init();
    }
})();
