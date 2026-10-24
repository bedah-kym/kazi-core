"""#216b-1: turn leases, the self-fence, one reap routine and saved endings."""
from __future__ import annotations

import asyncio
import base64
import json
from contextlib import ExitStack
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from asgiref.sync import async_to_sync
from django.contrib.auth import get_user_model
from django.test import SimpleTestCase, TransactionTestCase
from django.utils import timezone

from chatbot.kazi_messages import room_cipher
from chatbot.models import Chatroom, Member
from orchestration import turn_leases
from orchestration.agent_loop import (
    AgentEvent,
    HARD_TURN_TIMEOUT_SECONDS,
    LoopState,
    _run_sub_agent,
    run_agent_loop,
)
from orchestration.coordinator import OrchestrationCoordinator
from orchestration.models import Turn
from orchestration.turn_leases import (
    beat_once,
    live_turn_ids,
    register_live_turn,
    turn_live,
    unregister_live_turn,
)
from orchestration.turns import (
    BUSY_LIVE,
    claim_turn,
    reap_boot_rows,
    reap_lapsed_turns,
    reap_turn,
)
from tests.turn_support import turn_test_patches

User = get_user_model()


def _run(coro):
    return asyncio.run(coro)


def _collect(gen):
    async def _inner():
        return [event async for event in gen]

    return _run(_inner())


def _tool_use(name, tool_input, tool_id="t1"):
    return {"type": "tool_use", "id": tool_id, "name": name, "input": tool_input}


def _response(blocks, stop_reason):
    return {
        "content": blocks,
        "stop_reason": stop_reason,
        "usage": {"input_tokens": 5, "output_tokens": 5},
    }


def _kazi_payloads(room):
    cipher = room_cipher(room)
    for message in room.chats.filter(member__User__username="kazi"):
        envelope = json.loads(message.content)
        nonce = base64.b64decode(envelope["nonce"])
        data = base64.b64decode(envelope["data"])
        yield json.loads(cipher.decrypt(nonce, data, None).decode("utf-8"))


class _TurnLeaseBase(TransactionTestCase):
    def setUp(self):
        super().setUp()
        self.user = User.objects.create_user(
            username="lease-user", password="fake-token",  # nosec B106
        )
        self.member, _ = Member.objects.get_or_create(User=self.user)
        self.room = Chatroom.objects.create()
        self.room.participants.add(self.member)
        turn_leases._LIVE_TURNS.clear()
        turn_leases._FENCED_TURNS.clear()
        turn_leases._LAST_RENEWAL.clear()
        layer = MagicMock()
        layer.group_send = AsyncMock()
        patcher = patch(
            "chatbot.kazi_messages.get_channel_layer", return_value=layer,
        )
        patcher.start()
        self.addCleanup(patcher.stop)

    def tearDown(self):
        turn_leases._LIVE_TURNS.clear()
        turn_leases._FENCED_TURNS.clear()
        turn_leases._LAST_RENEWAL.clear()
        super().tearDown()

    def _running(
        self, *, state="running", lease_delta=-1, started_at=None,
        owner="another-worker", user=None,
    ):
        return Turn.objects.create(
            room=self.room,
            user=user or self.user,
            state=state,
            started_at=started_at or timezone.now(),
            owner_id=owner,
            lease_expires_at=timezone.now() + timedelta(seconds=lease_delta),
        )


class ReapTests(_TurnLeaseBase):
    def test_a_lapsed_running_row_is_taken_over_with_one_notice(self):
        old = self._running()
        claim = claim_turn(room_id=self.room.id, user_id=self.user.id)

        self.assertEqual(claim.status, "took_over")
        old.refresh_from_db()
        self.assertEqual(old.state, "failed")
        self.assertEqual(old.end_reason, "lease_lost")
        self.assertTrue(old.partial)
        self.assertIsNotNone(claim.turn)
        notices = [
            payload for payload in _kazi_payloads(self.room)
            if payload.get("origin") == "turn_notice"
        ]
        self.assertEqual(len(notices), 1)
        self.assertIn("server restarted", notices[0]["content"])

    def test_the_reaper_ends_one_lapsed_running_turn_and_leaves_paused(self):
        lapsed = self._running()
        paused = Turn.objects.create(
            room=self.room, user=self.user, state="paused",
            started_at=timezone.now() - timedelta(days=2),
            end_reason="awaiting_approval",
        )

        result = reap_lapsed_turns()

        self.assertEqual(result["reaped"], 1)
        lapsed.refresh_from_db()
        paused.refresh_from_db()
        self.assertEqual(lapsed.state, "failed")
        self.assertEqual(lapsed.end_reason, "lease_lost")
        self.assertEqual(paused.state, "paused")
        self.assertEqual(paused.end_reason, "awaiting_approval")
        notices = [
            payload for payload in _kazi_payloads(self.room)
            if payload.get("origin") == "turn_notice"
        ]
        self.assertEqual(len(notices), 1)

    def test_a_lapsed_committing_turn_with_a_saved_reply_completes(self):
        from chatbot.kazi_messages import persist_kazi_message

        turn = self._running(state="committing")
        persist_kazi_message(
            self.room, content="done", origin="turn", turn=turn,
            model_text="done", broadcast=False,
        )

        self.assertTrue(reap_turn(turn, require_lapsed=True))

        turn.refresh_from_db()
        self.assertEqual(turn.state, "completed")
        self.assertIsNone(turn.lease_expires_at)
        self.assertIsNone(turn.owner_id)

    def test_a_row_renewed_between_the_read_and_the_update_is_not_reaped(self):
        turn = self._running()
        Turn.objects.filter(pk=turn.pk).update(
            lease_expires_at=timezone.now() + timedelta(seconds=90),
        )

        self.assertFalse(reap_turn(turn, require_lapsed=True))

        turn.refresh_from_db()
        self.assertEqual(turn.state, "running")

    def test_a_queued_row_created_before_the_dead_lease_expires(self):
        old = self._running()
        queued = Turn.objects.create(
            room=self.room, user=self.user, state="queued",
        )
        Turn.objects.filter(pk=queued.pk).update(
            created_at=timezone.now() - timedelta(minutes=5),
        )

        self.assertTrue(reap_turn(old, require_lapsed=True))

        queued.refresh_from_db()
        self.assertEqual(queued.state, "expired")
        self.assertEqual(queued.end_reason, "restart")
        notices = [
            payload for payload in _kazi_payloads(self.room)
            if payload.get("origin") == "turn_notice"
        ]
        self.assertEqual(len(notices), 1)
        self.assertIn("1 waiting message(s)", notices[0]["content"])

    def test_the_lite_boot_sweep_ends_rows_from_another_boot(self):
        old = self._running(lease_delta=90, owner="previous-boot")
        queued = Turn.objects.create(
            room=self.room, user=self.user, state="queued",
        )

        result = reap_boot_rows()

        self.assertEqual(result["reaped"], 1)
        old.refresh_from_db()
        queued.refresh_from_db()
        self.assertEqual(old.state, "failed")
        self.assertEqual(queued.state, "expired")

    def test_lite_with_a_fresh_lease_is_still_busy(self):
        # The old lite shortcut treated any blocker as stale; it is gone.
        self._running(lease_delta=90, owner="another-worker")

        with self.settings(KAZI_LITE_PROFILE=True):
            claim = claim_turn(room_id=self.room.id, user_id=self.user.id)

        self.assertEqual(claim.status, BUSY_LIVE)
        self.assertIsNone(claim.turn)

    def test_a_reply_from_before_the_resume_does_not_count_as_saved(self):
        # M1: the paused segment's "Please confirm…" is not this segment's
        # reply. The resumed committing row must fail with a notice.
        from chatbot.kazi_messages import persist_kazi_message
        from chatbot.models import Message

        turn = self._running(state="committing")
        confirm = persist_kazi_message(
            self.room, content="Please confirm.", origin="turn", turn=turn,
            model_text="Please confirm.", broadcast=False,
        )
        Message.objects.filter(pk=confirm.pk).update(
            timestamp=timezone.now() - timedelta(minutes=10),
        )
        resume = Message.objects.create(
            member=self.member,
            content="yes",
            timestamp=timezone.now() - timedelta(minutes=1),
            turn=turn,
        )
        self.room.chats.add(resume)

        self.assertTrue(reap_turn(turn, require_lapsed=True))

        turn.refresh_from_db()
        self.assertEqual(turn.state, "failed")
        self.assertEqual(turn.end_reason, "lease_lost")
        notices = [
            payload for payload in _kazi_payloads(self.room)
            if payload.get("origin") == "turn_notice"
        ]
        self.assertEqual(len(notices), 1)
        self.assertIn("server restarted", notices[0]["content"])

    def test_a_completed_reap_with_dropped_queue_only_mentions_the_queue(self):
        from chatbot.kazi_messages import persist_kazi_message

        turn = self._running(state="committing")
        persist_kazi_message(
            self.room, content="done", origin="turn", turn=turn,
            model_text="done", broadcast=False,
        )
        queued = Turn.objects.create(
            room=self.room, user=self.user, state="queued",
        )
        Turn.objects.filter(pk=queued.pk).update(
            created_at=timezone.now() - timedelta(minutes=5),
        )

        self.assertTrue(reap_turn(turn, require_lapsed=True))

        turn.refresh_from_db()
        self.assertEqual(turn.state, "completed")
        notices = [
            payload for payload in _kazi_payloads(self.room)
            if payload.get("origin") == "turn_notice"
        ]
        self.assertEqual(len(notices), 1)
        self.assertIn("1 waiting message(s)", notices[0]["content"])
        self.assertNotIn("stopped partway", notices[0]["content"])


class HeartbeatTests(_TurnLeaseBase):
    def test_the_beat_advances_the_lease_for_a_registry_turn(self):
        turn = self._running(
            lease_delta=-5, owner=turn_leases.PROCESS_OWNER_ID,
        )
        register_live_turn(str(turn.pk))

        renewed = beat_once(monotonic_now=1000.0)

        self.assertEqual(renewed, 1)
        turn.refresh_from_db()
        self.assertGreater(turn.lease_expires_at, timezone.now())
        self.assertEqual(turn.version, 0)
        self.assertEqual(turn.owner_id, turn_leases.PROCESS_OWNER_ID)
        unregister_live_turn(str(turn.pk))

    def test_the_beat_never_renews_a_leaked_row(self):
        leaked = self._running(lease_delta=-5, owner="dead-worker")
        live = self._running(
            lease_delta=-5, owner=turn_leases.PROCESS_OWNER_ID,
            user=User.objects.create_user(
                username="lease-user-2", password="fake-token",  # nosec B106
            ),
        )
        register_live_turn(str(live.pk))

        beat_once(monotonic_now=1000.0)

        leaked.refresh_from_db()
        live.refresh_from_db()
        self.assertLess(leaked.lease_expires_at, timezone.now())
        self.assertGreater(live.lease_expires_at, timezone.now())
        unregister_live_turn(str(live.pk))

    def test_the_first_segment_cannot_renew_forever(self):
        old = self._running(
            lease_delta=-5, owner=turn_leases.PROCESS_OWNER_ID,
        )
        register_live_turn(str(old.pk))
        # This segment has been running past the hard timeout.
        with turn_leases._REGISTRY_LOCK:
            turn_leases._LIVE_TURNS[str(old.pk)] = (
                1000.0 - HARD_TURN_TIMEOUT_SECONDS - 120
            )

        self.assertEqual(beat_once(monotonic_now=1000.0), 0)

        old.refresh_from_db()
        self.assertLess(old.lease_expires_at, timezone.now())
        self.assertFalse(turn_live(str(old.pk)))
        unregister_live_turn(str(old.pk))

    def test_a_turn_resumed_long_after_its_first_start_keeps_its_lease(self):
        # H1: the cap is per run segment, never the turn's first started_at.
        resumed = self._running(
            lease_delta=-5, owner=turn_leases.PROCESS_OWNER_ID,
            started_at=timezone.now() - timedelta(minutes=45),
        )
        register_live_turn(str(resumed.pk))

        self.assertEqual(beat_once(monotonic_now=1000.0), 1)

        resumed.refresh_from_db()
        self.assertGreater(resumed.lease_expires_at, timezone.now())
        with patch.object(turn_leases, "_MONOTONIC", return_value=1000.0):
            self.assertTrue(turn_live(str(resumed.pk)))
        unregister_live_turn(str(resumed.pk))

    def test_a_row_taken_over_by_another_owner_is_not_renewed(self):
        # Item 5: renewal never steals a row another worker now owns.
        stolen = self._running(lease_delta=-5, owner="another-worker")
        register_live_turn(str(stolen.pk))

        self.assertEqual(beat_once(monotonic_now=1000.0), 0)

        stolen.refresh_from_db()
        self.assertLess(stolen.lease_expires_at, timezone.now())
        self.assertFalse(turn_live(str(stolen.pk)))
        unregister_live_turn(str(stolen.pk))

    def test_another_turns_registration_does_not_refresh_a_failing_turn(self):
        # H2: the fence clock is per turn. A's failed beats must not be
        # forgiven when B registers in the same process.
        with patch.object(turn_leases, "_MONOTONIC", return_value=1000.0):
            a = self._running(
                lease_delta=90, owner=turn_leases.PROCESS_OWNER_ID,
            )
            register_live_turn(str(a.pk))

        # A never renews; B registers later in the same process.
        with patch.object(turn_leases, "_MONOTONIC", return_value=2000.0):
            register_live_turn("turn-b")
            self.assertFalse(turn_live(str(a.pk)))
            self.assertTrue(turn_live("turn-b"))
        unregister_live_turn(str(a.pk))
        unregister_live_turn("turn-b")

    def test_a_fake_clock_fences_the_owner_before_the_lease_lapses(self):
        turn = self._running(lease_delta=90, owner=turn_leases.PROCESS_OWNER_ID)
        register_live_turn(str(turn.pk))
        beat_once(monotonic_now=1000.0)

        budget = (
            turn_leases.lease_seconds() - turn_leases.fence_margin_seconds()
        )
        with patch.object(
            turn_leases, "_MONOTONIC", return_value=1000.0 + budget,
        ):
            self.assertTrue(turn_live(str(turn.pk)))
        with patch.object(
            turn_leases, "_MONOTONIC", return_value=1000.0 + budget + 0.1,
        ):
            self.assertFalse(turn_live(str(turn.pk)))

    def test_the_coordinator_passes_a_turn_live_check_into_the_loop(self):
        seen = {}

        async def _fake_loop(**kwargs):
            seen.update(kwargs["context"])
            seen["live_during_loop"] = kwargs["context"]["turn_live"]()
            if False:
                yield AgentEvent("done", {})

        patches = {
            "orchestration.coordinator.load_memory_summary": AsyncMock(return_value=None),
            "orchestration.coordinator.get_user_preferences": lambda *a, **k: {},
            "orchestration.coordinator.has_pending_agent_state": AsyncMock(return_value=False),
            "orchestration.coordinator.has_expired_pause": AsyncMock(return_value=False),
            "orchestration.coordinator.run_agent_loop": _fake_loop,
            "orchestration.coordinator.record_event": MagicMock(),
            "orchestration.coordinator.cache": MagicMock(),
            **turn_test_patches(),
        }
        with ExitStack() as stack:
            for target, value in patches.items():
                stack.enter_context(patch(target, new=value))
            async_to_sync(OrchestrationCoordinator().handle_message)(
                query="hello",
                user_id=1,
                room_id="1",
                username="alice",
                message_id=42,
                send_chunk=AsyncMock(),
                send_step_event=AsyncMock(),
                get_context_prompt=AsyncMock(return_value=""),
                bump_signals=MagicMock(),
            )

        self.assertTrue(callable(seen.get("turn_live")))
        self.assertTrue(seen.get("live_during_loop"))
        self.assertFalse(turn_live("00000000-0000-0000-0000-00000000dead"))


class AgentLoopFenceTests(SimpleTestCase):
    def _loop(self, responses, *, context, patches=None, resumed_state=None,
              confirmed_tool=False):
        mock_llm = MagicMock()
        mock_llm.return_value = mock_llm
        mock_llm.default_provider.return_value = "anthropic"
        side_effect = responses[0] if isinstance(responses, list) and len(responses) == 1 else responses
        mock_llm.create_message = AsyncMock(side_effect=side_effect)
        mock_cache = MagicMock()
        mock_cache.get.return_value = None
        defaults = {
            "orchestration.agent_loop.get_llm_client": mock_llm,
            "orchestration.agent_loop.cache": mock_cache,
            "orchestration.agent_loop._resolve_persona": AsyncMock(return_value=None),
            "orchestration.agent_loop._record_receipt": AsyncMock(),
            "orchestration.agent_loop._circuit_breaker_open": MagicMock(return_value=False),
            "orchestration.agent_loop.record_event": MagicMock(),
        }
        defaults.update(patches or {})
        with ExitStack() as stack:
            for target, value in defaults.items():
                stack.enter_context(patch(target, new=value))
            return _collect(run_agent_loop(
                user_message="do it", context=context, preferences={},
                resumed_state=resumed_state, confirmed_tool=confirmed_tool,
            ))

    def _lease_clock(self, turn_id="turn-1"):
        clock = {"t": 1000.0}
        turn_leases._LIVE_TURNS[turn_id] = clock["t"]
        turn_leases._LAST_RENEWAL[turn_id] = clock["t"]
        self.addCleanup(turn_leases._LIVE_TURNS.pop, turn_id, None)
        self.addCleanup(turn_leases._FENCED_TURNS.discard, turn_id)
        self.addCleanup(turn_leases._LAST_RENEWAL.pop, turn_id, None)
        return clock

    def test_a_fenced_owner_dispatches_no_further_batch_and_keeps_receipts(self):
        clock = self._lease_clock()
        calls = []

        async def _execute(name, tool_input, _context):
            calls.append(name)
            return {"status": "success", "result": "ok"}

        calls_to_llm = {"n": 0}

        async def _create(**_kwargs):
            calls_to_llm["n"] += 1
            if calls_to_llm["n"] == 1:
                return _response(
                    [_tool_use("get_weather", {"city": "a"}, "t1")], "tool_use",
                )
            # The lease lapses while the second model call is in flight.
            clock["t"] = (
                1000.0 + turn_leases.lease_seconds()
                - turn_leases.fence_margin_seconds() + 0.1
            )
            return _response(
                [_tool_use("get_weather", {"city": "b"}, "t2")], "tool_use",
            )

        receipts = AsyncMock()
        with patch.object(
            turn_leases, "_MONOTONIC", side_effect=lambda: clock["t"],
        ):
            events = self._loop(
                [_create],
                context={
                    "user_id": None, "room_id": 1, "username": "test",
                    "turn_id": "turn-1",
                    "turn_live": lambda: turn_live("turn-1"),
                },
                patches={
                    "orchestration.agent_loop.execute_tool": AsyncMock(side_effect=_execute),
                    "orchestration.agent_loop.preview_tool": AsyncMock(return_value=None),
                    "orchestration.agent_loop._record_receipt": receipts,
                },
            )

        self.assertEqual(calls, ["get_weather"])
        self.assertEqual(receipts.await_count, 1)
        self.assertEqual(receipts.await_args.args[0], "get_weather")
        done = next(event for event in events if event.kind == "done")
        self.assertEqual(done.data["end_reason"], "lease_lost")
        harness = "".join(
            event.data.get("text", "")
            for event in events if event.kind == "harness"
        )
        self.assertIn("marked interrupted", harness)

    def test_a_fenced_owner_does_not_supersede_a_pause_or_save_approval(self):
        clock = self._lease_clock()

        async def _create(**_kwargs):
            return _response(
                [_tool_use("send_email", {"to": "ops@example.com"}, "t1")],
                "tool_use",
            )

        async def _preview(*_args, **_kwargs):
            clock["t"] = (
                1000.0 + turn_leases.lease_seconds()
                - turn_leases.fence_margin_seconds() + 0.1
            )
            return None

        supersede = MagicMock(return_value=[])
        save = AsyncMock()
        with patch.object(
            turn_leases, "_MONOTONIC", side_effect=lambda: clock["t"],
        ):
            events = self._loop(
                [_create],
                context={
                    "user_id": None, "room_id": 1, "username": "test",
                    "turn_id": "turn-1",
                    "turn_live": lambda: turn_live("turn-1"),
                },
                patches={
                    "orchestration.agent_loop.execute_tool": AsyncMock(
                        return_value={"status": "success"},
                    ),
                    "orchestration.agent_loop.preview_tool": AsyncMock(side_effect=_preview),
                    "orchestration.agent_loop._supersede_older_pause_sync": supersede,
                    "orchestration.agent_loop.save_pending_confirmation": save,
                },
            )

        supersede.assert_not_called()
        save.assert_not_called()
        self.assertNotIn("confirmation", [event.kind for event in events])
        done = next(event for event in events if event.kind == "done")
        self.assertEqual(done.data["end_reason"], "lease_lost")

    def test_a_raising_turn_live_hook_stops_the_loop(self):
        def _boom():
            raise RuntimeError("the lease store is down")

        events = self._loop(
            [AsyncMock(side_effect=AssertionError("the model must not be called"))],
            context={
                "user_id": None, "room_id": 1, "username": "test",
                "turn_id": "turn-x", "turn_live": _boom,
            },
        )

        done = next(event for event in events if event.kind == "done")
        self.assertEqual(done.data["end_reason"], "lease_lost")
        self.assertFalse(any(event.kind == "tool_result" for event in events))

    def test_a_turn_context_without_a_hook_fails_closed(self):
        events = self._loop(
            [AsyncMock(side_effect=AssertionError("the model must not be called"))],
            context={
                "user_id": None, "room_id": 1, "username": "test",
                "turn_id": "turn-x",
            },
        )

        done = next(event for event in events if event.kind == "done")
        self.assertEqual(done.data["end_reason"], "lease_lost")

    def test_a_fenced_resumed_held_call_yields_one_note_and_runs_nothing(self):
        # Drives the real turn_live through the resumed held-call loop.
        self._lease_clock("turn-held")
        state = LoopState(
            messages=[],
            pending_tools=[{
                "id": "t1", "name": "get_weather", "input": {"city": "a"},
            }],
        )
        execute = AsyncMock()
        fence_at = (
            1000.0 + turn_leases.lease_seconds()
            - turn_leases.fence_margin_seconds() + 0.1
        )
        with patch.object(turn_leases, "_MONOTONIC", return_value=fence_at):
            events = self._loop(
                [AsyncMock(side_effect=AssertionError("no model call"))],
                context={
                    "user_id": None, "room_id": 1, "username": "test",
                    "turn_id": "turn-held",
                    "turn_live": lambda: turn_live("turn-held"),
                },
                patches={
                    "orchestration.agent_loop.execute_tool": execute,
                    "orchestration.agent_loop._record_receipt": AsyncMock(),
                },
                resumed_state=state, confirmed_tool=True,
            )

        execute.assert_not_called()
        notes = [
            event.data.get("text", "")
            for event in events if event.kind == "harness"
        ]
        self.assertEqual(
            sum("marked interrupted" in text for text in notes), 1,
        )
        done = next(event for event in events if event.kind == "done")
        self.assertEqual(done.data["end_reason"], "lease_lost")


class SubAgentFenceTests(SimpleTestCase):
    def test_a_fence_during_a_delegate_stops_further_child_dispatch(self):
        calls = []

        async def _execute(name, _tool_input, _context):
            calls.append(name)
            return {"status": "success"}

        # Loop-top check, then the first tool: both live. The second tool is
        # dispatched after the fence trips.
        checks = {"n": 0}

        def _turn_live():
            checks["n"] += 1
            return checks["n"] <= 2

        llm = MagicMock()
        llm.create_message = AsyncMock(return_value=_response([
            _tool_use("get_weather", {"city": "a"}, "t1"),
            _tool_use("get_weather", {"city": "b"}, "t2"),
        ], "tool_use"))

        with patch(
            "orchestration.agent_loop.get_llm_client", return_value=llm,
        ), patch(
            "orchestration.agent_loop._resolve_persona",
            new=AsyncMock(return_value=None),
        ), patch(
            "orchestration.agent_loop._execute_with_timeout",
            new=AsyncMock(side_effect=_execute),
        ), patch(
            "orchestration.agent_loop._record_receipt", new=AsyncMock(),
        ), patch(
            "orchestration.agent_loop.record_event",
        ):
            result = _run(_run_sub_agent(
                {"task": "check the weather"},
                {
                    "user_id": 1, "room_id": 2, "turn_id": "turn-1",
                    "turn_live": _turn_live,
                },
                None, "system", [],
            ))

        self.assertEqual(calls, ["get_weather"])
        self.assertEqual(result["stopped_reason"], "lease_lost")


class ConsumerBackstopTests(_TurnLeaseBase):
    def test_the_running_row_backstop_unregisters_the_turn(self):
        from chatbot.consumers import ChatConsumer

        turn = self._running(owner=turn_leases.PROCESS_OWNER_ID)
        register_live_turn(str(turn.pk))
        result = SimpleNamespace(turn_id=str(turn.pk), turn_version=turn.version)

        _run(ChatConsumer()._fail_running_turn(result))

        turn.refresh_from_db()
        self.assertEqual(turn.state, "failed")
        self.assertEqual(turn.end_reason, "error")
        self.assertNotIn(str(turn.pk), live_turn_ids())


class LostAdvanceTests(SimpleTestCase):
    def test_a_lost_advance_write_unregisters_the_turn(self):
        turn_id = "00000000-0000-0000-0000-000000000001"

        async def _fake_loop(**_kwargs):
            yield AgentEvent("done", {"end_reason": "finished"})

        patches = {
            "orchestration.coordinator.load_memory_summary": AsyncMock(return_value=None),
            "orchestration.coordinator.get_user_preferences": lambda *a, **k: {},
            "orchestration.coordinator.has_pending_agent_state": AsyncMock(return_value=False),
            "orchestration.coordinator.has_expired_pause": AsyncMock(return_value=False),
            "orchestration.coordinator.run_agent_loop": _fake_loop,
            "orchestration.coordinator.record_event": MagicMock(),
            "orchestration.coordinator.cache": MagicMock(),
            **turn_test_patches(),
        }
        patches["orchestration.coordinator.atransition"] = AsyncMock(
            return_value=False,
        )
        patches["orchestration.coordinator.aget_turn"] = AsyncMock(
            return_value=None,
        )
        with ExitStack() as stack:
            for target, value in patches.items():
                stack.enter_context(patch(target, new=value))
            async_to_sync(OrchestrationCoordinator().handle_message)(
                query="hello",
                user_id=1,
                room_id="1",
                username="alice",
                message_id=42,
                send_chunk=AsyncMock(),
                send_step_event=AsyncMock(),
                get_context_prompt=AsyncMock(return_value=""),
                bump_signals=MagicMock(),
            )

        self.assertNotIn(turn_id, live_turn_ids())


class DirectiveOwnershipTests(SimpleTestCase):
    def _run(self, query, *, claim_status=BUSY_LIVE, patches=None):
        async def _empty_loop(**_kwargs):
            if False:
                yield AgentEvent("done", {})

        defaults = {
            "orchestration.coordinator.load_memory_summary": AsyncMock(return_value=None),
            "orchestration.coordinator.get_user_preferences": lambda *a, **k: {},
            "orchestration.coordinator.has_pending_agent_state": AsyncMock(return_value=False),
            "orchestration.coordinator.has_expired_pause": AsyncMock(return_value=False),
            "orchestration.coordinator.run_agent_loop": _empty_loop,
            "orchestration.coordinator.record_event": MagicMock(),
            "orchestration.coordinator.cache": MagicMock(),
            "orchestration.coordinator._persist_turn_notice": MagicMock(return_value=True),
            **turn_test_patches(),
        }
        defaults["orchestration.coordinator.aclaim_turn"] = AsyncMock(
            return_value=SimpleNamespace(status=claim_status, turn=None),
        )
        defaults.update(patches or {})
        with ExitStack() as stack:
            for target, value in defaults.items():
                stack.enter_context(patch(target, new=value))
            async_to_sync(OrchestrationCoordinator().handle_message)(
                query=query,
                user_id=1,
                room_id="1",
                username="alice",
                message_id=42,
                send_chunk=AsyncMock(),
                send_step_event=AsyncMock(),
                get_context_prompt=AsyncMock(return_value=""),
                bump_signals=MagicMock(),
            )

    def test_reset_while_busy_does_not_clear_memory(self):
        clear = AsyncMock()
        self._run(
            "reset",
            patches={"orchestration.coordinator.clear_memory": clear},
        )
        clear.assert_not_called()

    def test_undo_while_busy_does_not_run_and_saves_a_notice(self):
        undo = MagicMock(return_value={"message": "undone"})
        notice = MagicMock(return_value=True)
        self._run(
            "undo",
            patches={
                "orchestration.coordinator.undo_last_action": undo,
                "orchestration.coordinator._persist_turn_notice": notice,
            },
        )
        undo.assert_not_called()
        notice.assert_called_once()

    def test_allowing_a_host_while_busy_does_not_grant(self):
        grant = MagicMock(return_value="example.com")
        self._run(
            "allow host example.com",
            patches={"orchestration.shell.host_grants.grant": grant},
        )
        grant.assert_not_called()

    def test_disarm_while_busy_still_disarms(self):
        disarm = MagicMock(return_value=True)
        self._run(
            "autopilot off",
            patches={"orchestration.shell.autopilot.disarm": disarm},
        )
        disarm.assert_called_once()

    def test_revoking_a_host_while_busy_still_revokes(self):
        revoke = MagicMock(return_value=True)
        self._run(
            "revoke host example.com",
            patches={"orchestration.shell.host_grants.revoke": revoke},
        )
        revoke.assert_called_once()
