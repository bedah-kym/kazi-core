"""#216a: the durable Turn record and its transition machine."""
from __future__ import annotations

import asyncio
from contextlib import ExitStack
from unittest.mock import AsyncMock, MagicMock, patch

from asgiref.sync import async_to_sync
from django.contrib.auth import get_user_model
from django.db import IntegrityError, transaction
from django.test import SimpleTestCase, TransactionTestCase
from django.utils import timezone

from chatbot.models import Chatroom, Member, Message
from orchestration.agent_loop import AgentEvent
from orchestration.coordinator import OrchestrationCoordinator
from orchestration.models import Turn
from orchestration.turns import (
    ALLOWED,
    TERMINAL_STATES,
    create_turn,
    transition,
)

User = get_user_model()


def _run(coro):
    return asyncio.run(coro)


def _collect(gen):
    async def _inner():
        return [event async for event in gen]

    return _run(_inner())


def _text(text):
    return {"type": "text", "text": text}


def _tool_use(name, tool_input, tool_id="t1"):
    return {"type": "tool_use", "id": tool_id, "name": name, "input": tool_input}


def _response(blocks, stop_reason):
    return {
        "content": blocks,
        "stop_reason": stop_reason,
        "usage": {"input_tokens": 5, "output_tokens": 5},
    }


class TurnTransitionTests(TransactionTestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="turn-matrix", password="fake-token",  # nosec B106
        )
        self.room = Chatroom.objects.create()

    def _queued(self):
        return create_turn(room_id=self.room.id, user_id=self.user.id)

    def test_every_legal_pair_moves_and_bumps_the_version(self):
        legal = [(to, source) for to, sources in ALLOWED.items() for source in sources]
        self.assertTrue(legal)
        for to_state, from_state in legal:
            with self.subTest(to=to_state, source=from_state):
                Turn.objects.all().delete()
                turn = self._queued()
                if from_state != "queued":
                    Turn.objects.filter(pk=turn.pk).update(state=from_state)
                    turn.state = from_state
                moved = transition(
                    str(turn.pk), turn.version, to_state, from_state=from_state,
                )
                self.assertTrue(moved)
                turn.refresh_from_db()
                self.assertEqual(turn.state, to_state)
                self.assertEqual(turn.version, 1)

    def test_every_illegal_pair_is_refused_and_writes_nothing(self):
        states = [choice[0] for choice in Turn.STATE_CHOICES]
        for from_state in states:
            for to_state in states:
                with self.subTest(source=from_state, to=to_state):
                    legal = from_state in ALLOWED.get(to_state, frozenset())
                    if from_state == "queued" and to_state == "running":
                        legal = True  # covered above; keep matrix explicit
                    if legal:
                        continue
                    Turn.objects.all().delete()
                    turn = self._queued()
                    if from_state != "queued":
                        Turn.objects.filter(pk=turn.pk).update(state=from_state)
                        turn.state = from_state
                    moved = transition(
                        str(turn.pk), turn.version, to_state, from_state=from_state,
                    )
                    self.assertFalse(moved)
                    turn.refresh_from_db()
                    self.assertEqual(turn.state, from_state)
                    self.assertEqual(turn.version, 0)

    def test_terminal_states_never_transition(self):
        for state in TERMINAL_STATES:
            with self.subTest(state=state):
                Turn.objects.all().delete()
                turn = self._queued()
                Turn.objects.filter(pk=turn.pk).update(state=state)
                turn.state = state
                for to_state, _label in Turn.STATE_CHOICES:
                    self.assertFalse(transition(
                        str(turn.pk), 0, to_state, from_state=state,
                    ))
                turn.refresh_from_db()
                self.assertEqual(turn.state, state)

    def test_a_compare_and_set_loser_writes_nothing(self):
        turn = self._queued()
        self.assertTrue(transition(str(turn.pk), 0, "running", from_state="queued"))
        # The old version is stale: a second claimant must lose silently.
        loser_events = MagicMock()
        with patch("orchestration.turns.record_event", new=loser_events):
            self.assertFalse(transition(
                str(turn.pk), 0, "running", from_state="queued",
            ))
        loser_events.assert_not_called()
        turn.refresh_from_db()
        self.assertEqual(turn.state, "running")
        self.assertEqual(turn.version, 1)


class TurnConstraintTests(TransactionTestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="turn-constraints", password="fake-token",  # nosec B106
        )
        self.room = Chatroom.objects.create()

    def _create(self, state):
        return Turn.objects.create(
            room=self.room, user=self.user, state=state,
        )

    def test_one_running_or_committing_turn_per_room_and_user(self):
        for state in ("running", "committing"):
            with self.subTest(state=state):
                self._create(state)
                with self.assertRaises(IntegrityError):
                    with transaction.atomic():
                        self._create(state)
                Turn.objects.all().delete()

    def test_one_paused_turn_per_room_and_user(self):
        self._create("paused")
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                self._create("paused")

    def test_a_paused_and_a_running_turn_can_coexist(self):
        self._create("paused")
        self._create("running")
        self.assertEqual(Turn.objects.count(), 2)

    def test_partial_unique_constraints_accept_different_users_and_rooms(self):
        other_user = User.objects.create_user(
            username="turn-constraints-2", password="fake-token",  # nosec B106
        )
        other_room = Chatroom.objects.create()
        self._create("running")
        Turn.objects.create(room=self.room, user=other_user, state="running")
        Turn.objects.create(room=other_room, user=self.user, state="running")
        self.assertEqual(Turn.objects.filter(state="running").count(), 3)


class _CoordinatorTurnBase(TransactionTestCase):
    def setUp(self):
        super().setUp()
        self.user = User.objects.create_user(
            username="turn-coordinator", password="fake-token",  # nosec B106
        )
        self.member, _ = Member.objects.get_or_create(User=self.user)
        self.room = Chatroom.objects.create()
        self.room.participants.add(self.member)
        self.message = Message.objects.create(
            member=self.member,
            content="do the thing",
            timestamp=timezone.now(),
        )
        self.room.chats.add(self.message)
        self.telemetry = MagicMock()

    def _handle(self, query, *, run_loop, resume=None, pending=False, budget=False, pause_kind=""):
        async def _no_events(**_kwargs):
            if False:
                yield AgentEvent("done", {})

        patches = {
            "orchestration.coordinator.load_memory_summary": AsyncMock(return_value=None),
            "orchestration.coordinator.get_user_preferences": lambda user_id, profile_obj=None: {},
            "orchestration.coordinator.has_pending_agent_state": AsyncMock(return_value=pending),
            "orchestration.coordinator.has_pending_budget_state": AsyncMock(return_value=budget),
            "orchestration.coordinator.pending_resume_kind": AsyncMock(
                return_value=pause_kind or "budget",
            ),
            "orchestration.coordinator._quota_resume_still_blocked": AsyncMock(return_value=None),
            "orchestration.coordinator.load_loop_state": MagicMock(return_value=None),
            "orchestration.coordinator.dismiss_pending_confirmation": AsyncMock(),
            "orchestration.coordinator.cancel_pending_action": AsyncMock(return_value="Cancelled."),
            "orchestration.coordinator.cache": MagicMock(),
            "orchestration.coordinator.record_event": MagicMock(),
            "orchestration.turns.record_event": self.telemetry,
            "orchestration.coordinator.run_agent_loop": run_loop,
        }
        if resume is not None:
            patches["orchestration.coordinator.resume_after_confirmation"] = resume

        async def _call():
            return await OrchestrationCoordinator().handle_message(
                query=query,
                user_id=self.user.id,
                room_id=self.room.id,
                username=self.user.username,
                message_id=self.message.id,
                send_chunk=AsyncMock(),
                send_step_event=AsyncMock(),
                get_context_prompt=AsyncMock(return_value=""),
                bump_signals=MagicMock(),
            )

        with ExitStack() as stack:
            for target, value in patches.items():
                stack.enter_context(patch(target, new=value))
            return async_to_sync(_call)()

    @staticmethod
    def _loop(*events):
        async def _events(**_kwargs):
            for event in events:
                yield event

        return _events


class CoordinatorTurnTests(_CoordinatorTurnBase):
    def test_routed_message_creates_exactly_one_turn_and_links_the_request(self):
        result = self._handle(
            "hello",
            run_loop=self._loop(
                AgentEvent("text_delta", {"text": "Hi."}),
                AgentEvent("done", {"end_reason": "finished"}),
            ),
        )

        self.assertEqual(Turn.objects.count(), 1)
        turn = Turn.objects.get()
        self.assertEqual(str(turn.pk), result.turn_id)
        self.assertEqual(turn.state, "completed")
        self.assertEqual(turn.end_reason, "finished")
        self.assertEqual(turn.attempt, 1)
        self.message.refresh_from_db()
        self.assertEqual(self.message.turn_id, turn.pk)

    def test_approval_resume_reuses_the_same_turn_id(self):
        first = self._handle(
            "run the command",
            run_loop=self._loop(
                AgentEvent("text_delta", {"text": "Please confirm."}),
                AgentEvent("paused", {"pause_kind": "approval"}),
            ),
        )
        turn = Turn.objects.get()
        self.assertEqual(turn.state, "paused")
        self.assertEqual(turn.pause_kind, "approval")
        self.assertEqual(turn.attempt, 1)

        second = self._handle(
            "yes",
            pending=True,
            run_loop=self._loop(),
            resume=self._loop(
                AgentEvent("text_delta", {"text": "Done."}),
                AgentEvent("done", {"end_reason": "finished"}),
            ),
        )

        self.assertEqual(first.turn_id, second.turn_id)
        self.assertEqual(Turn.objects.count(), 1)
        turn.refresh_from_db()
        self.assertEqual(turn.state, "completed")
        self.assertEqual(turn.attempt, 2)

    def test_budget_and_quota_resumes_reuse_the_turn(self):
        for pause_reason, pause_kind in (
            ("asked_for_more", "budget"),
            ("quota", "quota"),
        ):
            with self.subTest(reason=pause_reason):
                Turn.objects.all().delete()
                first = self._handle(
                    "do a long task",
                    run_loop=self._loop(
                        AgentEvent("error", {"message": "paused"}),
                        AgentEvent("done", {"end_reason": pause_reason}),
                    ),
                )
                turn = Turn.objects.get()
                self.assertEqual(turn.state, "paused")
                self.assertEqual(turn.pause_kind, pause_kind)

                resume_name = (
                    "orchestration.coordinator.resume_with_more_budget"
                    if pause_kind == "budget"
                    else "orchestration.coordinator.resume_after_quota"
                )
                with patch(resume_name, new=self._loop(
                    AgentEvent("text_delta", {"text": "Done."}),
                    AgentEvent("done", {"end_reason": "finished"}),
                )):
                    second = self._handle(
                        "more budget" if pause_kind == "budget" else "continue",
                        budget=True,
                        pause_kind=pause_kind,
                        run_loop=self._loop(),
                    )
                self.assertEqual(first.turn_id, second.turn_id)
                turn.refresh_from_db()
                self.assertEqual(turn.state, "completed")
                self.assertEqual(turn.attempt, 2)

    def test_break_paths_record_honest_endings(self):
        for reason, expected_state in (
            ("safety_stop", "failed"),
            ("pause_limit", "failed"),
            # The approved mapping says a refusal is the answer, so the turn
            # completed; the reason carries the refusal.
            ("refusal", "completed"),
        ):
            with self.subTest(reason=reason):
                Turn.objects.all().delete()
                self._handle(
                    "hello",
                    run_loop=self._loop(
                        AgentEvent("error", {"message": "stopped"}),
                        AgentEvent("done", {"end_reason": reason}),
                    ),
                )
                turn = Turn.objects.get()
                self.assertEqual(turn.end_reason, reason)
                self.assertEqual(turn.state, expected_state)

    def test_confirmation_pause_emits_turn_paused(self):
        self._handle(
            "run the command",
            run_loop=self._loop(
                AgentEvent("paused", {"pause_kind": "approval"}),
            ),
        )
        events = [call.args[0] for call in self.telemetry.call_args_list if call.args]
        self.assertIn("turn_paused", events)


class AgentLoopEndReasonTests(SimpleTestCase):
    """The three break paths no longer fall through to `finished`."""

    def _loop(self, responses, preferences=None, tools=None):
        from orchestration.agent_loop import run_agent_loop

        mock_llm = MagicMock()
        mock_llm.default_provider.return_value = "anthropic"
        mock_llm.create_message = AsyncMock(side_effect=responses)
        mock_cache = MagicMock()
        mock_cache.get.return_value = None
        with (
            patch("orchestration.agent_loop.get_llm_client", return_value=mock_llm),
            patch("orchestration.agent_loop.cache", new=mock_cache),
            patch("orchestration.agent_loop._resolve_persona", new=AsyncMock(return_value=None)),
            patch("orchestration.agent_loop.execute_tool", new=AsyncMock(return_value={
                "status": "error", "message": "blocked by safety policy",
            })),
            patch("orchestration.agent_loop.preview_tool", new=AsyncMock(return_value=None)),
            patch("orchestration.agent_loop.save_pending_confirmation", new=AsyncMock()),
            patch("orchestration.agent_loop._record_receipt", new=AsyncMock()),
            patch("orchestration.agent_loop.record_event"),
        ):
            events = _collect(run_agent_loop(
                user_message="do it",
                context={"user_id": None, "room_id": 1, "username": "test"},
                preferences=preferences or {},
            ))
        return events

    def test_repeated_safety_blocks_end_failed(self):
        events = self._loop([
            _response([_tool_use("get_weather", {"city": "a"}, "t1")], "tool_use"),
            _response([_tool_use("get_weather", {"city": "b"}, "t2")], "tool_use"),
            _response([_tool_use("get_weather", {"city": "c"}, "t3")], "tool_use"),
        ])
        done = next(e for e in events if e.kind == "done")
        self.assertEqual(done.data["end_reason"], "safety_stop")

    def test_pause_limit_ends_failed(self):
        from orchestration.agent_loop import MAX_CONSECUTIVE_PAUSES

        events = self._loop([
            _response([_text("Working.")], "pause_turn")
            for _ in range(MAX_CONSECUTIVE_PAUSES + 1)
        ])
        done = next(e for e in events if e.kind == "done")
        self.assertEqual(done.data["end_reason"], "pause_limit")

    def test_refusal_is_recorded_as_refusal(self):
        events = self._loop([_response([_text("No.")], "refusal")])
        done = next(e for e in events if e.kind == "done")
        self.assertEqual(done.data["end_reason"], "refusal")

    def test_confirmation_emits_a_paused_event(self):
        events = self._loop(
            [_response([_tool_use("send_email", {"to": "ops@example.com"}, "t1")], "tool_use")],
            preferences={"approval_overrides": {}},
        )
        self.assertIn("confirmation", [e.kind for e in events])
        paused = [e for e in events if e.kind == "paused"]
        self.assertEqual(len(paused), 1)
        self.assertEqual(paused[0].data["pause_kind"], "approval")


class TurnWriteCeilingTests(_CoordinatorTurnBase):
    """The durable record costs four Turn writes plus one request link."""

    def test_a_completed_turn_costs_at_most_five_writes(self):
        from django.db.backends.utils import CursorWrapper

        seen = []
        real_execute = CursorWrapper.execute

        def counting_execute(cursor, sql, params=None):
            seen.append(sql)
            return real_execute(cursor, sql, params)

        with patch.object(CursorWrapper, "execute", new=counting_execute):
            self._handle(
                "hello",
                run_loop=self._loop(
                    AgentEvent("text_delta", {"text": "Hi."}),
                    AgentEvent("done", {"end_reason": "finished"}),
                ),
            )

        writes = [
            sql for sql in seen
            if sql.lstrip().upper().startswith(("INSERT", "UPDATE"))
            and ("orchestration_turn" in sql or "chatbot_message" in sql)
        ]
        self.assertGreaterEqual(len(writes), 3)
        self.assertLessEqual(len(writes), 5)
