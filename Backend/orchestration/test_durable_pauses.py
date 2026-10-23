"""T-D4: durable pauses that survive a restart (P3-3).

Every test that matters here fails on ``main``: the pause lived in a cache
with a ten-minute TTL, so a fresh process could not resume it, and only the
first gated call was held.
"""
from __future__ import annotations

import asyncio
from datetime import timedelta
from unittest.mock import AsyncMock, MagicMock, patch

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TransactionTestCase, override_settings
from django.utils import timezone

from chatbot.models import Chatroom
from orchestration import turns as turns_mod
from orchestration.agent_loop import (
    resume_after_confirmation,
    resume_after_expiry,
    resume_after_quota,
    resume_with_more_budget,
    run_agent_loop,
    save_pending_confirmation,
)
from orchestration.models import Turn
from workflows.models import WorkflowApprovalRecord


def _run(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


async def _collect(gen):
    return [event async for event in gen]


def _response(blocks, stop_reason="end_turn", usage=None):
    return {
        "content": blocks,
        "stop_reason": stop_reason,
        "usage": usage or {"input_tokens": 10, "output_tokens": 5},
    }


def _text(text):
    return {"type": "text", "text": text}


def _tool(call_id, name, tool_input):
    return {"type": "tool_use", "id": call_id, "name": name, "input": tool_input}


def _assert_history_paired(messages):
    """Every tool_use must be answered before the next assistant turn."""
    pending = set()
    for message in messages:
        content = message.get("content")
        if not isinstance(content, list):
            continue
        for block in content:
            if block.get("type") == "tool_use":
                pending.add(block.get("id"))
            elif block.get("type") == "tool_result":
                pending.discard(block.get("tool_use_id"))
    if pending:
        raise AssertionError(f"tool_use left unpaired: {pending}")


class _PauseHarness(TransactionTestCase):
    def setUp(self):
        super().setUp()
        cache.clear()
        User = get_user_model()
        self.user = User.objects.create_user(username="pauseuser", password="pw")  # nosec B106
        self.room = Chatroom.objects.create()

        self.persona = patch(
            "orchestration.agent_loop._resolve_persona",
            new=AsyncMock(return_value=None),
        )
        self.persona.start()
        self.addCleanup(self.persona.stop)
        self.patches = [
            patch("orchestration.agent_loop.enforce_agent_caps", return_value=True),
            patch("orchestration.agent_loop._search_backend_available", return_value=False),
            patch("orchestration.agent_loop.get_llm_client"),
            patch("orchestration.agent_loop.execute_tool", new_callable=AsyncMock),
            patch("orchestration.agent_loop.preview_tool", new_callable=AsyncMock),
        ]
        started = [p.start() for p in self.patches]
        self.addCleanup(lambda: [p.stop() for p in self.patches])
        self.llm_client = started[2]
        self.execute_tool = started[3]
        self.preview_tool = started[4]
        self.preview_tool.return_value = None
        self.execute_tool.return_value = {"status": "success", "data": {"ok": True}}

    def _turn(self):
        return Turn.objects.create(
            room=self.room,
            user=self.user,
            state="running",
            version=0,
            started_at=timezone.now(),
            attempt=1,
        )

    def _llm(self, responses):
        llm = MagicMock()
        llm.default_provider = MagicMock(return_value="deepseek")
        llm.create_message = AsyncMock(side_effect=responses)
        self.llm_client.return_value = llm
        return llm

    def _context(self, turn):
        return {
            "user_id": self.user.id,
            "room_id": self.room.id,
            "username": self.user.username,
            "turn_id": str(turn.pk),
        }

    def _pause(self, turn, calls, *, kind="approval", state=None):
        payload = dict(state or {})
        if calls:
            payload.setdefault("messages", [
                {"role": "assistant", "content": [
                    _tool(c["id"], c["name"], c["input"]) for c in calls
                ]},
            ])
            payload.setdefault("pending_tools", calls)
            payload.setdefault("pending_tiers", [None for _ in calls])
            payload.setdefault("pending_tool", calls[0])
            payload.setdefault("pending_tier", None)
            payload.setdefault("paused_for_confirmation", True)
        payload.setdefault("active_seconds", 0.0)
        payload.setdefault("wall_started_at", timezone.now().timestamp())
        return turns_mod.pause_turn(
            str(turn.pk), turn.version,
            state_payload=payload, pause_kind=kind,
        )


class RestartApprovalTests(_PauseHarness):
    def test_restart_mid_approval_runs_the_stored_action_once(self):
        turn = self._turn()
        call = {"id": "call-1", "name": "send_email", "input": {"to": "a@b.c", "body": "hi"}}
        self._llm([
            _response([_tool("call-1", "send_email", call["input"])], stop_reason="tool_use"),
            _response([_text("Sent.")]),
        ])

        events = _run(_collect(run_agent_loop(
            user_message="send it",
            context=self._context(turn),
            preferences={},
        )))
        self.assertTrue(any(e.kind == "confirmation" for e in events))
        turn.refresh_from_db()
        self.assertEqual(turn.state, "paused")
        self.assertEqual(turn.pause_kind, "approval")
        self.assertIsNotNone(turn.paused_state_enc)
        self.assertNotIn("a@b.c", turn.paused_state_enc)

        # A fresh process: the cache is empty, but the Turn is not.
        cache.clear()
        resumed = _run(_collect(resume_after_confirmation(
            context=self._context(turn),
            preferences={},
        )))

        self.assertEqual(self.execute_tool.await_count, 1)
        self.assertEqual(self.execute_tool.await_args.args[0], "send_email")
        self.assertEqual(self.execute_tool.await_args.args[1], call["input"])
        self.assertTrue(any(e.kind == "done" for e in resumed))
        self.assertEqual(
            WorkflowApprovalRecord.objects.get(kind="agent_loop").status, "approved",
        )

    def test_the_same_approval_can_never_run_twice(self):
        turn = self._turn()
        call = {"id": "c1", "name": "send_email", "input": {"to": "a@b.c"}}
        self._llm([
            _response([_tool("c1", "send_email", call["input"])], stop_reason="tool_use"),
            _response([_text("Sent.")]),
        ])
        _run(_collect(run_agent_loop(
            user_message="send it", context=self._context(turn), preferences={},
        )))
        cache.clear()

        first = _run(_collect(resume_after_confirmation(
            context=self._context(turn), preferences={},
        )))
        second = _run(_collect(resume_after_confirmation(
            context=self._context(turn), preferences={},
        )))
        self.assertEqual(self.execute_tool.await_count, 1)
        self.assertTrue(any(e.kind == "done" for e in first))
        self.assertTrue(any(e.kind == "error" for e in second))


class HeldBatchTests(_PauseHarness):
    def test_two_gated_calls_are_held_and_run_together(self):
        turn = self._turn()
        first = {"id": "c1", "name": "send_email", "input": {"to": "a@b.c"}}
        second = {"id": "c2", "name": "send_email", "input": {"to": "d@e.f"}}
        llm = self._llm([
            _response([
                _tool("c1", "send_email", first["input"]),
                _tool("c2", "send_email", second["input"]),
            ], stop_reason="tool_use"),
            _response([_text("Both sent.")]),
        ])

        events = _run(_collect(run_agent_loop(
            user_message="send both",
            context=self._context(turn),
            preferences={},
        )))
        confirmation = next(e for e in events if e.kind == "confirmation")
        self.assertIn("2 actions", confirmation.data["message"])
        self.assertNotIn("Skipped:", confirmation.data["message"])
        self.assertEqual(len(confirmation.data["calls"]), 2)

        record = WorkflowApprovalRecord.objects.get(kind="agent_loop")
        self.assertEqual(len(record.metadata["calls"]), 2)
        self.assertEqual(record.sanitized_params, {"to": "[REDACTED EMAIL]"})

        cache.clear()
        turn.refresh_from_db()
        resumed = _run(_collect(resume_after_confirmation(
            context=self._context(turn), preferences={},
        )))
        self.assertEqual(self.execute_tool.await_count, 2)
        self.assertEqual(
            [c.args[1]["to"] for c in self.execute_tool.await_args_list],
            ["a@b.c", "d@e.f"],
        )
        done = next(e for e in resumed if e.kind == "done")
        self.assertEqual(done.data["end_reason"], "finished")
        messages = llm.create_message.await_args_list[-1].kwargs["messages"]
        _assert_history_paired(messages)

    def test_tainted_pause_resumes_tainted_after_room_key_is_gone(self):
        turn = self._turn()
        call = {"id": "c1", "name": "send_email", "input": {"to": "a@b.c"}}
        self._pause(turn, [call], state={"tainted": True})
        cache.delete("agent_loop_taint:{}".format(self.room.id))
        turn.refresh_from_db()
        state = _run(turns_mod.aget_turn_paused_state(turn))
        self.assertIsNotNone(state)
        self.assertTrue(state["tainted"])

    def test_reproposed_arguments_do_not_run_without_a_new_approval(self):
        turn = self._turn()
        shown = {"id": "c1", "name": "send_email", "input": {"to": "safe@b.c"}}
        other = {"id": "c1", "name": "send_email", "input": {"to": "evil@b.c"}}
        _run(save_pending_confirmation(
            self.room.id, self.user.id, shown, "Send?",
            turn_id=turn.pk, calls=[shown],
        ))
        # The stored state tries to run different arguments.
        self._pause(turn, [other])
        cache.clear()

        events = _run(_collect(resume_after_confirmation(
            context=self._context(turn), preferences={},
        )))
        self.assertEqual(self.execute_tool.await_count, 0)
        self.assertTrue(any(e.kind == "error" for e in events))


class BudgetAndQuotaRestartTests(_PauseHarness):
    @override_settings(AGENT_TURN_TOKEN_BUDGET=10, AGENT_TURN_TOKEN_BUDGET_MAX=100000)
    def test_budget_pause_survives_a_restart(self):
        turn = self._turn()
        self._llm([
            _response(
                [_tool("w1", "get_weather", {"city": "Nairobi"})],
                stop_reason="tool_use",
                usage={"input_tokens": 50, "output_tokens": 50},
            ),
            _response([_text("Finished.")]),
        ])
        events = _run(_collect(run_agent_loop(
            user_message="long task", context=self._context(turn), preferences={},
        )))
        self.assertTrue(any(e.kind == "paused" for e in events))
        turn.refresh_from_db()
        self.assertEqual((turn.state, turn.pause_kind), ("paused", "budget"))

        cache.clear()
        resumed = _run(_collect(resume_with_more_budget(
            context=self._context(turn), preferences={},
        )))
        done = next(e for e in resumed if e.kind == "done")
        self.assertEqual(done.data["end_reason"], "finished")

    def test_quota_pause_survives_a_restart(self):
        from orchestration.llm_client import TokenQuotaExceeded

        turn = self._turn()
        llm = self._llm([
            TokenQuotaExceeded(1800, reason="limit"),
            _response([_text("Finished.")]),
        ])
        events = _run(_collect(run_agent_loop(
            user_message="long task", context=self._context(turn), preferences={},
        )))
        self.assertTrue(any(e.kind == "paused" for e in events))
        turn.refresh_from_db()
        self.assertEqual((turn.state, turn.pause_kind), ("paused", "quota"))

        cache.clear()
        resumed = _run(_collect(resume_after_quota(
            context=self._context(turn), preferences={},
        )))
        self.assertTrue(any(e.kind == "done" for e in resumed))
        self.assertGreaterEqual(llm.create_message.await_count, 2)


class ExpiryAndTimeTests(_PauseHarness):
    def test_late_yes_after_expiry_carries_an_is_error_result(self):
        turn = self._turn()
        call = {"id": "c1", "name": "send_email", "input": {"to": "a@b.c"}}
        self._pause(turn, [call])
        turn.refresh_from_db()
        turns_mod.transition(
            str(turn.pk), turn.version, "expired",
            from_state="paused", end_reason="pause_expired",
        )
        Turn.objects.filter(pk=turn.pk).update(
            pause_expires_at=timezone.now() - timedelta(minutes=1),
        )
        llm = self._llm([_response([_text("It expired.")])])

        events = _run(_collect(resume_after_expiry(
            context=self._context(turn), preferences={},
        )))
        self.assertTrue(any(
            e.kind == "text_delta" and "expired" in e.data.get("text", "").lower()
            for e in events
        ))
        messages = llm.create_message.await_args.kwargs["messages"]
        self.assertTrue(any(
            block.get("is_error") is True
            for message in messages if isinstance(message.get("content"), list)
            for block in message["content"] if isinstance(block, dict)
        ))
        _assert_history_paired(messages)
        self.assertEqual(self.execute_tool.await_count, 0)

    @override_settings(HARD_TURN_TIMEOUT_SECONDS=60)
    def test_elapsed_spans_the_pause_and_the_backstop_uses_active_time(self):
        turn = self._turn()
        state = {
            "messages": [{"role": "user", "content": "keep going"}],
            "awaiting_budget": True,
            "token_budget": 50000,  # nosec B105 — test fixture, not a credential
            "tokens_used": 10,
            "active_seconds": 5.0,
            "wall_started_at": timezone.now().timestamp() - 3600,
        }
        self.assertTrue(self._pause(turn, [], kind="budget", state=state))
        self._llm([_response([_text("Finished.")])])

        events = _run(_collect(resume_with_more_budget(
            context=self._context(turn), preferences={},
        )))
        done = next(e for e in events if e.kind == "done")
        self.assertGreaterEqual(done.data["elapsed"], 3500)
        self.assertFalse(any(
            e.kind == "error" and "ran out of time" in e.data.get("message", "")
            for e in events
        ))


class CoordinatorRestartTests(_PauseHarness):
    def _coordinator(self):
        from asgiref.sync import async_to_sync
        from orchestration.coordinator import OrchestrationCoordinator

        async def _send(*args, **kwargs):
            return None

        async def _send_step(*args, **kwargs):
            return None

        async def _run(query):
            return await OrchestrationCoordinator().handle_message(
                query=query,
                user_id=self.user.id,
                room_id=self.room.id,
                username=self.user.username,
                message_id=None,
                send_chunk=_send,
                send_step_event=_send_step,
                get_context_prompt=AsyncMock(return_value=""),
                bump_signals=MagicMock(),
                history_messages=None,
            )

        with (
            patch("orchestration.coordinator.load_memory_summary", AsyncMock(return_value=None)),
            patch("orchestration.coordinator.get_user_preferences", lambda user_id, profile_obj=None: {}),
            patch("orchestration.coordinator.cache", MagicMock()),
            patch("orchestration.coordinator.record_event", MagicMock()),
            patch("orchestration.coordinator._quota_resume_still_blocked", AsyncMock(return_value=None)),
        ):
            return async_to_sync(_run)

    def test_a_fresh_coordinator_resumes_the_paused_action_on_yes(self):
        call = {"id": "c1", "name": "send_email", "input": {"to": "a@b.c"}}
        self._llm([
            _response([_tool("c1", "send_email", call["input"])], stop_reason="tool_use"),
            _response([_text("Sent.")]),
        ])

        run = self._coordinator()
        run("send it")
        turn = Turn.objects.get()
        self.assertEqual((turn.state, turn.pause_kind), ("paused", "approval"))

        # Restart: no cache, a brand-new coordinator, the same durable Turn.
        cache.clear()
        run("yes")

        self.assertEqual(self.execute_tool.await_count, 1)
        self.assertEqual(self.execute_tool.await_args.args[1], call["input"])
        self.assertEqual(
            WorkflowApprovalRecord.objects.get(kind="agent_loop").status, "approved",
        )
