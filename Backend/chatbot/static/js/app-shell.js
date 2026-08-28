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

    function init() {
        applyTheme(readTheme());

        var toggle = document.getElementById('darkModeToggle');
        if (toggle) toggle.addEventListener('click', toggleTheme);

        var burger = document.getElementById('appBurger');
        if (burger) burger.addEventListener('click', function () { toggleDrawer(); });

        var backdrop = document.getElementById('appBackdrop');
        if (backdrop) backdrop.addEventListener('click', function () { toggleDrawer(false); });
    }

    window.AppShell = {
        applyTheme: applyTheme,
        toggleTheme: toggleTheme,
        setBadge: setBadge
    };

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', init);
    } else {
        init();
    }
})();
