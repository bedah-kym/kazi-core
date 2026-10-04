"""Enforced shell egress: network mode, risk gate, host grants, sidecar wiring.

See ``docs/plans/2026-10-shell-egress-proxy.md``. Docker is mocked throughout;
real-daemon verification is ``scripts/verify_shell_egress.py``.
"""
from __future__ import annotations

import asyncio
import json
import sys
import tempfile
from contextlib import ExitStack, suppress
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
from asgiref.sync import async_to_sync
from django.contrib.auth import get_user_model
from django.test import SimpleTestCase, TestCase, override_settings
from django.utils import timezone

from chatbot.models import Chatroom, Member
from orchestration.agent_loop import _bucket_tool_calls
from orchestration.connectors.shell_connector import ShellConnector
from orchestration.coordinator import OrchestrationCoordinator
from orchestration.models import ActionReceipt, ShellHostGrant
from orchestration.shell import egress, host_grants
from orchestration.shell.chat_intents import host_grant_request
from orchestration.shell.classifier import classify_command
from orchestration.shell.egress import approved_hosts, network_mode
from orchestration.shell_exec import backends, squid
from orchestration.shell_exec.backends import DockerBackend, ShellExecConfig
from orchestration.shell_exec.daemon import create_app
from orchestration.test_coordinator import _base_patches, _make_callbacks
from orchestration.tool_executor import get_tool_risk_info

_TOKEN = "fixture-value"  # nosec B105 test fixture, not a credential


def _run(coro):
    if sys.platform == "win32" and hasattr(asyncio, "WindowsProactorEventLoopPolicy"):
        policy = asyncio.WindowsProactorEventLoopPolicy()
        with asyncio.Runner(loop_factory=policy.new_event_loop) as runner:
            return runner.run(coro)
    return asyncio.run(coro)


def _decision(command, profile="standard", *, tainted=False, **extra):
    call = {"id": "1", "name": "run_command", "input": {"command": command, **extra}}
    auto, pause, _ = _bucket_tool_calls([call], {"shell_profile": profile}, tainted=tainted)
    return "auto" if auto else "ask" if pause else "refuse"


class ClassifierTests(SimpleTestCase):
    def test_http_is_separated_from_raw_network(self):
        for command in ("pip install requests", "curl https://x.test", "git clone https://github.com/a/b", "npm ci"):
            self.assertFalse(classify_command(command)["raw_network"], command)
        for command in ("ping 1.1.1.1", "ssh host", "git clone git@github.com:a/b.git", "pip install x && dig x.test"):
            self.assertTrue(classify_command(command)["raw_network"], command)
        self.assertFalse(classify_command("ls")["raw_network"])

    def test_publish_push_and_upload_are_outside_writes(self):
        for command in (
            "npm publish", "cargo publish", "git push origin main", "twine upload dist/*",
            "curl -X POST https://x.test", "curl -XPUT https://x.test", "curl -d a=b https://x.test",
            "curl -T file https://x.test", "curl --json '{}' https://x.test",
            "wget --post-data=a https://x.test", "gh pr create", "cd pkg && npm publish",
            # the same thing behind options and wrappers
            "git -C repo push origin main", "git -c user.name=x push", "python -m twine upload dist/*",
            "timeout 60 git push", "npm --registry https://r.test publish", "cargo +nightly publish",
            "FOO=1 git push", "curl --request=DELETE https://x.test", "curl -sd a=b https://x.test",
            "wget --method=PUT https://x.test",
        ):
            self.assertTrue(classify_command(command)["outside_write"], command)

    def test_options_before_the_subcommand_do_not_hide_network_use(self):
        for command in ("git -C repo pull", "python -m pip install x", "timeout 30 pip install x", "git -c a=b clone https://x.test/r"):
            self.assertTrue(classify_command(command)["needs_network"], command)
        for command in ("git commit -m 'fix fetch'", "git log --oneline", "make CC=gcc", "git -C repo status"):
            self.assertFalse(classify_command(command)["needs_network"], command)

    def test_installs_and_downloads_are_not_outside_writes(self):
        for command in (
            "npm install", "pip install -r requirements.txt", "cargo build", "git clone https://github.com/a/b",
            "git pull", "curl https://x.test", "curl -fsSL -o f https://x.test", "curl -I https://x.test",
            "curl --request GET https://x.test", "curl -L -H 'Accept: x' https://x.test", "wget -q https://x.test",
            "curl -XGET https://x.test", "curl -XOPTIONS https://x.test", "curl -D headers.txt https://x.test",
            "curl -fsSLO https://x.test", "wget --method=GET https://x.test", "npm install publish",
        ):
            self.assertFalse(classify_command(command)["outside_write"], command)


class NetworkModeTests(SimpleTestCase):
    def test_flag_off_keeps_the_open_bridge(self):
        self.assertEqual(network_mode(classify_command("pip install x"), "standard"), "bridge")
        self.assertEqual(network_mode(classify_command("ls"), "standard"), "none")

    @override_settings(SHELL_EGRESS_PROXY=True)
    def test_flag_on_proxies_only_http_on_standard(self):
        self.assertEqual(network_mode(classify_command("pip install x"), "standard"), "proxy")
        self.assertEqual(network_mode(classify_command("ping 1.1.1.1"), "standard"), "bridge")
        self.assertEqual(network_mode(classify_command("ls"), "standard"), "none")
        self.assertEqual(network_mode(classify_command("pip install x", "open"), "open"), "bridge")
        script = classify_command("python fetch.py", requested_network="bridge")
        self.assertEqual(network_mode(script, "standard"), "proxy")


class RiskGateTests(SimpleTestCase):
    def test_flag_off_is_phase_one_behaviour(self):
        self.assertEqual(_decision("pip install requests"), "ask")
        self.assertEqual(_decision("pip install requests", tainted=True), "ask")

    @override_settings(SHELL_EGRESS_PROXY=True)
    def test_untainted_proxied_commands_need_no_prompt(self):
        for command in ("pip install requests", "curl https://unlisted.test", "git clone https://github.com/a/b"):
            self.assertEqual(_decision(command), "auto", command)
        self.assertEqual(_decision("python fetch.py", network="bridge"), "auto")

    @override_settings(SHELL_EGRESS_PROXY=True)
    def test_a_tainted_run_still_asks_for_any_network(self):
        """An approved host is still an exfiltration path (#134 guidance)."""
        for command in ("pip install requests", "curl https://pypi.org/simple/", "git clone https://github.com/a/b"):
            self.assertEqual(_decision(command, tainted=True), "ask", command)
        self.assertEqual(_decision("python fetch.py", tainted=True, network="bridge"), "ask")
        self.assertEqual(_decision("ls", tainted=True), "auto")

    @override_settings(SHELL_EGRESS_PROXY=True)
    def test_publish_push_and_upload_always_ask(self):
        for command in ("npm publish", "cargo publish", "git push https://github.com/a/b", "curl -X POST https://x.test"):
            self.assertEqual(_decision(command), "ask", command)

    @override_settings(SHELL_EGRESS_PROXY=True, SHELL_EXEC_NETWORK_ALLOWLIST=["x.test"])
    def test_operator_allowlist_does_not_exempt_an_outside_write(self):
        self.assertEqual(_decision("curl https://x.test"), "auto")
        self.assertEqual(_decision("curl -d secret=1 https://x.test"), "ask")

    @override_settings(SHELL_EGRESS_PROXY=True, SHELL_EXEC_NETWORK_ALLOWLIST=["ops.test"])
    def test_operator_allowlisted_raw_command_keeps_its_phase_one_behaviour(self):
        """The #134 shortlist still skips the prompt for a raw command; it runs on the bridge."""
        info = get_tool_risk_info("run_command", {"shell_profile": "standard"}, {"command": "ping ops.test"})
        self.assertEqual(info["network_mode"], "bridge")
        self.assertFalse(info["requires_confirmation"])
        self.assertEqual(_decision("ping ops.test", tainted=True), "ask")
        self.assertEqual(_decision("ping other.test"), "ask")

    @override_settings(SHELL_EGRESS_PROXY=True)
    def test_raw_network_and_destructive_still_ask(self):
        self.assertEqual(_decision("ping 1.1.1.1"), "ask")
        self.assertEqual(_decision("ssh build.example.com", tainted=True), "ask")
        self.assertEqual(_decision("pip install x && rm -rf /"), "ask")
        self.assertEqual(_decision("git clone git@github.com:a/b.git"), "ask")

    @override_settings(SHELL_EGRESS_PROXY=True)
    def test_other_profiles_are_unchanged(self):
        self.assertEqual(_decision("pip install x", "locked"), "refuse")
        self.assertEqual(_decision("pip install x", "open"), "auto")
        self.assertEqual(_decision("pip install x", "open", tainted=True), "ask")
        self.assertEqual(_decision("npm publish", "open"), "auto")

    @override_settings(SHELL_EGRESS_PROXY=True)
    def test_basis_and_mode_are_reported(self):
        info = get_tool_risk_info("run_command", {"shell_profile": "standard"}, {"command": "pip install x"})
        self.assertEqual(info["approval_basis"], "egress_proxy")
        self.assertEqual(info["network_mode"], "proxy")
        self.assertTrue(info["egress"])
        publish = get_tool_risk_info("run_command", {"shell_profile": "standard"}, {"command": "npm publish"})
        self.assertEqual(publish["approval_basis"], "")
        self.assertEqual(publish["network_mode"], "proxy")


class HostListTests(SimpleTestCase):
    def test_clean_hosts_validates_and_removes_overlap(self):
        cleaned = squid.clean_hosts([
            "PyPI.org", ".pythonhosted.org", "files.pythonhosted.org", "pythonhosted.org",
            ".a.pythonhosted.org", "x.test", "x.test",
        ])
        self.assertEqual(cleaned, ["pypi.org", ".pythonhosted.org", "x.test"])

    def test_ip_literals_and_lookalikes_are_never_hosts(self):
        for bad in ("1.1.1.1", "127.1", "0x7f.0x0.0x0.0x1", "8.8.2056", "localhost", "bad host",
                    "http://x.test", "x.test:443", "*.x.test", "", None, 5, "-x.test", "x..test"):
            self.assertFalse(squid.valid_host_entry(bad), bad)
        self.assertEqual(squid.clean_hosts(["1.1.1.1", "localhost", "ok.example"]), ["ok.example"])

    def test_every_built_in_default_is_a_valid_entry(self):
        self.assertEqual(squid.clean_hosts(egress.DEFAULT_HOSTS), list(egress.DEFAULT_HOSTS))
        for excluded in ("proxy.golang.org", "sum.golang.org", "storage.googleapis.com", "s3.amazonaws.com",
                         "api.github.com", "gist.github.com", "upload.pypi.org", "crates.io",
                         "cdn.jsdelivr.net", "unpkg.com", "cdnjs.cloudflare.com"):
            self.assertNotIn(excluded, egress.DEFAULT_HOSTS)
        self.assertFalse(any(entry.startswith(".") for entry in egress.DEFAULT_HOSTS))

    def test_config_only_tunnels_tls_to_approved_names(self):
        config = squid.render_config(3128)
        for line in (
            "http_access deny !kazi_connect",
            "http_access deny !kazi_tls",
            "http_access deny kazi_ip_literal",
            "http_access deny !kazi_hosts",
            "http_access deny kazi_private",
            "http_access deny all",
            "ssl_bump peek kazi_step1",
            "ssl_bump splice kazi_sni",
            "ssl_bump terminate all",
            "cache deny all",
        ):
            self.assertIn(line, config)
        self.assertNotIn("ssl_bump bump", config)
        self.assertLess(config.index("http_access deny kazi_private"), config.index("http_access allow"))
        for network in ("127.0.0.0/8", "10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16", "169.254.0.0/16", "fc00::/7"):
            self.assertIn(network, config)

    def test_private_ranges_do_not_swallow_all_of_ipv4(self):
        """Squid stores IPv4 as IPv4-mapped IPv6: ::ffff:0:0/96 would mean "everything"."""
        private = next(line for line in squid.render_config().splitlines() if line.startswith("acl kazi_private"))
        self.assertNotIn("::ffff:", private)
        self.assertNotIn("0.0.0.0/0", private)
        self.assertNotIn("::/0", private)

    def test_denied_hosts_are_parsed_defensively(self):
        log = (
            "TCP_DENIED CONNECT evil.example:443 -\n"
            "NONE_NONE CONNECT pypi.org:443 pypi.org\n"
            "TCP_TUNNEL CONNECT pypi.org:443 pypi.org\n"
            "NONE_NONE CONNECT pypi.org:443 fronted.example\n"
            "TCP_DENIED GET http://plain.example/x -\n"
            "TCP_DENIED CONNECT port.example:22 -\n"
            "TCP_DENIED CONNECT 1.2.3.4:443 -\n"
            "TCP_DENIED CONNECT [fe80::1%x]:443 -\n"
            "TCP_DENIED CONNECT 10.0.0.1.nip.io:443 10.0.0.1.nip.io\n"
            "NONE_NONE GET https://10.0.0.1.nip.io/ 10.0.0.1.nip.io\n"
            "TCP_DENIED CONNECT evil.example:443 -\n"
            "garbage\n"
        )
        # Only refused HTTPS tunnels and fronted names: approving a host cannot
        # fix a wrong port, plain HTTP, an IP literal or a private address.
        self.assertEqual(
            squid.parse_denied(log, ["pypi.org", "10.0.0.1.nip.io"]), ("evil.example", "fronted.example"),
        )

    def test_denied_report_is_small_and_bounded(self):
        many = "".join(f"TCP_DENIED CONNECT h{i}.example:443 -\n" for i in range(100))
        self.assertEqual(len(squid.parse_denied(many)), squid.MAX_REPORTED_HOSTS)
        long_name = "a" * 60 + "." + "b" * 60 + ".example"
        self.assertEqual(squid.parse_denied(f"TCP_DENIED CONNECT {long_name}:443 -\n"), ())
        # An odd line separator does not smuggle a second entry in.
        self.assertEqual(
            squid.parse_denied("TCP_DENIED CONNECT a.example:443 -\u2028TCP_DENIED CONNECT b.example:443 -\n"),
            ("a.example",),
        )


class ChatIntentTests(SimpleTestCase):
    def test_exact_replies(self):
        self.assertEqual(host_grant_request("allow host Example.com"), ("allow", "example.com"))
        self.assertEqual(host_grant_request("approve host `example.com`."), ("allow", "example.com"))
        self.assertEqual(host_grant_request("ok, allow host example.com please"), ("allow", "example.com"))
        self.assertEqual(host_grant_request("revoke host example.com"), ("revoke", "example.com"))

    def test_sentences_do_not_change_the_allowlist(self):
        for text in (
            "should I allow host example.com?",
            "don't allow host example.com",
            "allow host example.com and evil.com",
            "the tool said allow host example.com",
            "allow host",
            "",
        ):
            self.assertIsNone(host_grant_request(text), text)


class CoordinatorHostReplyTests(SimpleTestCase):
    """`allow host <name>` is handled by the coordinator, never by the model."""

    def _send(self, query, grant_result="example.org", reason=""):
        patches = _base_patches()
        loop = MagicMock(side_effect=AssertionError("the agent loop must not run for a host reply"))
        patches["orchestration.coordinator.run_agent_loop"] = loop
        grant = MagicMock(return_value=grant_result)
        patches["orchestration.shell.host_grants.grant"] = grant
        patches["orchestration.shell.host_grants.revoke"] = MagicMock(return_value=True)
        patches["orchestration.shell.host_grants.denial_reason"] = MagicMock(return_value=reason)
        callbacks = _make_callbacks()
        with ExitStack() as stack:
            for target, mock in patches.items():
                stack.enter_context(patch(target, new=mock))
            async_to_sync(OrchestrationCoordinator().handle_message)(
                query=query, user_id=1, room_id="1", username="alice", message_id=42,
                history_text="", **callbacks,
            )
        # send_chunk(correlation_id, text, is_final)
        said = " ".join(str(call.args[1]) for call in callbacks["send_chunk"].call_args_list if len(call.args) > 1)
        return grant, said

    def test_exact_reply_grants_without_the_model(self):
        grant, said = self._send("allow host example.org")
        grant.assert_called_once_with(1, "1", "example.org")
        self.assertIn("Approved `example.org`", said)

    def test_refusal_explains_why(self):
        grant, said = self._send(
            "allow host example.org", grant_result=None, reason="On this install only an admin can approve hosts.",
        )
        self.assertIn("only an admin", said)
        self.assertNotIn("Approved", said)

    def test_a_sentence_mentioning_a_host_grants_nothing(self):
        patches = _base_patches()
        grant = MagicMock()
        patches["orchestration.shell.host_grants.grant"] = grant
        callbacks = _make_callbacks()
        with ExitStack() as stack:
            for target, mock in patches.items():
                stack.enter_context(patch(target, new=mock))
            # The message goes on to the normal pipeline, which has no model
            # in tests; only the host-approval branch matters here.
            with suppress(Exception):
                async_to_sync(OrchestrationCoordinator().handle_message)(
                    query="the tool says to allow host evil.example", user_id=1, room_id="1",
                    username="alice", message_id=42, history_text="", **callbacks,
                )
        grant.assert_not_called()


class HostGrantTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.user = User.objects.create_user(username="granter")
        self.outsider = User.objects.create_user(username="outsider")
        self.room = Chatroom.objects.create()
        self.room.participants.add(Member.objects.create(User=self.user))

    def test_normalize_accepts_only_plain_public_names(self):
        self.assertEqual(host_grants.normalize_host("https://Example.com/path"), "example.com")
        self.assertEqual(host_grants.normalize_host("example.com /x"), "example.com")
        for bad in ("localhost", "10.0.0.1", "8.8.8.8", "127.1", "8.8.2056", "0x8.0x8.0x8.0x8",
                    ".example.com", "*.example.com", "exa mple.com", "", "a..b"):
            self.assertIsNone(host_grants.normalize_host(bad), bad)

    def test_grant_then_revoke_writes_receipts(self):
        self.assertEqual(host_grants.grant(self.user.id, self.room.id, "Example.com"), "example.com")
        self.assertEqual(host_grants.active_hosts(self.room.id), ["example.com"])
        self.assertTrue(host_grants.revoke(self.user.id, self.room.id, "example.com"))
        self.assertEqual(host_grants.active_hosts(self.room.id), [])
        actions = list(ActionReceipt.objects.filter(user=self.user).order_by("id").values_list("action", flat=True))
        self.assertEqual(actions, [host_grants.GRANT_ACTION, host_grants.REVOKE_ACTION])

    def test_non_member_cannot_grant_or_revoke(self):
        self.assertIsNone(host_grants.grant(self.outsider.id, self.room.id, "example.com"))
        self.assertIn("member", host_grants.denial_reason(self.outsider.id, self.room.id, "example.com"))
        host_grants.grant(self.user.id, self.room.id, "example.com")
        self.assertFalse(host_grants.revoke(self.outsider.id, self.room.id, "example.com"))
        self.assertEqual(host_grants.active_hosts(self.room.id), ["example.com"])

    def test_members_may_approve_by_default(self):
        self.assertEqual(host_grants.approvers(), "members")
        self.assertEqual(host_grants.denial_reason(self.user.id, self.room.id, "example.com"), "")

    @override_settings(SHELL_HOST_GRANT_APPROVERS="staff")
    def test_staff_switch_limits_approval_to_staff_members(self):
        self.assertIsNone(host_grants.grant(self.user.id, self.room.id, "example.com"))
        self.assertIn("admin", host_grants.denial_reason(self.user.id, self.room.id, "example.com"))

        self.user.is_staff = True
        self.user.save(update_fields=["is_staff"])
        self.assertEqual(host_grants.grant(self.user.id, self.room.id, "example.com"), "example.com")

        staff_outsider = get_user_model().objects.create_user(username="staffoutsider", is_staff=True)
        self.assertIsNone(host_grants.grant(staff_outsider.id, self.room.id, "other.example"))

    @override_settings(SHELL_HOST_GRANT_APPROVERS="staff")
    def test_any_member_may_still_revoke_under_the_staff_switch(self):
        friend = get_user_model().objects.create_user(username="friend")
        self.room.participants.add(Member.objects.create(User=friend))
        self.user.is_staff = True
        self.user.save(update_fields=["is_staff"])
        host_grants.grant(self.user.id, self.room.id, "example.com")
        self.assertTrue(host_grants.revoke(friend.id, self.room.id, "example.com"))

    @override_settings(SHELL_HOST_GRANT_APPROVERS="everyone-please")
    def test_unknown_switch_value_means_the_stricter_mode(self):
        self.assertEqual(host_grants.approvers(), "staff")
        self.assertIsNone(host_grants.grant(self.user.id, self.room.id, "example.com"))

    def test_no_receipt_means_no_grant(self):
        with patch("orchestration.shell.host_grants._receipt", return_value=False):
            self.assertIsNone(host_grants.grant(self.user.id, self.room.id, "example.com"))
        self.assertEqual(host_grants.active_hosts(self.room.id), [])

    def test_expired_grant_is_inactive_and_regrant_replaces(self):
        host_grants.grant(self.user.id, self.room.id, "example.com")
        ShellHostGrant.objects.update(expires_at=timezone.now())
        self.assertEqual(host_grants.active_hosts(self.room.id), [])
        host_grants.grant(self.user.id, self.room.id, "example.com")
        host_grants.grant(self.user.id, self.room.id, "example.com")
        self.assertEqual(host_grants.active_hosts(self.room.id), ["example.com"])

    def test_grants_are_room_scoped(self):
        other = Chatroom.objects.create()
        host_grants.grant(self.user.id, self.room.id, "example.com")
        self.assertEqual(host_grants.active_hosts(other.id), [])

    @override_settings(SHELL_EXEC_NETWORK_ALLOWLIST=["Ops.test", "1.1.1.1"], SHELL_EGRESS_DEFAULT_HOSTS=["pypi.org"])
    def test_approved_hosts_combines_all_three_sources_and_drops_ips(self):
        host_grants.grant(self.user.id, self.room.id, "example.com")
        # Explicit approvals first, so the size cap can only ever drop defaults.
        self.assertEqual(approved_hosts(self.room.id), ["example.com", "ops.test", "pypi.org"])

    @override_settings(SHELL_EXEC_NETWORK_ALLOWLIST=[], SHELL_EGRESS_DEFAULT_HOSTS=None)
    def test_built_in_defaults_apply_when_the_setting_is_unset(self):
        self.assertEqual(approved_hosts(self.room.id), list(egress.DEFAULT_HOSTS))

    @override_settings(SHELL_EXEC_NETWORK_ALLOWLIST=[], SHELL_EGRESS_DEFAULT_HOSTS=[])
    def test_an_empty_setting_removes_the_defaults(self):
        self.assertEqual(approved_hosts(self.room.id), [])

    @override_settings(SHELL_EXEC_NETWORK_ALLOWLIST=[], SHELL_EGRESS_DEFAULT_HOSTS=["pypi.org"])
    def test_grant_lookup_failure_drops_only_the_grants(self):
        with patch("orchestration.shell.host_grants.active_hosts", side_effect=RuntimeError("db down")):
            self.assertEqual(approved_hosts(self.room.id), ["pypi.org"])


class ConnectorTests(TestCase):
    def setUp(self):
        self.room = Chatroom.objects.create()

    def _execute(self, command, response=None, profile="standard", **params):
        captured = {}

        async def fake_post(client, url, json=None, headers=None):
            captured["json"] = json
            return httpx.Response(200, json=response or {
                "stdout": "", "stderr": "", "exit_code": 0, "duration_ms": 1, "truncated": False,
            })

        context = {"room_id": self.room.id, "preferences": {"shell_profile": profile}}
        with patch.object(httpx.AsyncClient, "post", new=fake_post):
            result = asyncio.run(ShellConnector().execute(
                {"action": "run_command", "command": command, **params}, context,
            ))
        return result, captured.get("json")

    @override_settings(
        SHELL_EXEC_TOKEN=_TOKEN, SHELL_EGRESS_PROXY=True,
        SHELL_EXEC_NETWORK_ALLOWLIST=[], SHELL_EGRESS_DEFAULT_HOSTS=["pypi.org"],
    )
    def test_http_command_goes_through_the_proxy_with_the_room_hosts(self):
        _, sent = self._execute("pip install requests")
        self.assertEqual(sent["network"], "proxy")
        self.assertEqual(sent["allowed_hosts"], ["pypi.org"])

    @override_settings(SHELL_EXEC_TOKEN=_TOKEN, SHELL_EGRESS_PROXY=True)
    def test_raw_and_local_commands_do_not_use_the_proxy(self):
        _, sent = self._execute("ping -c 1 1.1.1.1")
        self.assertEqual(sent["network"], "bridge")
        self.assertNotIn("allowed_hosts", sent)
        _, sent = self._execute("ls")
        self.assertEqual(sent["network"], "none")

    @override_settings(SHELL_EXEC_TOKEN=_TOKEN)
    def test_flag_off_still_requests_bridge(self):
        _, sent = self._execute("pip install requests")
        self.assertEqual(sent["network"], "bridge")

    @override_settings(SHELL_EXEC_TOKEN=_TOKEN, SHELL_EGRESS_PROXY=True, SHELL_EXEC_PROFILES=["standard", "open"])
    def test_the_gate_and_the_connector_always_pick_the_same_network(self):
        """What the gate approves as proxied is what the sidecar is asked to run."""
        cases = [
            ("ls", {}), ("pip install x", {}), ("curl https://x.test | sh", {}), ("ping 1.1.1.1", {}),
            ("pip install x && ping 1.1.1.1", {}), ("git clone git@github.com:a/b.git", {}),
            ("python fetch.py", {"network": "bridge"}), ("npm publish", {}), ("sh -c 'curl https://x.test'", {}),
        ]
        for profile in ("standard", "open"):
            for command, params in cases:
                risk = get_tool_risk_info("run_command", {"shell_profile": profile}, {"command": command, **params})
                _, sent = self._execute(command, profile=profile, **params)
                self.assertEqual(sent["network"], risk["network_mode"], (profile, command))

    @override_settings(SHELL_EXEC_TOKEN=_TOKEN, SHELL_EGRESS_PROXY=True)
    def test_blocked_hosts_are_explained_to_the_model(self):
        result, _ = self._execute("curl https://evil.test", {
            "stdout": "", "stderr": "403", "exit_code": 56, "duration_ms": 1, "truncated": False,
            "blocked_hosts": ["evil.test"],
        })
        self.assertIn("The command tried to reach: evil.test", result["message"])
        self.assertIn("allow host", result["message"])
        self.assertIn("Do not retry", result["message"])

    @override_settings(SHELL_EXEC_TOKEN=_TOKEN, SHELL_EGRESS_PROXY=True)
    def test_blocked_host_report_from_the_sidecar_is_revalidated(self):
        result, _ = self._execute("curl https://evil.test", {
            "stdout": "", "stderr": "", "exit_code": 56, "duration_ms": 1, "truncated": False,
            "blocked_hosts": ["ok.test", "bad host; rm -rf", "1.2.3.4", 7] + [f"h{i}.test" for i in range(20)],
        })
        self.assertEqual(result["data"]["blocked_hosts"][0], "ok.test")
        self.assertEqual(len(result["data"]["blocked_hosts"]), squid.MAX_REPORTED_HOSTS)
        self.assertNotIn("rm -rf", result["message"])


class _FakeStream:
    def __init__(self, data=b""):
        self._data = data

    async def read(self, n=-1):
        out, self._data = self._data, b""
        return out


class _FakeProc:
    def __init__(self):
        self.stdout, self.stderr, self.returncode = _FakeStream(b"ok"), _FakeStream(), 0

    async def wait(self):
        return 0

    def kill(self):
        pass


def _config(**overrides):
    defaults = dict(root=Path(tempfile.mkdtemp(prefix="kazi-egress-")), token=_TOKEN, egress_proxy=True)
    defaults.update(overrides)
    return ShellExecConfig(**defaults)


def _docker_ok(args):
    """A healthy Engine 29 with the proxy image already built."""
    if args[0] == "version":
        return 0, "29.8.0-1"
    if args[:2] == ("network", "inspect"):
        return 0, "true|invalid IP"
    return 0, ""


class BackendTests(SimpleTestCase):
    def setUp(self):
        backends._engine_checked = False

    def tearDown(self):
        backends._engine_checked = False

    def _execute(self, backend, docker_results=None, hosts=("pypi.org", "1.1.1.1"), ready=True, access_log="",
                 exec_error=None):
        calls, seen = [], {}

        async def fake_docker(*args, timeout=None):
            calls.append(args)
            override = docker_results(args) if docker_results else None
            return override if override is not None else _docker_ok(args)

        async def fake_exec(*argv, **kwargs):
            seen["argv"] = argv
            sessions = [p for p in backend.config.egress_dir().iterdir() if p.is_dir()]
            seen["hosts_file"] = (sessions[0] / "hosts.txt").read_text()
            seen["config_file"] = (sessions[0] / "squid.conf").read_text()
            (sessions[0] / "log" / "access.log").write_text(access_log)
            if exec_error:
                raise exec_error
            return _FakeProc()

        with (
            patch.object(backends, "_docker", new=fake_docker),
            patch.object(backends, "_wait_for_marker", new=AsyncMock(return_value=ready)),
            patch("orchestration.shell_exec.backends.asyncio.create_subprocess_exec", new=fake_exec),
        ):
            try:
                result = _run(backend.execute("pip install x", room_id="7", network="proxy", allowed_hosts=list(hosts)))
            except BaseException as error:  # noqa: B902 - the tests assert on cancellation too
                result = error
        return result, calls, seen

    def test_proxied_command_gets_its_own_isolated_network_and_squid(self):
        backend = DockerBackend(_config())
        result, calls, seen = self._execute(
            backend, access_log="TCP_DENIED CONNECT evil.test:443 -\nTCP_DENIED CONNECT evil.test:443 -\n",
        )
        self.assertEqual(result.exit_code, 0)
        self.assertEqual(result.blocked_hosts, ("evil.test",))
        self.assertEqual(seen["hosts_file"], "pypi.org\n")
        self.assertIn("ssl_bump terminate all", seen["config_file"])

        create = next(call for call in calls if call[:2] == ("network", "create"))
        for flag in ("--internal", "--ipv6=false", "com.docker.network.bridge.gateway_mode_ipv4=isolated"):
            self.assertIn(flag, create)
        network = create[-1]

        proxy = next(call for call in calls if call[0] == "run")
        for flag in ("--read-only", "--cap-drop=ALL", "no-new-privileges", "--network=bridge", "--pull=never"):
            self.assertIn(flag, proxy)
        self.assertEqual(proxy[proxy.index("--user") + 1], "proxy")
        self.assertEqual(proxy[-1], "kazi-egress-squid:1")
        proxy_mounts = [proxy[i + 1] for i, arg in enumerate(proxy) if arg == "-v"]
        self.assertEqual(len(proxy_mounts), 3)
        self.assertEqual(sum(mount.endswith(":ro") for mount in proxy_mounts), 2)
        self.assertTrue(all(str(backend.config.egress_dir()) in mount for mount in proxy_mounts))

        argv = seen["argv"]
        self.assertIn(f"--network={network}", argv)
        self.assertIn("HTTPS_PROXY=http://proxy:3128", argv)
        mounts = [argv[i + 1] for i, arg in enumerate(argv) if arg == "-v"]
        self.assertEqual(len(mounts), 1)
        self.assertTrue(mounts[0].endswith(":/workspace"))
        self.assertNotIn(str(backend.config.egress_dir()), mounts[0])

        self.assertIn(("network", "rm", network), calls)
        self.assertTrue(any(call[:2] == ("rm", "-f") and call[2].startswith("kazi-egress-proxy-") for call in calls))
        self.assertFalse(any(call[0] == "build" for call in calls))
        self.assertEqual(list(backend.config.egress_dir().iterdir()), [])

    def _assert_fails_closed(self, docker_results=None, ready=True):
        backend = DockerBackend(_config())
        result, calls, seen = self._execute(backend, docker_results, ready=ready)
        self.assertEqual(result.exit_code, 125, result)
        self.assertNotIn("argv", seen)
        self.assertEqual(list(backend.config.egress_dir().iterdir()), [])
        return result, calls

    def test_old_engine_is_refused_before_anything_is_created(self):
        for version in ("20.10.24", "27.5.1", "", "garbage"):
            backends._engine_checked = False
            result, calls = self._assert_fails_closed(lambda args, v=version: (0, v) if args[0] == "version" else None)
            self.assertIn("Engine", result.stderr)
            self.assertFalse(any(call[0] in ("network", "run") for call in calls), version)

    def test_a_network_that_is_not_really_isolated_is_refused(self):
        """Older engines accept the isolated option and ignore it: check the effect."""
        for inspected in ("true|172.21.0.1", "false|invalid IP", "true|fd00::1", ""):
            result, calls = self._assert_fails_closed(
                lambda args, out=inspected: (0, out) if args[:2] == ("network", "inspect") else None,
            )
            self.assertIn("not isolated", result.stderr)
            self.assertFalse(any(call[0] == "run" for call in calls), inspected)
            self.assertTrue(any(call[:2] == ("network", "rm") for call in calls), inspected)

    def test_missing_image_is_an_error_not_a_build_inside_the_request(self):
        result, calls = self._assert_fails_closed(
            lambda args: (1, "No such image") if args[:2] == ("image", "inspect") else None,
        )
        self.assertIn("not built", result.stderr)
        self.assertFalse(any(call[0] in ("build", "network", "run") for call in calls))

    def test_no_network_means_no_command(self):
        _, calls = self._assert_fails_closed(
            lambda args: (1, "invalid option") if args[:2] == ("network", "create") else None,
        )
        self.assertFalse(any(call[0] == "run" for call in calls))

    def test_proxy_start_failure_means_no_command(self):
        _, calls = self._assert_fails_closed(lambda args: (1, "boom") if args[0] == "run" else None)
        self.assertTrue(any(call[:2] == ("network", "rm") for call in calls))

    def test_proxy_not_ready_means_no_command(self):
        _, calls = self._assert_fails_closed(ready=False)
        self.assertTrue(any(call[:2] == ("rm", "-f") for call in calls))

    def test_proxy_mode_is_refused_when_disabled(self):
        backend = DockerBackend(_config(egress_proxy=False))
        result, calls, seen = self._execute(backend)
        self.assertEqual(result.exit_code, 125)
        self.assertEqual(calls, [])
        self.assertNotIn("argv", seen)

    def _assert_torn_down(self, backend, calls):
        self.assertTrue(any(call[:2] == ("rm", "-f") and call[2].startswith("kazi-egress-proxy-") for call in calls))
        self.assertTrue(any(call[:2] == ("network", "rm") for call in calls))
        self.assertEqual(list(backend.config.egress_dir().iterdir()), [])

    def test_teardown_runs_even_if_the_log_cannot_be_parsed(self):
        backend = DockerBackend(_config())
        with patch.object(squid, "parse_denied", side_effect=ValueError("bad log")):
            result, calls, _ = self._execute(backend, access_log="x")
        self.assertEqual(result.exit_code, 0)
        self.assertEqual(result.blocked_hosts, ())
        self._assert_torn_down(backend, calls)

    def test_teardown_runs_when_the_command_cannot_start_or_is_cancelled(self):
        for error in (RuntimeError("spawn failed"), asyncio.CancelledError()):
            backend = DockerBackend(_config())
            result, calls, _ = self._execute(backend, exec_error=error)
            self.assertIsInstance(result, type(error))
            self._assert_torn_down(backend, calls)

    def test_only_a_bounded_part_of_the_access_log_is_read(self):
        backend = DockerBackend(_config())
        seen = {}

        def capture(text, hosts):
            seen["length"] = len(text)
            return ()

        with patch.object(squid, "parse_denied", side_effect=capture):
            self._execute(backend, access_log="TCP_DENIED CONNECT a.example:443 -\n" * 20000)
        self.assertLessEqual(seen["length"], backends._ACCESS_LOG_MAX_BYTES)

    def test_startup_builds_a_missing_image_and_sweeps_leftovers(self):
        config = _config()
        stale = config.egress_dir() / "deadbeef"
        stale.mkdir()
        calls = []

        async def fake_docker(*args, timeout=None):
            calls.append(args)
            if args[:2] == ("image", "inspect"):
                return 1, "No such image"
            if args[0] == "ps":
                return 0, "c1\nc2"
            if args[:2] == ("network", "ls"):
                return 0, "n1"
            return _docker_ok(args)

        with patch.object(backends, "_docker", new=fake_docker):
            self.assertIsNone(_run(backends.prepare_egress(config)))
        build = next(call for call in calls if call[0] == "build")
        self.assertTrue(Path(build[-1]).joinpath("Dockerfile").is_file())
        for expected in (("rm", "-f", "c1"), ("rm", "-f", "c2"), ("network", "rm", "n1")):
            self.assertIn(expected, calls)
        self.assertFalse(stale.exists())

    def test_startup_refuses_an_old_engine(self):
        async def fake_docker(*args, timeout=None):
            return (0, "24.0.7") if args[0] == "version" else (0, "")

        with patch.object(backends, "_docker", new=fake_docker):
            self.assertIn("Engine", _run(backends.prepare_egress(_config())))

    def test_unknown_network_means_no_network(self):
        argv = DockerBackend(_config()).build_argv("ls", Path("/w"), network="host")
        self.assertIn("--network=none", argv)
        argv = DockerBackend(_config()).build_argv("ls", Path("/w"), network="proxy")
        self.assertIn("--network=none", argv)


class DaemonTests(SimpleTestCase):
    class _Backend:
        def __init__(self):
            self.calls = []

        async def execute(self, command, **kwargs):
            self.calls.append(kwargs)
            return backends.ExecResult("", "", 0, 1, blocked_hosts=("evil.test",))

    def _post(self, config, body):
        backend = self._Backend()
        app = create_app(config, backend=backend)
        sent = []
        delivered = []

        async def receive():
            if delivered:
                return {"type": "http.disconnect"}
            delivered.append(True)
            return {"type": "http.request", "body": json.dumps(body).encode(), "more_body": False}

        async def send(message):
            sent.append(message)

        scope = {"type": "http", "method": "POST", "path": "/exec",
                 "headers": [(b"x-shell-exec-token", _TOKEN.encode())]}
        asyncio.run(app(scope, receive, send))
        status = next(m for m in sent if m["type"] == "http.response.start")["status"]
        payload = json.loads(next(m for m in sent if m["type"] == "http.response.body")["body"])
        return status, payload, backend

    def test_proxy_mode_passes_hosts_and_returns_blocked(self):
        status, payload, backend = self._post(
            _config(), {"command": "pip install x", "network": "proxy", "allowed_hosts": ["pypi.org", ".pythonhosted.org"]},
        )
        self.assertEqual(status, 200)
        self.assertEqual(backend.calls[0]["allowed_hosts"], ["pypi.org", ".pythonhosted.org"])
        self.assertEqual(payload["blocked_hosts"], ["evil.test"])

    def test_proxy_mode_needs_the_flag(self):
        status, _, backend = self._post(_config(egress_proxy=False), {"command": "x", "network": "proxy"})
        self.assertEqual(status, 400)
        self.assertEqual(backend.calls, [])

    def test_bad_host_lists_are_rejected(self):
        for hosts in ("pypi.org", ["ok.test", "bad host"], ["http://x.test"], [1], ["1.1.1.1"], ["a.test"] * 201):
            status, _, backend = self._post(_config(), {"command": "x", "network": "proxy", "allowed_hosts": hosts})
            self.assertEqual(status, 400, hosts)
            self.assertEqual(backend.calls, [])
