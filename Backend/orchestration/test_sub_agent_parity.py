"""A delegated run follows the main loop's rules: receipts, persona scope, caps."""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

from asgiref.sync import async_to_sync
from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import SimpleTestCase, TestCase

from orchestration import agent_loop
from orchestration.agent_loop import _execute_scoped_tool_calls, _run_sub_agent
from orchestration.models import ActionReceipt

OPEN_PROFILE = {"shell_profile": "open"}
WHO = {"user_id": 1, "room_id": 2}


def _call(name, tool_input, call_id="t1"):
    return {"id": call_id, "name": name, "input": tool_input}


def _tool_use_response(count, name="get_weather"):
    return {
        "content": [
            {"type": "tool_use", "id": f"t{i}", "name": name, "input": {"city": "Nairobi"}}
            for i in range(count)
        ],
        "stop_reason": "tool_use",
        "usage": {"input_tokens": 10, "output_tokens": 5},
    }


def _end_turn():
    return {"content": [{"type": "text", "text": "done"}], "stop_reason": "end_turn", "usage": {}}


class _Harness:
    """Patches the executor, memory and receipt writer around a delegated run."""

    def __enter__(self):
        self._patches = [
            patch("orchestration.agent_loop._execute_with_timeout", new=AsyncMock(return_value={"status": "success"})),
            patch("orchestration.agent_loop.update_memory_state", new=AsyncMock()),
            patch("orchestration.agent_loop._record_receipt", new=AsyncMock()),
            patch("orchestration.agent_loop.record_event"),
        ]
        self.executed, _, self.receipt, _ = [p.start() for p in self._patches]
        return self

    def __exit__(self, *exc):
        for p in self._patches:
            p.stop()


class DelegatedReceiptTests(SimpleTestCase):
    def test_an_executed_delegated_call_writes_a_receipt(self):
        with _Harness() as h:
            async_to_sync(_execute_scoped_tool_calls)(
                [_call("get_weather", {"city": "Nairobi"})], WHO, None, [],
            )
        h.executed.assert_awaited_once()
        h.receipt.assert_awaited_once()
        self.assertEqual(h.receipt.await_args.args[0], "get_weather")

    def test_the_receipt_says_which_rule_let_a_command_run(self):
        # The basis is judged before the command taints the run, as in the main loop.
        with _Harness() as h:
            async_to_sync(_execute_scoped_tool_calls)(
                [_call("run_command", {"command": "ls"})], WHO, OPEN_PROFILE, [],
            )
        h.executed.assert_awaited_once()
        self.assertEqual(h.receipt.await_args.kwargs["basis"], "open_profile")

    def test_a_blocked_delegated_call_writes_no_receipt(self):
        prefs = {"shell_profile": "open", "_shell_tainted": True}
        with _Harness() as h:
            async_to_sync(_execute_scoped_tool_calls)(
                [_call("run_command", {"command": "ls"})], WHO, prefs, [],
            )
        h.executed.assert_not_called()
        h.receipt.assert_not_called()

    def test_a_receipt_that_cannot_be_written_is_logged_and_the_result_is_unchanged(self):
        store_down = AsyncMock(side_effect=RuntimeError("store down"))
        with patch("orchestration.agent_loop._execute_with_timeout", new=AsyncMock(return_value={"status": "success"})), \
                patch("orchestration.agent_loop.update_memory_state", new=AsyncMock()), \
                patch("orchestration.action_receipts.record_action_receipt", new=store_down), \
                self.assertLogs("orchestration.agent_loop", level="WARNING") as logs:
            blocks, _ = async_to_sync(_execute_scoped_tool_calls)(
                [_call("run_command", {"command": "ls"})], WHO, OPEN_PROFILE, [],
            )
        store_down.assert_awaited_once()
        self.assertIn("Action receipt not recorded for run_command", logs.output[0])
        self.assertIn("success", blocks[0]["content"])


class DelegatedReceiptRowTests(TestCase):
    def setUp(self):
        cache.clear()
        self.addCleanup(cache.clear)
        self.user = get_user_model().objects.create_user(
            username="delegated-owner", email="example@example.com",
            password="fake-token",  # nosec B106 — test fixture — fake credential
        )

    def test_a_delegated_command_leaves_a_receipt_row_with_its_basis(self):
        with patch("orchestration.agent_loop._execute_with_timeout", new=AsyncMock(return_value={"status": "success"})), \
                patch("orchestration.agent_loop.update_memory_state", new=AsyncMock()):
            async_to_sync(_execute_scoped_tool_calls)(
                [_call("run_command", {"command": "ls"})],
                {"user_id": self.user.id, "room_id": None}, OPEN_PROFILE, [],
            )

        receipt = ActionReceipt.objects.get(user_id=self.user.id)
        self.assertEqual(receipt.action, "run_command")
        self.assertEqual(receipt.status, "success")
        self.assertEqual(receipt.result.get("approval_basis"), "open_profile")


class DelegatedPersonaTests(SimpleTestCase):
    # get_weather runs with no confirmation when no persona applies, so only
    # the persona can be what stops it here.
    def _email_only_persona(self):
        return MagicMock(tool_scope=["send_email"], approval_boundary=[], risk_ceiling="high")

    def test_a_call_outside_the_persona_scope_is_refused_with_the_reason(self):
        with _Harness() as h:
            blocks, _ = async_to_sync(_execute_scoped_tool_calls)(
                [_call("get_weather", {"city": "Nairobi"})], WHO, None, [],
                persona=self._email_only_persona(),
            )
        h.executed.assert_not_called()
        h.receipt.assert_not_called()
        self.assertIn("persona", blocks[0]["content"])
        self.assertNotIn("requires confirmation", blocks[0]["content"])

    def _delegate_weather(self, persona):
        llm = MagicMock()
        llm.create_message = AsyncMock(side_effect=[_tool_use_response(1), _end_turn()])
        with _Harness() as h, \
                patch("orchestration.agent_loop.get_llm_client", return_value=llm), \
                patch("orchestration.agent_loop._resolve_persona", new=AsyncMock(return_value=persona)) as resolve:
            async_to_sync(_run_sub_agent)({"task": "check the weather"}, dict(WHO), None, "system", [])
        return h, resolve

    def test_a_delegated_run_resolves_and_applies_the_room_persona(self):
        h, resolve = self._delegate_weather(self._email_only_persona())

        resolve.assert_awaited_once_with(2, 1)
        h.executed.assert_not_called()

    def test_the_same_delegated_run_executes_when_no_persona_applies(self):
        h, _ = self._delegate_weather(None)

        h.executed.assert_awaited_once()


class DelegatedCapTests(SimpleTestCase):
    def _run(self, tool_input, context):
        llm = MagicMock()
        llm.create_message = AsyncMock(return_value=_tool_use_response(12))
        with _Harness() as h, \
                patch("orchestration.agent_loop.get_llm_client", return_value=llm), \
                patch("orchestration.agent_loop._resolve_persona", new=AsyncMock(return_value=None)):
            result = async_to_sync(_run_sub_agent)(tool_input, context, None, "system", [])
        return result, h

    def test_the_model_cannot_raise_its_own_tool_call_cap(self):
        result, h = self._run({"task": "loop", "max_tool_calls": 1000}, dict(WHO))
        self.assertEqual(result["tool_calls"], agent_loop.SUB_AGENT_MAX_TOOL_CALLS)
        self.assertEqual(result["stopped_reason"], "max_tool_calls")
        self.assertEqual(h.executed.await_count, agent_loop.SUB_AGENT_MAX_TOOL_CALLS)

    def test_a_cap_granted_by_the_harness_is_honoured(self):
        result, h = self._run({"task": "loop"}, {**WHO, "sub_agent_tool_call_cap": 3})
        self.assertEqual(result["tool_calls"], 3)
        self.assertEqual(h.executed.await_count, 3)

    def test_a_granted_cap_may_be_larger_than_the_default(self):
        larger = agent_loop.SUB_AGENT_MAX_TOOL_CALLS + 12
        result, h = self._run({"task": "loop"}, {**WHO, "sub_agent_tool_call_cap": larger})
        self.assertEqual(h.executed.await_count, larger)

    def test_a_cap_that_makes_no_sense_falls_back_to_the_default(self):
        for nonsense in (-5, 0, "abc", None, [4]):
            with self.subTest(cap=nonsense):
                result, h = self._run({"task": "loop"}, {**WHO, "sub_agent_tool_call_cap": nonsense})
                self.assertEqual(h.executed.await_count, agent_loop.SUB_AGENT_MAX_TOOL_CALLS)
