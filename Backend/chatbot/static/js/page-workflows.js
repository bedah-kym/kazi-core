/* Workflows pages: approval expiry countdown. */
(function () {
    'use strict';

    function init() {
        document.querySelectorAll('[data-expires]').forEach(function (el) {
            var label = el.querySelector('[data-expiry-label]');
            if (!label) return;
            var expiresAt = new Date(el.dataset.expires).getTime();
            function tick() {
                var left = expiresAt - Date.now();
                if (left <= 0) {
                    el.classList.remove('soon');
                    el.classList.add('expired');
                    label.textContent = 'Expired';
                    return;
                }
                var minutes = Math.floor(left / 60000);
                if (minutes < 15) {
                    el.classList.add('soon');
                    label.textContent = minutes <= 0 ? 'Expiring in under a minute' : 'Expires in ' + minutes + ' min';
                }
            }
            tick();
            setInterval(tick, 30000);
        });
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', init);
    } else {
        init();
    }
})();
