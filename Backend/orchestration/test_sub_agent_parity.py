"""A delegated run follows the main loop's rules: receipts, persona scope, caps."""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

from asgiref.sync import async_to_sync
from django.test import SimpleTestCase

from orchestration import agent_loop
from orchestration.agent_loop import _execute_scoped_tool_calls, _run_sub_agent


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
                [_call("get_weather", {"city": "Nairobi"})], {"user_id": 1, "room_id": 2}, None, [],
            )
        h.executed.assert_awaited_once()
        h.receipt.assert_awaited_once()
        self.assertEqual(h.receipt.await_args.args[0], "get_weather")

    def test_a_blocked_delegated_call_writes_no_receipt(self):
        prefs = {"shell_profile": "open", "_shell_tainted": True}
        with _Harness() as h:
            async_to_sync(_execute_scoped_tool_calls)(
                [_call("run_command", {"command": "ls"})], {"user_id": 1, "room_id": 2}, prefs, [],
            )
        h.executed.assert_not_called()
        h.receipt.assert_not_called()


class DelegatedPersonaTests(SimpleTestCase):
    def _persona(self):
        return MagicMock(tool_scope=["get_weather"], approval_boundary=[], risk_ceiling="high")

    def test_a_call_outside_the_persona_scope_is_refused_with_the_reason(self):
        with _Harness() as h:
            blocks, _ = async_to_sync(_execute_scoped_tool_calls)(
                [_call("send_email", {"to": "a@example.com"})], {}, None, [], persona=self._persona(),
            )
        h.executed.assert_not_called()
        self.assertIn("persona", blocks[0]["content"])

    def test_a_delegated_run_resolves_and_applies_the_room_persona(self):
        llm = MagicMock()
        llm.create_message = AsyncMock(side_effect=[
            _tool_use_response(1, name="send_email"),
            {"content": [{"type": "text", "text": "done"}], "stop_reason": "end_turn", "usage": {}},
        ])
        with _Harness() as h, \
                patch("orchestration.agent_loop.get_llm_client", return_value=llm), \
                patch("orchestration.agent_loop._resolve_persona", new=AsyncMock(return_value=self._persona())) as resolve:
            async_to_sync(_run_sub_agent)(
                {"task": "email the report"}, {"user_id": 1, "room_id": 2}, None, "system", [],
            )
        resolve.assert_awaited_once_with(2, 1)
        h.executed.assert_not_called()


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
        result, h = self._run({"task": "loop", "max_tool_calls": 1000}, {"user_id": 1, "room_id": 2})
        self.assertEqual(result["tool_calls"], agent_loop.SUB_AGENT_MAX_TOOL_CALLS)
        self.assertEqual(result["stopped_reason"], "max_tool_calls")
        self.assertEqual(h.executed.await_count, agent_loop.SUB_AGENT_MAX_TOOL_CALLS)

    def test_a_cap_granted_by_the_harness_is_honoured(self):
        result, h = self._run({"task": "loop"}, {"user_id": 1, "room_id": 2, "sub_agent_tool_call_cap": 3})
        self.assertEqual(result["tool_calls"], 3)
        self.assertEqual(h.executed.await_count, 3)
