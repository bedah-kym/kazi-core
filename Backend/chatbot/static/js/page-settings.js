/* Settings page: scrollspy + platform invite. */
(function () {
    'use strict';

    function initScrollSpy() {
        if (window.location.hash) {
            var target = document.querySelector(window.location.hash);
            if (target) target.scrollIntoView({ behavior: 'smooth' });
        }
        var links = document.querySelectorAll('#settingsNav .nav-link');
        var sections = document.querySelectorAll('.settings-section');
        if (!links.length || !sections.length) return;

        function onScroll() {
            var current = '';
            sections.forEach(function (section) {
                if (window.scrollY >= section.offsetTop - 120) current = section.id;
            });
            links.forEach(function (link) {
                link.classList.toggle('active', link.getAttribute('href') === '#' + current);
            });
        }
        window.addEventListener('scroll', onScroll);
        onScroll();
    }

    function initInvite() {
        var sendBtn = document.getElementById('sendInviteBtn');
        if (!sendBtn) return;

        sendBtn.addEventListener('click', function () {
            var emailInput = document.getElementById('inviteEmail');
            var alertDiv = document.getElementById('inviteAlert');
            var email = emailInput.value.trim();
            if (!email) return;

            var url = sendBtn.dataset.inviteUrl;
            var csrf = window.AppShell ? window.AppShell.csrfToken() : '';

            sendBtn.disabled = true;
            fetch(url, {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/x-www-form-urlencoded',
                    'X-CSRFToken': csrf
                },
                body: 'email=' + encodeURIComponent(email)
            })
                .then(function (r) { return r.json(); })
                .then(function (data) {
                    alertDiv.classList.remove('d-none', 'alert-danger', 'alert-success');
                    if (data.ok) {
                        alertDiv.classList.add('alert-success');
                        alertDiv.textContent = 'Invite sent to ' + email + '! ' + data.remaining + ' remaining.';
                        emailInput.value = '';
                        setTimeout(function () { window.location.reload(); }, 1500);
                    } else {
                        alertDiv.classList.add('alert-danger');
                        alertDiv.textContent = data.error;
                    }
                    sendBtn.disabled = false;
                })
                .catch(function () {
                    alertDiv.classList.remove('d-none', 'alert-success');
                    alertDiv.classList.add('alert-danger');
                    alertDiv.textContent = 'Something went wrong. Please try again.';
                    sendBtn.disabled = false;
                });
        });
    }

    function init() {
        initScrollSpy();
        initInvite();
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', init);
    } else {
        init();
    }
})();
