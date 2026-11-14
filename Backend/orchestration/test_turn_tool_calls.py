"""Drive the real coordinator and loop, and assert the Turn's typed record.

Only the model is faked (``create_message`` from the turn-runner tests); the
coordinator, the loop, the row writer, the transcript builder and the history
read are the production code.
"""
from __future__ import annotations

from contextlib import ExitStack
from unittest.mock import AsyncMock, MagicMock, patch

from asgiref.sync import async_to_sync
from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TestCase
from django.utils import timezone

from chatbot.history import get_history_rows
from chatbot.kazi_messages import ORIGIN_TURN, persist_kazi_message
from chatbot.models import Chatroom, Member, Message
from chatbot.test_turn_runner import _FakeLLM, _response, _text, _tool
from chatbot.transcript import build_history_messages
from orchestration import turn_leases
from orchestration.coordinator import OrchestrationCoordinator
from orchestration.models import Turn, TurnToolCall
from orchestration.personas import PERSONA_LOOKUP_FAILED
from orchestration.shell.profiles import PROFILES
from orchestration.turns import CLAIMED, ClaimResult

_UNSET = object()


class TurnToolCallCoordinatorTests(TestCase):
    def setUp(self):
        cache.clear()
        self.addCleanup(cache.clear)
        turn_leases._LIVE_TURNS.clear()
        turn_leases._FENCED_TURNS.clear()
        turn_leases._LAST_RENEWAL.clear()
        self.addCleanup(turn_leases._LIVE_TURNS.clear)
        self.addCleanup(turn_leases._FENCED_TURNS.clear)
        self.addCleanup(turn_leases._LAST_RENEWAL.clear)
        from orchestration import agent_loop

        agent_loop._circuit_state.clear()
        self.addCleanup(agent_loop._circuit_state.clear)

        User = get_user_model()
        self.user = User.objects.create_user(username="tool-turn", password="pw")  # nosec B106 — fake test credential
        self.member = Member.objects.select_related("User").get(User=self.user)
        self.room = Chatroom.objects.get(participants=self.member)
        self.request = Message.objects.create(
            member=self.member, content="do it", timestamp=timezone.now(),
        )
        self.room.chats.add(self.request)
        self.turn = Turn.objects.create(
            room=self.room, user=self.user, state="running",
        )
        self.llm = _FakeLLM()

    def _run(self, query, events, *, claimed=_UNSET, persona=None, execute=None):
        self.llm.queue(*events)
        turn_leases.register_live_turn(str(self.turn.id))
        result = {}
        targets = {
            "orchestration.agent_loop.get_llm_client": MagicMock(return_value=self.llm),
            "orchestration.agent_loop._resolve_persona": AsyncMock(
                return_value=persona,
            ),
            "orchestration.agent_loop.enforce_agent_caps": MagicMock(return_value=True),
            "orchestration.agent_loop._search_backend_available": MagicMock(return_value=False),
            "orchestration.agent_loop.execute_tool": AsyncMock(
                return_value=execute or {"status": "success", "data": {"ok": True}},
            ),
            "orchestration.agent_loop.preview_tool": AsyncMock(return_value=None),
            "orchestration.agent_loop._record_receipt": AsyncMock(return_value=None),
            "orchestration.agent_loop.record_event": MagicMock(),
            "orchestration.shell.environment.get_shell_environment": AsyncMock(return_value={}),
            "orchestration.shell.profiles.resolve_profile": MagicMock(
                return_value=PROFILES["open"],
            ),
            "orchestration.coordinator.aclaim_turn": AsyncMock(
                return_value=ClaimResult(CLAIMED, claimed or self.turn),
            ),
            "orchestration.coordinator.get_user_preferences": MagicMock(return_value={}),
            "orchestration.coordinator.has_pending_agent_state": self._has_pending,
            "orchestration.coordinator.has_pending_budget_state": AsyncMock(return_value=False),
            "orchestration.coordinator.pending_resume_kind": AsyncMock(return_value=""),
            "orchestration.coordinator.has_expired_pause": AsyncMock(return_value=False),
            "orchestration.coordinator.record_event": MagicMock(),
            "orchestration.coordinator.load_memory_summary": AsyncMock(return_value=None),
            "orchestration.coordinator._consent_postdates_prompt": AsyncMock(return_value=True),
            "orchestration.coordinator._consent_postdates_pause": AsyncMock(return_value=True),
            "orchestration.coordinator.last_ran_turn_before": MagicMock(return_value=None),
            "orchestration.turn_leases.ensure_heartbeat": MagicMock(return_value=None),
            "orchestration.turn_leases.ensure_cancel_poll": MagicMock(return_value=None),
        }

        async def _drive():
            result["value"] = await OrchestrationCoordinator().handle_message(
                query=query,
                user_id=self.user.id,
                room_id=str(self.room.id),
                username=self.user.username,
                message_id=self.request.id,
                send_chunk=AsyncMock(),
                send_step_event=AsyncMock(),
                get_context_prompt=AsyncMock(return_value=""),
                bump_signals=MagicMock(),
                history_messages=None,
                claimed_turn=self.turn if claimed is _UNSET else claimed,
            )
            return result["value"]

        with ExitStack() as stack:
            for target, new in targets.items():
                stack.enter_context(patch(target, new))
            return async_to_sync(_drive)()

    @property
    def _has_pending(self):
        from orchestration.agent_loop import has_pending_agent_state

        return has_pending_agent_state

    def _save_reply(self, result, text="Done."):
        return persist_kazi_message(
            self.room,
            content=text,
            origin=ORIGIN_TURN,
            turn=self.turn,
            model_text=text,
            tools=None if result.tool_calls_recorded else result.tools,
            by=self.user.id,
            broadcast=False,
        )

    def _replayed_results(self):
        rows = get_history_rows(self.room, limit=8)
        messages = build_history_messages(rows, viewer_user_id=self.user.id)
        return [
            block for message in messages
            if isinstance(message.get("content"), list)
            for block in message["content"]
            if block.get("type") == "tool_result"
        ]

    def test_a_declined_pause_replays_three_not_executed_results(self):
        calls = [
            _tool("c1", "send_message", {"to": "a", "message": "one"}),
            _tool("c2", "send_email", {"to": "a@example.com", "subject": "two", "body": "b"}),
            _tool("c3", "create_payment_link", {"amount": 3}),
        ]
        first = self._run("@kazi run them", [_response(calls, stop_reason="tool_use")])
        self.assertEqual(first.turn_state, "paused")

        declined = self._run("no", [], claimed=None)
        self.assertEqual(declined.turn_state, "cancelled")
        self._save_reply(declined, "Okay, cancelled.")

        rows = list(TurnToolCall.objects.filter(turn=self.turn).order_by("seq"))
        self.assertEqual([row.status for row in rows], ["declined"] * 3)
        results = self._replayed_results()
        self.assertEqual(len(results), 3)
        for index, result in enumerate(results, start=1):
            self.assertEqual(result["tool_use_id"], f"c{index}")
            self.assertIn("not executed", result["content"])
            self.assertIn("declined", result["content"])
            self.assertTrue(result["is_error"])

    def test_a_safe_call_before_a_cancelled_pause_is_in_last_actions_once(self):
        first = self._run("@kazi check and run", [
            _response([
                _tool("a1", "get_weather", {"city": "Nairobi"}),
                _tool("a2", "send_message", {"to": "a", "message": "hello"}),
            ], stop_reason="tool_use"),
        ])
        self.assertEqual(first.turn_state, "paused")

        self._run("no", [], claimed=None)

        from orchestration.memory_state import load_memory_state

        state = async_to_sync(load_memory_state)({
            "user_id": self.user.id, "room_id": self.room.id,
        })
        actions = [entry["action"] for entry in (state or {}).get("last_actions") or []]
        self.assertEqual(actions.count("get_weather"), 1)
        self.assertNotIn("send_message", actions)

    def test_a_denied_call_is_recorded_and_replayed(self):
        result = self._run("@kazi try it", [
            _response(
                [_tool("d1", "run_command", {"command": "rm x"})],
                stop_reason="tool_use",
            ),
            _response([_text("I could not run it.")]),
        ], persona=PERSONA_LOOKUP_FAILED)
        self._save_reply(result)

        row = TurnToolCall.objects.get(turn=self.turn, call_id="d1")
        self.assertEqual(row.status, "denied")
        self.assertTrue(row.untrusted)
        results = self._replayed_results()
        self.assertEqual(results[0]["tool_use_id"], "d1")
        self.assertIn("denied", results[0]["content"])
        self.assertTrue(results[0]["is_error"])

    def test_a_retrieval_call_is_marked_for_phase_four(self):
        result = self._run("@kazi search", [
            _response(
                [_tool("s1", "web_search", {"query": "nairobi"})],
                stop_reason="tool_use",
            ),
            _response([_text("Found it.")]),
        ])
        self._save_reply(result)

        row = TurnToolCall.objects.get(turn=self.turn, call_id="s1")
        self.assertTrue(row.retrieval)
        self.assertTrue(row.untrusted)

    def test_a_delegated_call_is_recorded_with_provenance(self):
        result = self._run("@kazi delegate", [
            _response(
                [_tool("p1", "delegate_task", {"task": "search"})],
                stop_reason="tool_use",
            ),
            _response(
                [_tool("s2", "web_search", {"query": "nairobi"})],
                stop_reason="tool_use",
            ),
            _response([_text("Found it.")]),
            _response([_text("The sub-agent found it.")]),
        ])
        self._save_reply(result)

        child = TurnToolCall.objects.get(turn=self.turn, call_id="s2")
        parent = TurnToolCall.objects.get(turn=self.turn, call_id="p1")
        self.assertEqual(child.provenance, "delegated")
        self.assertEqual(child.parent_call_id, "p1")
        self.assertEqual(parent.provenance, "main")

    def test_the_provider_search_count_is_stored_on_the_turn(self):
        server_search = {
            "content": [_text("Found it.")],
            "stop_reason": "end_turn",
            "usage": {
                "input_tokens": 10,
                "output_tokens": 5,
                "server_tool_use": {"web_search_requests": 2},
            },
        }
        result = self._run("@kazi search", [server_search])
        self._save_reply(result)

        self.turn.refresh_from_db()
        self.assertEqual(self.turn.server_tool_uses, 2)
        row = TurnToolCall.objects.get(turn=self.turn, name="web_search")
        self.assertTrue(row.retrieval)

    def test_a_recorded_turn_does_not_write_the_legacy_payload(self):
        result = self._run("@kazi note", [
            _response(
                [_tool("n1", "get_weather", {"city": "Nairobi"})],
                stop_reason="tool_use",
            ),
            _response([_text("Sunny.")]),
        ])
        message = self._save_reply(result)

        self.assertTrue(result.tool_calls_recorded)
        self.assertEqual(TurnToolCall.objects.filter(turn=self.turn).count(), 1)
        from chatbot.history import decrypt_stored

        _content, payload = decrypt_stored(self.room, message)
        self.assertNotIn("tools", payload)
