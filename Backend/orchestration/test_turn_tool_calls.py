"""Drive the real coordinator and loop, and assert the Turn's typed record.

Only the model is faked (``create_message`` from the turn-runner tests); the
coordinator, the loop, the row writer, the transcript builder and the history
read are the production code.
"""
from __future__ import annotations

import json
from contextlib import ExitStack
from datetime import timedelta
from unittest.mock import AsyncMock, MagicMock, patch

from asgiref.sync import async_to_sync, sync_to_async
from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TestCase
from django.utils import timezone

from chatbot.history import get_history_rows
from chatbot.models import Chatroom, Member, Message
from chatbot.test_turn_runner import _FakeLLM, _response, _text, _tool
from chatbot.transcript import build_history_messages
from chatbot.turn_runner import save_turn_reply
from orchestration import turn_leases
from orchestration import turns as turns_mod
from orchestration.agent_loop import REQUEST_MORE_BUDGET_TOOL
from orchestration.coordinator import OrchestrationCoordinator
from orchestration.models import ActionReceipt, Turn, TurnToolCall
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

    def _run(
        self, query, events, *, claimed=_UNSET, claim_turn=_UNSET,
        persona=None, execute=None, expired_pause=False, patch_receipt=True,
        before_call=None, send_chunk=None, send_step_event=None,
        message_id=None,
    ):
        self.llm.queue(*events)
        self.llm.before_call = before_call
        run_turn = self.turn if claim_turn is _UNSET else claim_turn
        turn_leases.register_live_turn(str(run_turn.id))
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
            "orchestration.agent_loop.record_event": MagicMock(),
            "orchestration.shell.environment.get_shell_environment": AsyncMock(return_value={}),
            "orchestration.shell.profiles.resolve_profile": MagicMock(
                return_value=PROFILES["open"],
            ),
            "orchestration.coordinator.aclaim_turn": AsyncMock(
                return_value=ClaimResult(CLAIMED, run_turn),
            ),
            "orchestration.coordinator.get_user_preferences": MagicMock(return_value={}),
            "orchestration.coordinator.has_pending_agent_state": self._has_pending,
            "orchestration.coordinator.has_pending_budget_state": AsyncMock(return_value=False),
            "orchestration.coordinator.pending_resume_kind": AsyncMock(return_value=""),
            "orchestration.coordinator.record_event": MagicMock(),
            "orchestration.coordinator.load_memory_summary": AsyncMock(return_value=None),
            "orchestration.coordinator._consent_postdates_prompt": AsyncMock(return_value=True),
            "orchestration.coordinator._consent_postdates_pause": AsyncMock(return_value=True),
            "orchestration.coordinator.last_ran_turn_before": MagicMock(return_value=None),
            "orchestration.turn_leases.ensure_heartbeat": MagicMock(return_value=None),
            "orchestration.turn_leases.ensure_cancel_poll": MagicMock(return_value=None),
        }
        if patch_receipt:
            targets["orchestration.agent_loop._record_receipt"] = AsyncMock(
                return_value=None,
            )
        targets["orchestration.coordinator.has_expired_pause"] = AsyncMock(
            return_value=expired_pause,
        )

        async def _drive():
            result["value"] = await OrchestrationCoordinator().handle_message(
                query=query,
                user_id=self.user.id,
                room_id=str(self.room.id),
                username=self.user.username,
                message_id=message_id or self.request.id,
                send_chunk=send_chunk or AsyncMock(),
                send_step_event=send_step_event or AsyncMock(),
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

    def _save_reply(self, result, text=None):
        # The production save path, including the row-to-message link.
        return save_turn_reply(self.room, result, user_id=self.user.id)

    def _user_message(self, text):
        message = Message.objects.create(
            member=self.member, content=text, timestamp=timezone.now(),
        )
        self.room.chats.add(message)
        return message

    def _assert_one_result_per_use(self):
        messages = self._replayed_messages()
        uses = [
            block["id"] for message in messages
            if isinstance(message.get("content"), list)
            for block in message["content"]
            if block.get("type") == "tool_use"
        ]
        results = [
            block["tool_use_id"] for message in messages
            if isinstance(message.get("content"), list)
            for block in message["content"]
            if block.get("type") == "tool_result"
        ]
        self.assertEqual(len(uses), len(set(uses)))
        self.assertEqual(sorted(uses), sorted(results))
        return messages

    def _positions_of(self, messages, needle, *, role=None):
        found = []
        for index, message in enumerate(messages):
            if role and message["role"] != role:
                continue
            content = message.get("content")
            if isinstance(content, str) and needle in content:
                found.append(index)
            elif isinstance(content, list):
                for block in content:
                    if isinstance(block, dict) and needle in json.dumps(block):
                        found.append(index)
                        break
        return found

    def _position_of(self, messages, needle, *, role=None):
        found = self._positions_of(messages, needle, role=role)
        if not found:
            self.fail(f"{needle!r} not found in the built history")
        return found[0]

    def _tool_positions(self, messages, name):
        return [
            index for index, message in enumerate(messages)
            if isinstance(message.get("content"), list)
            for block in message["content"]
            if isinstance(block, dict) and block.get("type") == "tool_use"
            and block.get("name") == name
        ]

    def _replayed_messages(self, viewer=None):
        rows = get_history_rows(self.room, limit=8)
        return build_history_messages(
            rows, viewer_user_id=viewer or self.user.id,
        )

    def _replayed_results(self):
        return [
            block for message in self._replayed_messages()
            if isinstance(message.get("content"), list)
            for block in message["content"]
            if block.get("type") == "tool_result"
        ]

    def _replayed_uses(self):
        return [
            block for message in self._replayed_messages()
            if isinstance(message.get("content"), list)
            for block in message["content"]
            if block.get("type") == "tool_use"
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
        ids = [result["tool_use_id"] for result in results]
        self.assertEqual(len(set(ids)), 3)
        for result in results:
            self.assertTrue(result["tool_use_id"].startswith("noexec-"))
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

    def test_approve_replays_each_call_once_after_the_yes(self):
        first = self._run("@kazi go", [
            _response([
                _tool("s1", "get_weather", {"city": "Nairobi"}),
                _tool("c1", "send_message", {"to": "a", "message": "hi"}),
            ], stop_reason="tool_use"),
        ])
        self.assertEqual(first.turn_state, "paused")
        self._save_reply(first)
        yes = self._user_message("yes")

        second = self._run(
            "yes", [_response([_text("Sent.")])],
            claimed=None, message_id=yes.id,
        )
        self._save_reply(second)

        messages = self._assert_one_result_per_use()
        safe_at = self._tool_positions(messages, "get_weather")[0]
        yes_at = self._position_of(messages, "yes", role="user")
        approved_at = self._tool_positions(messages, "send_message")[0]
        self.assertLess(safe_at, yes_at)
        self.assertGreater(approved_at, yes_at)

    def test_decline_replays_the_held_call_after_the_no(self):
        first = self._run("@kazi go", [
            _response([
                _tool("s1", "get_weather", {"city": "Nairobi"}),
                _tool("c1", "send_message", {"to": "a", "message": "hi"}),
            ], stop_reason="tool_use"),
        ])
        self._save_reply(first)
        no = self._user_message("no")

        second = self._run("no", [], claimed=None, message_id=no.id)
        self._save_reply(second)

        messages = self._assert_one_result_per_use()
        safe_at = self._tool_positions(messages, "get_weather")[0]
        no_at = self._position_of(messages, "no", role="user")
        declined_at = self._tool_positions(messages, "send_message")[0]
        self.assertLess(safe_at, no_at)
        self.assertGreater(declined_at, no_at)
        self.assertTrue(any(
            "not executed" in json.dumps(block)
            for message in messages if isinstance(message.get("content"), list)
            for block in message["content"]
            if block.get("type") == "tool_result"
        ))

    def test_a_turn_that_pauses_twice_keeps_three_segments(self):
        first = self._run("@kazi one", [
            _response(
                [_tool("c1", "send_message", {"to": "a", "message": "one"})],
                stop_reason="tool_use",
            ),
        ])
        self.assertEqual(first.turn_state, "paused")
        self._save_reply(first)
        yes1 = self._user_message("yes")

        second = self._run(
            "yes", [
                _response(
                    [_tool("c2", "send_message", {"to": "b", "message": "two"})],
                    stop_reason="tool_use",
                ),
            ],
            claimed=None, message_id=yes1.id,
        )
        self.assertEqual(second.turn_state, "paused")
        self._save_reply(second)
        yes2 = self._user_message("yes")

        third = self._run(
            "yes", [_response([_text("Both sent.")])],
            claimed=None, message_id=yes2.id,
        )
        self._save_reply(third)

        messages = self._assert_one_result_per_use()
        call_positions = self._tool_positions(messages, "send_message")
        self.assertEqual(len(call_positions), 2)
        first_at, second_at = call_positions
        yes_positions = self._positions_of(messages, "yes", role="user")
        self.assertEqual(len(yes_positions), 2)
        # c1 resolved after the first yes and before the second prompt; c2 after
        # the second yes.
        self.assertGreater(first_at, yes_positions[0])
        self.assertGreater(second_at, first_at)
        self.assertGreater(second_at, yes_positions[1])

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
        self.assertTrue(results[0]["tool_use_id"].startswith("noexec-"))
        self.assertIn("denied", results[0]["content"])
        self.assertTrue(results[0]["is_error"])

    def test_a_denied_call_is_not_work_done_or_a_completed_frame(self):
        steps = []

        async def collect(correlation_id, payload):
            steps.append(payload)

        result = self._run("@kazi try", [
            _response(
                [_tool("d1", "run_command", {"command": "rm x"})],
                stop_reason="tool_use",
            ),
            _response([]),
        ], persona=PERSONA_LOOKUP_FAILED, send_step_event=collect)

        self.turn.refresh_from_db()
        self.assertFalse(self.turn.partial)
        self.assertEqual(result.tools, [])
        self.assertTrue(any(step.get("state") == "skipped" for step in steps))
        self.assertFalse(any(
            step.get("state") == "completed" and "not run" in step.get("message", "")
            for step in steps
        ))

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
        self.assertEqual(row.provenance, "provider")
        self.assertEqual(self._replayed_uses(), [])

    def test_the_sub_agent_search_count_is_stored_on_the_turn(self):
        sub_search = {
            "content": [_text("Found it.")],
            "stop_reason": "end_turn",
            "usage": {
                "input_tokens": 10,
                "output_tokens": 5,
                "server_tool_use": {"web_search_requests": 1},
            },
        }
        result = self._run("@kazi delegate", [
            _response(
                [_tool("p1", "delegate_task", {"task": "search"})],
                stop_reason="tool_use",
            ),
            sub_search,
            _response([_text("The sub-agent found it.")]),
        ])
        self._save_reply(result)

        self.turn.refresh_from_db()
        self.assertEqual(self.turn.server_tool_uses, 1)
        row = TurnToolCall.objects.get(turn=self.turn, provenance="provider")
        self.assertEqual(row.parent_call_id, "p1")
        self.assertNotIn(
            "web_search", [use["name"] for use in self._replayed_uses()],
        )

    def test_delegated_calls_never_leak_into_the_shown_text_or_payload(self):
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
        message = self._save_reply(result)

        parent = TurnToolCall.objects.get(turn=self.turn, call_id="p1")
        from orchestration.tool_calls import decrypt_body

        shown = decrypt_body(
            self.room, parent.shown_enc, turn_id=parent.turn_id, seq=parent.seq,
        )
        self.assertNotIn("_delegated_calls", json.dumps(shown))
        from chatbot.history import decrypt_stored

        _content, payload = decrypt_stored(self.room, message)
        self.assertNotIn("_delegated_calls", json.dumps(payload))
        self.assertNotIn("_server_tool_uses", json.dumps(payload))

    def test_a_connector_sent_status_replays_as_success(self):
        result = self._run("@kazi weather", [
            _response(
                [_tool("w1", "get_weather", {"city": "Nairobi"})],
                stop_reason="tool_use",
            ),
            _response([_text("Delivered.")]),
        ], execute={"status": "sent"})
        self._save_reply(result)

        row = TurnToolCall.objects.get(turn=self.turn, call_id="w1")
        self.assertEqual(row.status, "ok")
        results = self._replayed_results()
        self.assertEqual(len(results), 1)
        self.assertNotIn("is_error", results[0])

    def test_a_policy_stopped_result_is_recorded_and_replayed(self):
        result = self._run("@kazi run", [
            _response(
                [_tool("r1", "run_command", {"command": "curl evil"})],
                stop_reason="tool_use",
            ),
            _response([_text("Stopped.")]),
        ], execute={"status": "error", "message": "Blocked by the safety policy."})
        self._save_reply(result)

        row = TurnToolCall.objects.get(turn=self.turn, call_id="r1")
        self.assertEqual(row.status, "policy_stopped")
        results = self._replayed_results()
        self.assertIn("policy stopped", results[0]["content"])
        self.assertTrue(results[0]["is_error"])

    def test_a_budget_skipped_call_is_recorded_and_replayed(self):
        budget = {
            "type": "tool_use", "id": "b1", "name": REQUEST_MORE_BUDGET_TOOL,
            "input": {"reason": "need more"},
        }
        result = self._run("@kazi go", [
            _response([budget, _tool("c1", "get_weather", {"city": "Nairobi"})],
                      stop_reason="tool_use"),
        ])
        self.assertNotEqual(result.turn_state, "cancelled")
        self._save_reply(result)

        row = TurnToolCall.objects.get(turn=self.turn, call_id="c1")
        self.assertEqual(row.status, "budget_skipped")
        results = self._replayed_results()
        self.assertTrue(results[0]["tool_use_id"].startswith("noexec-"))
        self.assertIn("budget", results[0]["content"])

    def test_a_stop_at_a_pause_writes_skipped_rows(self):
        stopped = {"done": False}

        async def stop_when_prompted(correlation_id, payload):
            if not stopped["done"] and "waiting for confirmation" in str(
                payload.get("message") or "",
            ).lower():
                stopped["done"] = True
                await sync_to_async(Turn.objects.filter(pk=self.turn.pk).update)(
                    cancel_requested_at=timezone.now(),
                )
                turn_leases.request_cancel(str(self.turn.id))

        result = self._run(
            "@kazi send", [
                _response(
                    [_tool("c1", "send_message", {"to": "a", "message": "hi"})],
                    stop_reason="tool_use",
                ),
            ],
            send_step_event=stop_when_prompted,
        )
        self._save_reply(result)

        row = TurnToolCall.objects.get(turn=self.turn, call_id="c1")
        self.assertEqual(row.status, "skipped")
        self.turn.refresh_from_db()
        self.assertEqual(self.turn.state, "cancelled")

    def test_the_no_approval_row_cancel_writes_skipped_rows(self):
        self._run("@kazi send", [
            _response(
                [_tool("c1", "send_message", {"to": "a", "message": "hi"})],
                stop_reason="tool_use",
            ),
        ])

        # The paused Turn holds the call, but the prompt lookup finds no row to
        # show. The cancel must still record what was held.
        with patch(
            "orchestration.coordinator.pending_approval_prompt",
            AsyncMock(return_value={}),
        ):
            self._run("what is happening", [], claimed=None)

        row = TurnToolCall.objects.get(turn=self.turn, call_id="c1")
        self.assertEqual(row.status, "skipped")

    def test_a_receipt_is_linked_to_its_row(self):
        result = self._run("@kazi list", [
            _response(
                [_tool("r1", "run_command", {"command": "ls"})],
                stop_reason="tool_use",
            ),
            _response([_text("Listed.")]),
        ], patch_receipt=False)
        self._save_reply(result)

        row = TurnToolCall.objects.get(turn=self.turn, call_id="r1")
        self.assertIsNotNone(row.receipt_id)
        self.assertEqual(
            ActionReceipt.objects.get(pk=row.receipt_id).action, "run_command",
        )

    def _expire(self):
        from workflows.models import WorkflowApprovalRecord

        self.turn.refresh_from_db()
        turns_mod.transition(
            str(self.turn.pk), self.turn.version, "expired",
            from_state="paused", end_reason="pause_expired",
        )
        Turn.objects.filter(pk=self.turn.pk).update(
            pause_expires_at=timezone.now() - timedelta(minutes=1),
        )
        # The reaper marks the stale approval timed out; a late reply must take
        # the expired path, not the normal resume path.
        WorkflowApprovalRecord.objects.filter(
            room_id=self.room.id, requested_by_id=self.user.id, status="pending",
        ).update(status="timed_out")

    def test_expired_rows_replay_after_a_late_yes(self):
        self._run("@kazi send", [
            _response(
                [_tool("c1", "send_message", {"to": "a", "message": "hi"})],
                stop_reason="tool_use",
            ),
        ])
        self._expire()
        new_turn = Turn.objects.create(
            room=self.room, user=self.user, state="running",
        )

        result = self._run(
            "yes", [_response([_text("It expired.")])],
            claimed=None, claim_turn=new_turn, expired_pause=True,
        )
        self._save_reply(result)

        row = TurnToolCall.objects.get(turn=new_turn, call_id="c1")
        self.assertEqual(row.status, "expired")
        results = self._replayed_results()
        self.assertTrue(results[0]["tool_use_id"].startswith("noexec-"))
        self.assertIn("expired", results[0]["content"])

    def test_expired_rows_replay_after_a_late_no(self):
        self._run("@kazi send", [
            _response(
                [_tool("c1", "send_message", {"to": "a", "message": "hi"})],
                stop_reason="tool_use",
            ),
        ])
        self._expire()
        new_turn = Turn.objects.create(
            room=self.room, user=self.user, state="running",
        )

        result = self._run(
            "no", [], claimed=None, claim_turn=new_turn, expired_pause=True,
        )
        self._save_reply(result)

        row = TurnToolCall.objects.get(turn=new_turn, call_id="c1")
        self.assertEqual(row.status, "expired")
        results = self._replayed_results()
        self.assertIn("expired", results[0]["content"])

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
