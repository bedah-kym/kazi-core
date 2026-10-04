"""The model is told where its commands run, from facts and not from a guess."""
from __future__ import annotations

from contextlib import ExitStack
from unittest.mock import AsyncMock, MagicMock, patch

from asgiref.sync import async_to_sync
from django.core.cache import cache
from django.test import SimpleTestCase, TestCase

from orchestration.agent_loop import AgentEvent
from orchestration.agent_prompts import build_environment_block, build_system_prompt
from orchestration.coordinator import OrchestrationCoordinator
from orchestration.shell.environment import get_shell_environment
from orchestration.shell.profiles import get_profile
from orchestration.shell_exec.backends import DockerBackend, LocalBackend, ShellExecConfig

WINDOWS_OPEN = {
    "profile": "open", "sandboxed": False, "platform": "Windows", "shell": "cmd.exe",
    "network": "full", "writable": "full",
}


class EnvironmentBlockTests(SimpleTestCase):
    def test_unsandboxed_windows_host(self):
        block = build_environment_block(WINDOWS_OPEN)

        self.assertIn("Windows", block)
        self.assertIn("cmd.exe", block)
        self.assertIn("not sandboxed", block)
        self.assertIn("autopilot", block)

    def test_standard_profile_is_a_container_that_asks_for_network(self):
        block = build_environment_block({
            "profile": "standard", "sandboxed": True, "platform": "Linux", "shell": "sh",
            "network": "none", "writable": "workspace",
        })

        self.assertIn("fresh container", block)
        self.assertIn("asks the user first", block)
        self.assertIn("allow host", block)
        self.assertNotIn("autopilot", block)

    def test_locked_profile_has_no_network_and_a_read_only_workspace(self):
        block = build_environment_block({
            "profile": "locked", "sandboxed": True, "platform": "Linux", "shell": "sh",
            "network": "none", "writable": "none",
        })

        self.assertIn("no network", block)
        self.assertIn("read-only", block)
        self.assertNotIn("asks the user first", block)

    def test_unreachable_shell_says_so(self):
        block = build_environment_block(None)

        self.assertIn("not reachable", block)
        self.assertNotIn("Windows", block)


class SystemPromptTests(SimpleTestCase):
    def test_prompt_describes_this_install_and_drops_the_narration_rules(self):
        prompt = build_system_prompt(preferences={}, context_prompt=build_environment_block(WINDOWS_OPEN))

        self.assertIn("running inside the user's own Kazi install", prompt)
        self.assertIn("Where you are running", prompt)
        self.assertNotIn("manage communication, payments, travel", prompt)
        self.assertNotIn("explain briefly what you are about to do", prompt)
        self.assertNotIn("progress updates between steps", prompt)


class BackendEnvironmentTests(SimpleTestCase):
    def _config(self):
        return ShellExecConfig(root=MagicMock())

    def test_local_backend_reports_the_shell_it_spawns(self):
        with patch("orchestration.shell_exec.backends.sys.platform", "win32"):
            self.assertEqual(
                LocalBackend(self._config()).environment(), {"platform": "Windows", "shell": "cmd.exe"},
            )
        with patch("orchestration.shell_exec.backends.sys.platform", "linux"):
            self.assertEqual(LocalBackend(self._config()).environment()["shell"], "sh")

    def test_docker_backend_reports_a_linux_container(self):
        facts = DockerBackend(self._config()).environment()

        self.assertEqual(facts["shell"], "sh")
        self.assertIn("Linux", facts["platform"])


class ShellEnvironmentTests(TestCase):
    def setUp(self):
        cache.clear()
        self.addCleanup(cache.clear)

    def test_sandboxed_profile_needs_no_sidecar_call(self):
        with patch("orchestration.shell.environment._sidecar_host", new=AsyncMock()) as sidecar:
            facts = async_to_sync(get_shell_environment)(get_profile("standard"))

        sidecar.assert_not_called()
        self.assertTrue(facts["sandboxed"])
        self.assertEqual(facts["platform"], "Linux")

    def test_open_profile_uses_what_the_sidecar_reports(self):
        host = {"platform": "Windows", "shell": "cmd.exe"}
        with patch("orchestration.shell.environment._sidecar_host", new=AsyncMock(return_value=host)):
            facts = async_to_sync(get_shell_environment)(get_profile("open"))

        self.assertFalse(facts["sandboxed"])
        self.assertEqual(facts["platform"], "Windows")
        self.assertEqual(facts["shell"], "cmd.exe")

    def test_unreachable_sidecar_gives_none_and_is_not_retried_at_once(self):
        client = MagicMock()
        client.__aenter__ = AsyncMock(return_value=client)
        client.__aexit__ = AsyncMock(return_value=False)
        client.get = AsyncMock(side_effect=OSError("connection refused"))
        with patch("orchestration.shell.environment.httpx.AsyncClient", return_value=client):
            first = async_to_sync(get_shell_environment)(get_profile("open"))
            second = async_to_sync(get_shell_environment)(get_profile("open"))

        self.assertIsNone(first)
        self.assertIsNone(second)
        self.assertEqual(client.get.await_count, 1)


class CoordinatorEnvironmentTests(SimpleTestCase):
    def test_the_agent_loop_is_told_where_commands_run(self):
        captured = {}

        async def _capture(**kwargs):
            captured.update(kwargs)
            yield AgentEvent("text", {"text": "ok"})

        cache_mock = MagicMock()
        cache_mock.get.return_value = None
        targets = {
            "orchestration.coordinator.load_memory_summary": AsyncMock(return_value=None),
            "orchestration.coordinator.get_user_preferences": lambda user_id: {},
            "orchestration.coordinator.get_conversation_mode": AsyncMock(return_value="auto"),
            "orchestration.coordinator.has_pending_agent_state": AsyncMock(return_value=False),
            "orchestration.coordinator.load_task_state": AsyncMock(return_value=None),
            "orchestration.coordinator.cache": cache_mock,
            "orchestration.coordinator.record_event": MagicMock(),
            "orchestration.coordinator.run_agent_loop": _capture,
            "orchestration.shell.environment.get_shell_environment": AsyncMock(return_value=WINDOWS_OPEN),
        }

        async def run():
            return await OrchestrationCoordinator().handle_message(
                query="list the files",
                user_id=1,
                room_id="1",
                username="alice",
                message_id=42,
                history_text="",
                send_chunk=AsyncMock(),
                send_step_event=AsyncMock(),
                get_context_prompt=AsyncMock(return_value="ROOM CONTEXT:\nnotes here"),
                bump_signals=MagicMock(),
            )

        with ExitStack() as stack:
            for target, new in targets.items():
                stack.enter_context(patch(target, new))
            async_to_sync(run)()

        self.assertIn("Where you are running", captured["context_prompt"])
        self.assertIn("Windows through cmd.exe", captured["context_prompt"])
        self.assertIn("notes here", captured["context_prompt"])
