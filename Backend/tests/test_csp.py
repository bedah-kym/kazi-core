"""Contract tests for the enforced Content-Security-Policy header.

Every HTTP response goes through the real ``CSPMiddleware``; nothing above the
response is mocked. The inventory test reads the shipped templates and static
JS so a new CDN tag cannot drift away from the policy, and the pinned policy
test fails on any removal or widening.
"""
import re
from pathlib import Path

from axes.models import AccessAttempt, AccessLog
from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TestCase, override_settings
from django.urls import reverse

from Backend.settings import build_content_security_policy
from chatbot.models import Chatroom, Member

BACKEND = Path(__file__).resolve().parents[1]
HEADER = 'Content-Security-Policy'

SOCIAL_AUTHORIZE_ORIGINS = [
    'https://accounts.google.com',
    'https://github.com',
    'https://www.linkedin.com',
    'https://api.x.com',
]


def _parse_policy(value):
    directives = {}
    for part in (value or '').split(';'):
        part = part.strip()
        if not part:
            continue
        name, _, raw = part.partition(' ')
        directives[name] = raw.split()
    return directives


def _policy_from(response):
    return _parse_policy(response.headers.get(HEADER, ''))


def _origin(url):
    match = re.match(r'(https?://[^/]+)', url)
    return match.group(1) if match else None


def _inventory():
    """(path, directive, url) for every external resource the app loads."""
    items = []

    for path in sorted(BACKEND.glob('**/templates/**/*.html')):
        text = path.read_text(encoding='utf-8')
        for tag in re.findall(r'<script\b[^>]*>', text, re.I):
            match = re.search(r'\bsrc=["\'](https?://[^"\']+)', tag, re.I)
            if match:
                items.append((path, 'script-src', match.group(1)))
        for tag in re.findall(r'<link\b[^>]*>', text, re.I):
            match = re.search(r'\bhref=["\'](https?://[^"\']+)', tag, re.I)
            if not match:
                continue
            rel_match = re.search(r'\brel=["\']([^"\']+)', tag, re.I)
            rel = rel_match.group(1).lower() if rel_match else ''
            if 'stylesheet' in rel:
                items.append((path, 'style-src', match.group(1)))
            elif 'icon' in rel:
                items.append((path, 'img-src', match.group(1)))
        for tag in re.findall(r'<img\b[^>]*>', text, re.I):
            match = re.search(r'\bsrc=["\'](https?://[^"\']+)', tag, re.I)
            if match:
                items.append((path, 'img-src', match.group(1)))
        for tag in re.findall(r'<(?:iframe|audio|video|source)\b[^>]*>', text, re.I):
            match = re.search(r'\bsrc=["\'](https?://[^"\']+)', tag, re.I)
            if match:
                directive = 'frame-src' if tag.lower().startswith('<iframe') else 'media-src'
                items.append((path, directive, match.group(1)))
        for match in re.finditer(r"tileLayer\(\s*['\"](https?://[^'\"]+)", text):
            template = match.group(1).replace('{s}', 'a')
            for subdomain in ('a', 'b', 'c'):
                items.append((path, 'img-src', template.replace('a.tile', f'{subdomain}.tile')))

    for path in sorted(BACKEND.glob('**/static/**/*.js')):
        text = path.read_text(encoding='utf-8')
        for match in re.finditer(r"fetch\(\s*['\"](https?://[^'\"]+)", text):
            items.append((path, 'connect-src', match.group(1)))
        for match in re.finditer(r"url\(\s*['\"]?(https?://[^)'\"\s]+)", text):
            items.append((path, 'style-src', match.group(1)))

    return items


class CSPHeaderTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(
            username='csp-user', email='example@example.com',
            password='fake-password',  # nosec B106 — test fixture, not a real credential
        )
        member = Member.objects.create(User=self.user)
        self.room = Chatroom.objects.create()
        self.room.participants.add(member)

    def _assert_enforced(self, response):
        self.assertEqual(response.status_code, 200)
        value = response.headers.get(HEADER)
        self.assertIsNotNone(value, 'response has no Content-Security-Policy header')
        self.assertNotIn('Content-Security-Policy-Report-Only', response.headers)
        return _parse_policy(value)

    def test_chat_page_sends_enforced_policy(self):
        self.client.force_login(self.user)
        response = self.client.get(reverse('chatbot:bot-home', args=[self.room.id]))
        self._assert_enforced(response)

    def test_login_page_sends_enforced_policy(self):
        response = self.client.get(reverse('users:login'))
        self._assert_enforced(response)

    def test_json_api_sends_enforced_policy(self):
        self.client.force_login(self.user)
        response = self.client.get(reverse('chatbot:notification-status'))
        self._assert_enforced(response)


class CSPPolicyStrictnessTests(TestCase):
    def setUp(self):
        response = self.client.get(reverse('users:login'))
        self.assertIn(HEADER, response.headers, 'response has no Content-Security-Policy header')
        self.policy = _policy_from(response)

    def test_fetch_directives_allow_no_wildcards(self):
        for name in ('img-src', 'connect-src', 'media-src'):
            values = self.policy.get(name, [])
            self.assertTrue(values, f'{name} is missing from the policy')
            for value in values:
                self.assertNotIn('*', value, f'{name} allows a wildcard source: {value}')

    def test_no_scheme_only_sources(self):
        for name in ('img-src', 'connect-src', 'media-src', 'font-src', 'frame-src'):
            for value in self.policy.get(name, []):
                if value in ('data:', 'blob:'):
                    continue
                self.assertIsNone(
                    re.match(r'^[a-z][a-z0-9+.-]*:$', value),
                    f'{name} allows a scheme-only source: {value}',
                )

    def test_object_src_is_none(self):
        self.assertEqual(self.policy.get('object-src'), ["'none'"])

    def test_frame_ancestors_is_none(self):
        self.assertEqual(self.policy.get('frame-ancestors'), ["'none'"])

    def test_base_uri_is_self(self):
        self.assertEqual(self.policy.get('base-uri'), ["'self'"])

    def test_form_action_lists_the_social_authorize_origins(self):
        self.assertEqual(
            self.policy.get('form-action'),
            ["'self'"] + SOCIAL_AUTHORIZE_ORIGINS,
        )


class CSPIndirectLoadsTests(TestCase):
    def setUp(self):
        response = self.client.get(reverse('users:login'))
        self.assertIn(HEADER, response.headers, 'response has no Content-Security-Policy header')
        self.policy = _policy_from(response)

    def test_indirect_loads_are_covered(self):
        rows = [
            ('Google Fonts CSS loads its webfonts from gstatic', 'font-src', 'https://fonts.gstatic.com'),
            ('Font Awesome webfonts come from cdnjs', 'font-src', 'https://cdnjs.cloudflare.com'),
            ('the Calendly widget creates an iframe', 'frame-src', 'https://calendly.com'),
            ('emoji-mart fetches its data', 'connect-src', 'https://cdn.jsdelivr.net/npm/@emoji-mart/'),
            ('the OAuth form targets Google', 'form-action', 'https://accounts.google.com'),
            ('the OAuth form targets GitHub', 'form-action', 'https://github.com'),
            ('the OAuth form targets LinkedIn', 'form-action', 'https://www.linkedin.com'),
            ('the OAuth form targets X', 'form-action', 'https://api.x.com'),
            ('main.js previews use blob:', 'img-src', 'blob:'),
            ('onboarding uses createObjectURL', 'img-src', 'blob:'),
            ('avatar-upload cropper uses blob:', 'img-src', 'blob:'),
            ('inline avatars use data:', 'img-src', 'data:'),
        ]
        for reason, directive, source in rows:
            with self.subTest(reason):
                self.assertIn(source, self.policy.get(directive, []), reason)


class CSPPinnedPolicyTests(TestCase):
    def test_full_policy_is_pinned(self):
        self.assertEqual(
            build_content_security_policy(
                '/uploads/', ['https://app.example.test'], ['app.example.test'],
            ),
            {
                'default-src': ["'self'"],
                'script-src': [
                    "'self'",
                    "'unsafe-inline'",
                    'https://cdnjs.cloudflare.com',
                    'https://cdn.jsdelivr.net',
                    'https://unpkg.com',
                    'https://code.jquery.com',
                    'https://assets.calendly.com',
                ],
                'style-src': [
                    "'self'",
                    "'unsafe-inline'",
                    'https://fonts.googleapis.com',
                    'https://cdnjs.cloudflare.com',
                    'https://cdn.jsdelivr.net',
                    'https://unpkg.com',
                    'https://assets.calendly.com',
                ],
                'font-src': [
                    "'self'",
                    'data:',
                    'https://fonts.gstatic.com',
                    'https://cdnjs.cloudflare.com',
                ],
                'img-src': [
                    "'self'",
                    'data:',
                    'blob:',
                    'https://ui-avatars.com',
                    'https://a.tile.openstreetmap.org',
                    'https://b.tile.openstreetmap.org',
                    'https://c.tile.openstreetmap.org',
                ],
                'media-src': ["'self'", 'blob:'],
                'connect-src': [
                    "'self'",
                    'wss://app.example.test',
                    'https://cdn.jsdelivr.net/npm/@emoji-mart/',
                ],
                'frame-src': ["'self'", 'https://calendly.com'],
                'object-src': ["'none'"],
                'base-uri': ["'self'"],
                'form-action': ["'self'"] + SOCIAL_AUTHORIZE_ORIGINS,
                'frame-ancestors': ["'none'"],
            },
        )


class CSPWebSocketOriginTests(TestCase):
    def test_lite_http_origin_becomes_ws(self):
        directives = build_content_security_policy(
            '/uploads/', ['http://127.0.0.1:8000'], ['127.0.0.1'],
        )
        self.assertIn('ws://127.0.0.1:8000', directives['connect-src'])

    def test_production_https_origin_becomes_wss(self):
        directives = build_content_security_policy(
            '/uploads/', ['https://app.example.test'], ['app.example.test'],
        )
        self.assertIn('wss://app.example.test', directives['connect-src'])

    def test_empty_trusted_origins_add_no_websocket_source(self):
        directives = build_content_security_policy('/uploads/', [], [])
        websocket_sources = [
            value for value in directives['connect-src']
            if value.startswith(('ws://', 'wss://'))
        ]
        self.assertEqual(websocket_sources, [])

    def test_allowed_hosts_do_not_add_portless_websocket_sources(self):
        directives = build_content_security_policy(
            '/uploads/', [], ['app.example.test', '.example.com'],
        )
        self.assertNotIn('ws://app.example.test', directives['connect-src'])
        self.assertNotIn('wss://app.example.test', directives['connect-src'])
        self.assertNotIn('ws://.example.com', directives['connect-src'])
        self.assertNotIn('wss://.example.com', directives['connect-src'])


class CSPMediaOriginTests(TestCase):
    def test_relative_media_url_adds_no_origin(self):
        directives = build_content_security_policy('/uploads/', [], [])
        self.assertEqual(
            directives['img-src'],
            [
                "'self'",
                'data:',
                'blob:',
                'https://ui-avatars.com',
                'https://a.tile.openstreetmap.org',
                'https://b.tile.openstreetmap.org',
                'https://c.tile.openstreetmap.org',
            ],
        )
        self.assertEqual(directives['media-src'], ["'self'", 'blob:'])

    def test_absolute_media_url_is_allowed_for_media_and_connect(self):
        directives = build_content_security_policy(
            'https://media.example.test/uploads/', [], [],
        )
        for name in ('img-src', 'media-src', 'connect-src'):
            self.assertIn('https://media.example.test', directives[name])

    def test_header_uses_the_overridden_policy(self):
        directives = build_content_security_policy(
            'https://media.example.test/uploads/', [], [],
        )
        with override_settings(CONTENT_SECURITY_POLICY={'DIRECTIVES': directives}):
            response = self.client.get(reverse('users:login'))
        self.assertEqual(response.status_code, 200)
        policy = _policy_from(response)
        self.assertIn('https://media.example.test', policy['img-src'])
        self.assertIn('https://media.example.test', policy['connect-src'])


class CSPInventoryTests(TestCase):
    def setUp(self):
        response = self.client.get(reverse('users:login'))
        self.assertIn(HEADER, response.headers, 'response has no Content-Security-Policy header')
        self.policy = _policy_from(response)
        self.items = _inventory()

    def test_inventory_finds_external_resources(self):
        self.assertIn(
            ('script-src', 'https://cdnjs.cloudflare.com'),
            [(directive, _origin(url)) for _, directive, url in self.items],
        )

    def test_every_loaded_host_is_allowed_by_its_directive(self):
        for path, directive, url in self.items:
            host = _origin(url)
            self.assertIn(
                host, self.policy.get(directive, []),
                f'{path.relative_to(BACKEND)} loads {host} but {directive} does not allow it',
            )


class CSPLockoutTests(TestCase):
    def setUp(self):
        AccessAttempt.objects.all().delete()
        AccessLog.objects.all().delete()
        cache.clear()
        self.addCleanup(AccessAttempt.objects.all().delete)
        self.addCleanup(AccessLog.objects.all().delete)
        self.addCleanup(cache.clear)
        self.user = get_user_model().objects.create_user(
            username='csp-locked', email='example@example.com',
            password='fake-password',  # nosec B106 — test fixture, not a real credential
        )

    def test_locked_out_login_response_carries_csp(self):
        url = reverse('users:login')
        for _ in range(settings.AXES_FAILURE_LIMIT):
            self.client.post(url, {'username': 'csp-locked', 'password': 'wrong'})  # nosec B105
        response = self.client.post(url, {'username': 'csp-locked', 'password': 'wrong'})  # nosec B105
        self.assertEqual(response.status_code, settings.AXES_HTTP_RESPONSE_CODE)
        self.assertIn(HEADER, response.headers)
