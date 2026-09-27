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


class RequestedNetworkTests(unittest.TestCase):
    def test_ip_route_counts_as_network(self):
        self.assertTrue(classify_command("ip route", "standard")["needs_network"])

    def test_requested_network_makes_a_plain_command_bounded(self):
        result = classify_command("/workspace/netcheck.sh", "standard", requested_network="bridge")
        self.assertTrue(result["needs_network"])
        self.assertEqual(result["tier"], "bounded")

    def test_requested_network_on_locked_is_denied(self):
        self.assertEqual(
            classify_command("ls", "locked", requested_network="bridge")["tier"],
            "denied",
        )
