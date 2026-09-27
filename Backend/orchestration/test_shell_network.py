from __future__ import annotations

import unittest

from orchestration.shell.classifier import classify_command, extract_hosts


class ExtractHostsTests(unittest.TestCase):
    def test_ping_extracts_host_not_count(self):
        self.assertEqual(extract_hosts("ping -c 1 1.1.1.1"), ["1.1.1.1"])

    def test_curl_url_host(self):
        self.assertEqual(extract_hosts("curl https://example.com/path"), ["example.com"])

    def test_dig_host(self):
        self.assertEqual(extract_hosts("dig +short example.com"), ["example.com"])

    def test_ssh_user_host(self):
        self.assertEqual(extract_hosts("ssh user@host.example.com"), ["host.example.com"])

    def test_readonly_command_has_no_host(self):
        self.assertEqual(extract_hosts("ls -la"), [])


class AllowlistTests(unittest.TestCase):
    def test_allowlisted_when_every_host_is_listed(self):
        result = classify_command("ping -c 1 1.1.1.1", allowlist=["1.1.1.1"])
        self.assertTrue(result["allowlisted"])

    def test_not_allowlisted_when_a_host_is_missing(self):
        result = classify_command("ping -c 1 1.1.1.1", allowlist=["other.example"])
        self.assertFalse(result["allowlisted"])

    def test_no_hosts_is_never_allowlisted(self):
        result = classify_command("ls -la", allowlist=["1.1.1.1"])
        self.assertFalse(result["allowlisted"])
        self.assertEqual(result["hosts"], [])
