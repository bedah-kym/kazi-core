"""#216a: the durable Turn record and its transition machine."""
from __future__ import annotations

import asyncio
from contextlib import ExitStack
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from asgiref.sync import async_to_sync
from django.contrib.auth import get_user_model
from django.db import IntegrityError, transaction
from django.test import SimpleTestCase, TransactionTestCase, override_settings
from django.utils import timezone

from chatbot.models import Chatroom, Member, Message
from orchestration.agent_loop import AgentEvent, HARD_TURN_TIMEOUT_SECONDS
from orchestration.coordinator import OrchestrationCoordinator
from orchestration.llm_client import TokenQuotaExceeded
from orchestration.models import Turn
from orchestration.turns import (
    ALLOWED,
    BUSY_LIVE,
    CLAIMED,
    STALE_TURN_MARGIN_SECONDS,
    TERMINAL_STATES,
    TOOK_OVER,
    atransition as real_atransition,
    claim_turn,
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


class _TurnTelemetryMixin:
    def _silence_turn_telemetry(self):
        patcher = patch("orchestration.turns.record_event")
        self.addCleanup(patcher.stop)
        return patcher.start()


class TurnTransitionTests(_TurnTelemetryMixin, TransactionTestCase):
    def setUp(self):
        super().setUp()
        self._silence_turn_telemetry()
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


class TurnConstraintTests(_TurnTelemetryMixin, TransactionTestCase):
    def setUp(self):
        super().setUp()
        self._silence_turn_telemetry()
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

    def test_a_lost_race_inside_an_outer_atomic_block_does_not_poison_it(self):
        paused = self._create("paused")
        self._create("running")
        with transaction.atomic():
            # paused -> running would violate the active-turn constraint; the
            # savepoint must absorb the IntegrityError and return False.
            moved = transition(
                str(paused.pk), paused.version, "running", from_state="paused",
            )
            self.assertFalse(moved)
            self.assertEqual(Turn.objects.filter(state="running").count(), 1)


class ClaimTurnTests(_TurnTelemetryMixin, TransactionTestCase):
    """The running row is one INSERT; a refused claim only takes over stale rows."""

    def setUp(self):
        super().setUp()
        self._silence_turn_telemetry()
        self.user = User.objects.create_user(
            username="turn-claim", password="fake-token",  # nosec B106
        )
        self.room = Chatroom.objects.create()

    def _running(self, started_at=None, state="running"):
        return Turn.objects.create(
            room=self.room, user=self.user, state=state,
            started_at=started_at or timezone.now(),
        )

    def test_a_claim_inserts_running_directly_with_no_queued_row(self):
        claim = claim_turn(room_id=self.room.id, user_id=self.user.id)
        self.assertEqual(claim.status, CLAIMED)
        turn = claim.turn
        self.assertIsNotNone(turn)
        self.assertEqual(turn.state, "running")
        self.assertEqual(turn.attempt, 1)
        self.assertEqual(Turn.objects.filter(state="queued").count(), 0)

    @override_settings(KAZI_LITE_PROFILE=True)
    def test_a_lapsed_lease_is_taken_over_and_leaves_paused_alone(self):
        old = Turn.objects.create(
            room=self.room, user=self.user, state="running",
            started_at=timezone.now(), owner_id="another-worker",
            lease_expires_at=timezone.now() - timedelta(seconds=1),
        )
        paused = Turn.objects.create(
            room=self.room, user=self.user, state="paused",
            end_reason="awaiting_approval",
        )
        claim = claim_turn(room_id=self.room.id, user_id=self.user.id)
        self.assertEqual(claim.status, TOOK_OVER)
        self.assertIsNotNone(claim.turn)
        old.refresh_from_db()
        paused.refresh_from_db()
        self.assertEqual(old.state, "failed")
        self.assertEqual(old.end_reason, "lease_lost")
        self.assertEqual(paused.state, "paused")
        self.assertEqual(paused.end_reason, "awaiting_approval")

    @override_settings(KAZI_LITE_PROFILE=False)
    def test_full_profile_refuses_a_fresh_blocking_row(self):
        old = self._running()
        claim = claim_turn(room_id=self.room.id, user_id=self.user.id)
        self.assertEqual(claim.status, BUSY_LIVE)
        self.assertIsNone(claim.turn)
        old.refresh_from_db()
        self.assertEqual(old.state, "running")
        self.assertEqual(Turn.objects.count(), 1)

    @override_settings(KAZI_LITE_PROFILE=False)
    def test_full_profile_takes_over_a_lease_less_row_past_the_hard_guard(self):
        old = self._running(started_at=timezone.now() - timedelta(
            seconds=HARD_TURN_TIMEOUT_SECONDS + STALE_TURN_MARGIN_SECONDS + 5,
        ))
        claim = claim_turn(room_id=self.room.id, user_id=self.user.id)
        self.assertEqual(claim.status, TOOK_OVER)
        self.assertIsNotNone(claim.turn)
        old.refresh_from_db()
        self.assertEqual(old.state, "failed")
        self.assertEqual(old.end_reason, "abandoned")
        self.assertEqual(Turn.objects.filter(state="running").count(), 1)


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

    def _paused_turn(self, **kwargs):
        return Turn.objects.create(
            room=self.room, user=self.user, state="paused",
            started_at=timezone.now(), attempt=1, **kwargs,
        )

    def _handle(
        self,
        query,
        *,
        run_loop,
        resume=None,
        pending=False,
        budget=False,
        pause_kind="",
        send_chunk=None,
        send_step_event=None,
        extra_patches=None,
        loop_state=None,
        finalize=True,
        reply_saved=True,
    ):
        async def _no_events(**_kwargs):
            if False:
                yield AgentEvent("done", {})

        patches = {
            "orchestration.coordinator.load_memory_summary": AsyncMock(return_value=None),
            "orchestration.coordinator.get_user_preferences": lambda user_id, profile_obj=None: {},
            "orchestration.coordinator.has_pending_agent_state": AsyncMock(return_value=pending),
            "orchestration.coordinator._consent_postdates_prompt": AsyncMock(return_value=True),
            "orchestration.coordinator.has_pending_budget_state": AsyncMock(return_value=budget),
            "orchestration.coordinator.pending_resume_kind": AsyncMock(
                return_value=pause_kind or "budget",
            ),
            "orchestration.coordinator._quota_resume_still_blocked": AsyncMock(return_value=None),
            "orchestration.coordinator.load_pending_state": AsyncMock(return_value=loop_state),
            "orchestration.coordinator.dismiss_pending_confirmation": AsyncMock(),
            "orchestration.coordinator.cancel_pending_action": AsyncMock(return_value="Cancelled."),
            "orchestration.coordinator.cache": MagicMock(),
            "orchestration.coordinator.record_event": MagicMock(),
            "orchestration.turns.record_event": self.telemetry,
            "orchestration.coordinator.run_agent_loop": run_loop,
        }
        if resume is not None:
            patches["orchestration.coordinator.resume_after_confirmation"] = resume
        if extra_patches:
            patches.update(extra_patches)
        if send_chunk is None:
            send_chunk = AsyncMock()
        if send_step_event is None:
            send_step_event = AsyncMock()

        async def _call():
            callback = send_chunk
            result = await OrchestrationCoordinator().handle_message(
                query=query,
                user_id=self.user.id,
                room_id=self.room.id,
                username=self.user.username,
                message_id=self.message.id,
                send_chunk=callback,
                send_step_event=send_step_event,
                get_context_prompt=AsyncMock(return_value=""),
                bump_signals=MagicMock(),
            )
            if finalize:
                await OrchestrationCoordinator().finalize_turn(
                    result, reply_saved=reply_saved,
                )
            return result

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
                    budget=pause_kind == "budget",
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
            ("unknown_stop", "failed"),
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

    def _real_loop(self, responses):
        from orchestration.agent_loop import run_agent_loop

        mock_llm = MagicMock()
        mock_llm.default_provider.return_value = "anthropic"
        mock_llm.create_message = AsyncMock(side_effect=responses)
        mock_cache = MagicMock()
        mock_cache.get.return_value = None

        def run_loop(**kwargs):
            return run_agent_loop(**kwargs)

        patches = {
            "orchestration.agent_loop.get_llm_client": MagicMock(return_value=mock_llm),
            "orchestration.agent_loop.cache": mock_cache,
            "orchestration.agent_loop._resolve_persona": AsyncMock(return_value=None),
            "orchestration.agent_loop.execute_tool": AsyncMock(return_value={"status": "success"}),
            "orchestration.agent_loop.preview_tool": AsyncMock(return_value=None),
            "orchestration.agent_loop.save_pending_confirmation": AsyncMock(),
            "orchestration.agent_loop._record_receipt": AsyncMock(),
            "orchestration.agent_loop.record_event": MagicMock(),
            "orchestration.shell.environment.get_shell_environment": AsyncMock(return_value={}),
        }
        return run_loop, patches

    def test_a_real_cut_off_loop_records_length_limit_and_partial(self):
        run_loop, patches = self._real_loop([
            _response([_text("Here is the first part")], "max_tokens"),
        ])
        self._handle("write a long file", run_loop=run_loop, extra_patches=patches)

        turn = Turn.objects.get()
        self.assertEqual(turn.state, "failed")
        self.assertEqual(turn.end_reason, "length_limit")
        self.assertTrue(turn.partial)

    def test_a_real_cut_off_loop_with_nothing_visible_is_not_partial(self):
        run_loop, patches = self._real_loop([_response([], "max_tokens")])
        self._handle("write a long file", run_loop=run_loop, extra_patches=patches)

        turn = Turn.objects.get()
        self.assertEqual(turn.state, "failed")
        self.assertEqual(turn.end_reason, "length_limit")
        self.assertFalse(turn.partial)

    @staticmethod
    def _progress_messages(step_events):
        return [
            call.args[1].get("message")
            for call in step_events.await_args_list
            if len(call.args) > 1 and isinstance(call.args[1], dict)
        ]

    def test_a_length_limit_ending_does_not_emit_an_error_progress_label(self):
        step_events = AsyncMock()
        self._handle(
            "write a long file",
            send_step_event=step_events,
            run_loop=self._loop(
                AgentEvent("error", {
                    "message": "cut short",
                    "end_reason": "length_limit",
                }),
                AgentEvent("done", {"end_reason": "length_limit"}),
            ),
        )

        messages = self._progress_messages(step_events)
        self.assertIn("Response cut short by the length limit.", messages)
        self.assertNotIn("Error encountered.", messages)

    def test_a_length_limit_note_starts_a_new_paragraph_after_visible_text(self):
        send_chunk = AsyncMock()
        run_loop, patches = self._real_loop([
            _response([_text("The story reached the 1890s. Part")], "max_tokens"),
        ])
        self._handle(
            "write a long file",
            send_chunk=send_chunk,
            run_loop=run_loop,
            extra_patches=patches,
        )

        visible = "".join(call.args[1] for call in send_chunk.await_args_list)
        self.assertIn(
            "The story reached the 1890s. Part\n\n"
            "My response was cut short by the length limit.",
            visible,
        )

    def test_a_length_limit_note_has_no_leading_blank_lines_without_visible_text(self):
        send_chunk = AsyncMock()
        run_loop, patches = self._real_loop([_response([], "max_tokens")])
        self._handle(
            "write a long file",
            send_chunk=send_chunk,
            run_loop=run_loop,
            extra_patches=patches,
        )

        visible = "".join(call.args[1] for call in send_chunk.await_args_list)
        self.assertEqual(
            visible,
            "My response was cut short by the length limit. "
            "Anything I was still writing did not run.",
        )

    def test_a_real_error_still_emits_the_error_progress_label(self):
        step_events = AsyncMock()
        self._handle(
            "hello",
            send_step_event=step_events,
            run_loop=self._loop(
                AgentEvent("error", {"message": "boom"}),
                AgentEvent("done", {"end_reason": "error"}),
            ),
        )

        self.assertIn("Error encountered.", self._progress_messages(step_events))

    def test_confirmation_pause_emits_turn_paused(self):
        self._handle(
            "run the command",
            run_loop=self._loop(
                AgentEvent("paused", {"pause_kind": "approval"}),
            ),
        )
        events = [call.args[0] for call in self.telemetry.call_args_list if call.args]
        self.assertIn("turn_paused", events)

    def test_budget_at_the_ceiling_without_kept_state_fails_not_pauses(self):
        self._handle(
            "hello",
            budget=False,
            run_loop=self._loop(
                AgentEvent("text_delta", {"text": "Some work."}),
                AgentEvent("done", {"end_reason": "budget"}),
            ),
        )
        turn = Turn.objects.get()
        self.assertEqual(turn.state, "failed")
        self.assertEqual(turn.end_reason, "budget")
        self.assertTrue(turn.partial)

    def test_a_completed_turn_waits_for_the_consumer_to_save_the_reply(self):
        result = self._handle(
            "hello",
            finalize=False,
            run_loop=self._loop(
                AgentEvent("text_delta", {"text": "Hi."}),
                AgentEvent("done", {"end_reason": "finished"}),
            ),
        )
        turn = Turn.objects.get()
        self.assertEqual(turn.state, "committing")
        self.assertEqual(result.turn_state, "committing")

        with patch("orchestration.turns.record_event"):
            moved = async_to_sync(OrchestrationCoordinator().finalize_turn)(
                result, reply_saved=False,
            )
        self.assertTrue(moved)
        turn.refresh_from_db()
        self.assertEqual(turn.state, "failed")
        self.assertEqual(turn.end_reason, "reply_not_saved")
        self.assertTrue(turn.partial)

    def test_a_fenced_owners_late_reply_is_still_saved_with_a_harness_line(self):
        from chatbot.kazi_messages import ORIGIN_TURN, persist_kazi_message

        async def _fenced_loop(**kwargs):
            from orchestration import turns as turns_mod

            turn = await turns_mod.aget_turn(kwargs["context"]["turn_id"])
            await turns_mod.atransition(
                str(turn.pk), turn.version, "failed",
                from_state="running", end_reason="lease_lost",
            )
            yield AgentEvent("harness", {
                "text": "[Harness: this turn was marked interrupted.]\n\n",
            })
            yield AgentEvent("text_delta", {"text": "Here is what I had."})
            yield AgentEvent("done", {"end_reason": "lease_lost"})

        result = self._handle("hello", run_loop=_fenced_loop, finalize=False)
        turn = Turn.objects.get()
        self.assertEqual(turn.state, "failed")
        self.assertEqual(turn.end_reason, "lease_lost")
        self.assertTrue(result.persist)
        self.assertEqual(result.turn_id, str(turn.pk))
        self.assertIn("Here is what I had.", result.full_response)
        self.assertIn("marked interrupted", result.full_response)

        persist_kazi_message(
            self.room,
            content=result.full_response,
            origin=ORIGIN_TURN,
            turn=result.turn_id,
            model_text=result.model_text,
            harness_text=result.harness,
            broadcast=False,
        )
        self.assertTrue(Message.objects.filter(turn_id=turn.pk).exists())

    @override_settings(KAZI_LITE_PROFILE=True)
    def test_a_lapsed_running_row_is_taken_over_and_the_next_message_runs(self):
        old = Turn.objects.create(
            room=self.room, user=self.user, state="running",
            started_at=timezone.now(), version=3, owner_id="another-worker",
            lease_expires_at=timezone.now() - timedelta(seconds=1),
        )
        kazi_before = Message.objects.filter(member__User__username="kazi").count()
        result = self._handle(
            "hello",
            run_loop=self._loop(
                AgentEvent("text_delta", {"text": "Hi."}),
                AgentEvent("done", {"end_reason": "finished"}),
            ),
        )
        old.refresh_from_db()
        self.assertEqual(old.state, "failed")
        self.assertEqual(old.end_reason, "lease_lost")
        self.assertEqual(Turn.objects.filter(state="completed").count(), 1)
        self.assertEqual(result.turn_state, "committing")
        self.assertEqual(
            Message.objects.filter(member__User__username="kazi").count(),
            kazi_before + 1,
        )

    @override_settings(KAZI_LITE_PROFILE=False)
    def test_busy_live_claim_saves_one_notice_and_runs_nothing(self):
        old = Turn.objects.create(
            room=self.room, user=self.user, state="running",
            started_at=timezone.now(),
        )
        kazi_before = Message.objects.filter(member__User__username="kazi").count()
        send_chunk = AsyncMock()
        result = self._handle(
            "hello",
            run_loop=self._loop(),
            send_chunk=send_chunk,
        )
        old.refresh_from_db()
        self.assertEqual(old.state, "running")
        self.assertEqual(Turn.objects.count(), 1)
        self.assertFalse(result.persist)
        self.assertEqual(result.turn_id, "")
        self.assertNotIn("could not be recorded", result.full_response)
        self.assertEqual(
            Message.objects.filter(member__User__username="kazi").count(),
            kazi_before + 1,
        )
        sent_text = [call.args[1] for call in send_chunk.call_args_list if call.args]
        self.assertNotIn("still being written", "".join(sent_text))
        finals = [call.args for call in send_chunk.call_args_list if call.args[2]]
        self.assertTrue(finals)
        self.assertEqual(finals[-1][1], "")

    def test_a_lost_claim_keeps_the_old_fail_closed_ack(self):
        Turn.objects.create(
            room=self.room, user=self.user, state="running",
            started_at=timezone.now(),
        )
        # A blocker that disappears between the insert and the read is a lost
        # race, not a live owner: the fail-closed line is still honest.
        with patch(
            "orchestration.turns._active_turn", return_value=None,
        ):
            send_chunk = AsyncMock()
            result = self._handle(
                "hello",
                run_loop=self._loop(),
                send_chunk=send_chunk,
            )
        self.assertIn("could not be recorded", result.full_response)
        sent_text = [call.args[1] for call in send_chunk.call_args_list if call.args]
        self.assertIn("could not be recorded", "".join(sent_text))
        self.assertTrue(send_chunk.call_args_list[-1].args[2])

    def test_approval_decline_and_cancel_end_the_turn_cancelled(self):
        for query in ("no", "cancel"):
            with self.subTest(query=query):
                Turn.objects.all().delete()
                self._paused_turn(end_reason="awaiting_approval")
                self._handle(
                    query,
                    pending=True,
                    run_loop=self._loop(),
                )
                turn = Turn.objects.get()
                self.assertEqual(turn.state, "cancelled")
                self.assertEqual(turn.end_reason, "cancelled")

    def test_budget_decline_ends_the_turn_cancelled(self):
        self._paused_turn(pause_kind="budget", end_reason="asked_for_more")
        self._handle(
            "no",
            budget=True,
            pause_kind="budget",
            run_loop=self._loop(),
        )
        turn = Turn.objects.get()
        self.assertEqual(turn.state, "cancelled")
        self.assertEqual(turn.end_reason, "cancelled")

    def test_a_question_keeps_the_pause_and_starts_a_new_turn(self):
        paused = self._paused_turn(end_reason="awaiting_approval")
        self._handle(
            "hello",
            pending=True,
            run_loop=self._loop(
                AgentEvent("text_delta", {"text": "Hi."}),
                AgentEvent("done", {"end_reason": "finished"}),
            ),
        )
        paused.refresh_from_db()
        # D8: a non-approval message no longer drops the paused action.
        self.assertEqual(paused.state, "paused")
        self.assertEqual(Turn.objects.filter(state="completed").count(), 1)
        self.assertEqual(Turn.objects.count(), 2)

    def test_reset_cancels_the_paused_turn_and_records_its_own(self):
        paused = self._paused_turn(end_reason="awaiting_approval")
        result = self._handle("reset", run_loop=self._loop())
        paused.refresh_from_db()
        self.assertEqual(paused.state, "cancelled")
        self.assertEqual(paused.end_reason, "reset")
        self.assertFalse(result.persist)
        self.assertEqual(Turn.objects.filter(state="completed").count(), 1)

    def test_an_exception_after_the_claim_ends_the_turn_failed(self):
        boom = AsyncMock(side_effect=RuntimeError("boom"))
        with self.assertRaises(RuntimeError):
            self._handle(
                "reset",
                run_loop=self._loop(),
                extra_patches={"orchestration.coordinator.clear_task_state": boom},
            )
        turn = Turn.objects.get()
        self.assertEqual(turn.state, "failed")
        self.assertEqual(turn.end_reason, "error")

    def test_cancellation_after_the_claim_ends_the_turn_terminal(self):
        cancel = AsyncMock(side_effect=asyncio.CancelledError())
        with self.assertRaises(asyncio.CancelledError):
            self._handle(
                "reset",
                run_loop=self._loop(),
                extra_patches={"orchestration.coordinator.clear_task_state": cancel},
            )
        turn = Turn.objects.get()
        self.assertEqual(turn.state, "cancelled")
        self.assertEqual(turn.end_reason, "cancelled")

    def test_cancellation_after_committing_ends_the_turn_terminal(self):
        async def cancel_on_done(_correlation_id, event):
            if event.get("phase") == "done":
                raise asyncio.CancelledError()

        with self.assertRaises(asyncio.CancelledError):
            self._handle(
                "hello",
                run_loop=self._loop(
                    AgentEvent("text_delta", {"text": "Hi."}),
                    AgentEvent("done", {"end_reason": "finished"}),
                ),
                send_step_event=cancel_on_done,
            )
        turn = Turn.objects.get()
        self.assertIn(turn.state, TERMINAL_STATES)
        self.assertEqual(turn.state, "failed")
        self.assertEqual(turn.end_reason, "cancelled")

    def test_finalize_telemetry_names_the_committing_from_state(self):
        result = self._handle(
            "hello",
            finalize=False,
            run_loop=self._loop(
                AgentEvent("text_delta", {"text": "Hi."}),
                AgentEvent("done", {"end_reason": "finished"}),
            ),
        )
        with patch("orchestration.turns.record_event") as telemetry:
            moved = async_to_sync(OrchestrationCoordinator().finalize_turn)(
                result, reply_saved=True,
            )
        self.assertTrue(moved)
        event_name, payload = telemetry.call_args.args
        self.assertEqual(event_name, "turn_transition")
        self.assertEqual(payload["to"], "completed")
        self.assertEqual(payload["from"], "committing")

    def test_room_gone_fails_the_turn(self):
        self._handle(
            "hello",
            run_loop=self._loop(
                AgentEvent("text_delta", {"text": "Hi."}),
                AgentEvent("done", {"end_reason": "finished"}),
            ),
            extra_patches={"orchestration.coordinator._room_exists": lambda room_id: False},
        )
        turn = Turn.objects.get()
        self.assertEqual(turn.state, "failed")
        self.assertEqual(turn.end_reason, "room_gone")

    def test_autopilot_that_cannot_arm_keeps_the_pause_without_a_new_turn(self):
        paused = self._paused_turn(end_reason="awaiting_approval")
        # No coverable shell command, so the window cannot arm. The pause stays
        # on its own Turn; "autopilot" never confirms a mixed batch.
        self._handle(
            "autopilot",
            pending=True,
            run_loop=self._loop(
                AgentEvent("text_delta", {"text": "Hi."}),
                AgentEvent("done", {"end_reason": "finished"}),
            ),
        )
        paused.refresh_from_db()
        self.assertEqual(paused.state, "paused")
        self.assertEqual(Turn.objects.count(), 1)
        self.assertEqual(Turn.objects.filter(state="completed").count(), 0)

    def test_autopilot_arm_failure_holds_the_paused_turn(self):
        paused = self._paused_turn(end_reason="awaiting_approval")
        loop_state = SimpleNamespace(
            pending_tool={"name": "run_command"}, pending_tier="safe",
        )
        self._handle(
            "autopilot",
            pending=True,
            run_loop=self._loop(),
            loop_state=loop_state,
            extra_patches={
                "orchestration.shell.autopilot.arm": MagicMock(return_value=False),
            },
        )
        paused.refresh_from_db()
        self.assertEqual(paused.state, "paused")
        self.assertEqual(paused.end_reason, "awaiting_approval")

    def test_quota_still_blocked_holds_the_paused_turn(self):
        paused = self._paused_turn(pause_kind="quota", end_reason="quota")
        self._handle(
            "continue",
            budget=True,
            pause_kind="quota",
            run_loop=self._loop(),
            extra_patches={
                "orchestration.coordinator._quota_resume_still_blocked": AsyncMock(
                    return_value=TokenQuotaExceeded(120),
                ),
            },
        )
        paused.refresh_from_db()
        self.assertEqual(paused.state, "paused")
        self.assertEqual(paused.end_reason, "quota")

    def test_a_lost_resume_cas_says_already_resumed_elsewhere(self):
        self._paused_turn(end_reason="awaiting_approval")
        send_chunk = AsyncMock()
        self._handle(
            "yes",
            pending=True,
            run_loop=self._loop(),
            send_chunk=send_chunk,
            finalize=False,
            extra_patches={
                "orchestration.coordinator.atransition": AsyncMock(return_value=False),
            },
        )
        texts = [call.args[1] for call in send_chunk.call_args_list if call.args]
        self.assertIn("already resumed elsewhere", "".join(texts))
        self.assertEqual(Turn.objects.get().state, "paused")

    def test_a_resume_clears_the_stale_end_reason(self):
        self._paused_turn(end_reason="awaiting_approval")
        calls = []

        async def _spy(turn_id, expected_version, to_state, **kwargs):
            calls.append((to_state, kwargs))
            return await real_atransition(turn_id, expected_version, to_state, **kwargs)

        self._handle(
            "yes",
            pending=True,
            run_loop=self._loop(),
            resume=self._loop(),
            finalize=False,
            extra_patches={
                "orchestration.coordinator.atransition": AsyncMock(side_effect=_spy),
            },
        )
        running = [kwargs for to_state, kwargs in calls if to_state == "running"]
        self.assertTrue(running)
        self.assertIn("end_reason", running[0])
        self.assertIsNone(running[0]["end_reason"])


class TurnWriteCeilingTests(_CoordinatorTurnBase):
    """The durable record costs four writes: claim, link, commit, finalize."""

    def test_a_completed_turn_costs_exactly_four_writes(self):
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
        self.assertEqual(len(writes), 4)


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

    def test_repeated_safety_blocks_end_safety_stop(self):
        events = self._loop([
            _response([_tool_use("get_weather", {"city": "a"}, "t1")], "tool_use"),
            _response([_tool_use("get_weather", {"city": "b"}, "t2")], "tool_use"),
            _response([_tool_use("get_weather", {"city": "c"}, "t3")], "tool_use"),
        ])
        done = next(e for e in events if e.kind == "done")
        self.assertEqual(done.data["end_reason"], "safety_stop")

    def test_pause_limit_ends_pause_limit(self):
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

    def test_an_unknown_stop_is_not_recorded_as_a_refusal(self):
        events = self._loop([_response([_text("Hm.")], "stop_sequence")])
        done = next(e for e in events if e.kind == "done")
        self.assertEqual(done.data["end_reason"], "unknown_stop")

    def test_confirmation_emits_a_paused_event(self):
        events = self._loop(
            [_response([_tool_use("send_email", {"to": "ops@example.com"}, "t1")], "tool_use")],
            preferences={"approval_overrides": {}},
        )
        self.assertIn("confirmation", [e.kind for e in events])
        paused = [e for e in events if e.kind == "paused"]
        self.assertEqual(len(paused), 1)
        self.assertEqual(paused[0].data["pause_kind"], "approval")


class AgentLoopLengthLimitTests(SimpleTestCase):
    """A cut-off response ends as a length limit, and nothing after the cut runs."""

    def _run(self, responses):
        from orchestration.agent_loop import run_agent_loop

        mock_llm = MagicMock()
        mock_llm.default_provider.return_value = "anthropic"
        mock_llm.create_message = AsyncMock(side_effect=responses)
        mock_cache = MagicMock()
        mock_cache.get.return_value = None
        execute_tool = AsyncMock(return_value={"status": "success"})
        with (
            patch("orchestration.agent_loop.get_llm_client", return_value=mock_llm),
            patch("orchestration.agent_loop.cache", new=mock_cache),
            patch("orchestration.agent_loop._resolve_persona", new=AsyncMock(return_value=None)),
            patch("orchestration.agent_loop.execute_tool", new=execute_tool),
            patch("orchestration.agent_loop.preview_tool", new=AsyncMock(return_value=None)),
            patch("orchestration.agent_loop.save_pending_confirmation", new=AsyncMock()),
            patch("orchestration.agent_loop._record_receipt", new=AsyncMock()),
            patch("orchestration.agent_loop.record_event"),
        ):
            events = _collect(run_agent_loop(
                user_message="write a long file",
                context={"user_id": None, "room_id": 1, "username": "test"},
                preferences={},
            ))
        return events, execute_tool

    def test_max_tokens_ends_length_limit_with_a_partial_note(self):
        events, _ = self._run([
            _response([_text("Here is the first part")], "max_tokens"),
        ])
        done = next(e for e in events if e.kind == "done")
        self.assertEqual(done.data["end_reason"], "length_limit")

        messages = [e.data["message"] for e in events if e.kind == "error"]
        self.assertEqual(messages, [
            "My response was cut short by the length limit. "
            "Anything I was still writing did not run.",
        ])
        self.assertNotIn("error", messages[0].lower())

    def test_a_tool_call_in_a_cut_off_response_is_not_dispatched(self):
        events, execute_tool = self._run([
            _response([
                _text("Writing the file now"),
                _tool_use("run_command", {"command": "echo hi"}, "t1"),
            ], "max_tokens"),
        ])
        done = next(e for e in events if e.kind == "done")
        self.assertEqual(done.data["end_reason"], "length_limit")
        execute_tool.assert_not_called()
