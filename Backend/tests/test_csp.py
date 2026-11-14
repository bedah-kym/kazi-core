"""Contract tests for the enforced Content-Security-Policy header.

Every HTTP response goes through the real ``CSPMiddleware``; nothing above the
response is mocked. The inventory test reads the shipped templates and static
JS so a new CDN tag cannot drift away from the policy.
"""
import re
from pathlib import Path

from django.conf import settings
from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse

from chatbot.models import Chatroom, Member

BACKEND = Path(__file__).resolve().parents[1]
HEADER = 'Content-Security-Policy'


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
                self.assertNotEqual(value, 'https:', f'{name} allows any https host')

    def test_object_src_is_none(self):
        self.assertEqual(self.policy.get('object-src'), ["'none'"])

    def test_frame_ancestors_is_none(self):
        self.assertEqual(self.policy.get('frame-ancestors'), ["'none'"])

    def test_base_uri_is_self(self):
        self.assertEqual(self.policy.get('base-uri'), ["'self'"])


class CSPMediaOriginTests(TestCase):
    def _directives(self):
        policy = getattr(settings, 'CONTENT_SECURITY_POLICY', {})
        return policy.get('DIRECTIVES', {})

    @override_settings(MEDIA_URL='/uploads/')
    def test_relative_media_url_adds_no_origin(self):
        directives = self._directives()
        self.assertTrue(directives, 'CONTENT_SECURITY_POLICY.DIRECTIVES is missing')
        self.assertEqual(directives['media-src'], ["'self'", 'blob:'])
        self.assertNotIn('/uploads/', directives['img-src'])

    @override_settings(MEDIA_URL='https://media.example.test/uploads/')
    def test_absolute_media_url_is_allowed(self):
        directives = self._directives()
        self.assertTrue(directives, 'CONTENT_SECURITY_POLICY.DIRECTIVES is missing')
        self.assertIn('https://media.example.test', directives['img-src'])
        self.assertIn('https://media.example.test', directives['media-src'])


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
