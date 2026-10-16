"""T-F3b: the hourly token quota counts work tokens over a real rolling hour.

Acceptance tests for the quota fix. They fail on the T-F3 base: there the quota
estimates the whole resent prompt on every call, stores one counter whose TTL is
refreshed on every write (so an active user's usage never ages out), defaults to
half a turn's budget, and refuses with a message that names no time.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

from django.contrib.auth import get_user_model
from django.conf import settings
from django.core.cache import cache
from django.test import SimpleTestCase, TransactionTestCase, override_settings

from orchestration.agent_loop import (
    DEFAULT_TURN_TOKEN_BUDGET,
    load_loop_state,
    run_agent_loop,
)
from orchestration.llm_client import (
    LLMClient,
    QUOTA_BUCKET_SECONDS,
    QUOTA_WINDOW_SECONDS,
    TokenQuotaExceeded,
    _read_hourly_usage,
)
from orchestration.test_agentic_scenarios import (
    _make_llm_response,
    _text_block,
    _tool_use_block,
    collect_events,
    run_async,
)

User = get_user_model()


class WorkTokenWindowTests(SimpleTestCase):
    """The quota counts work, not the resent prefix, and ages out over an hour."""

    def setUp(self):
        cache.clear()
        self.addCleanup(cache.clear)
        self.client = LLMClient()

    @override_settings(LLM_TOKEN_QUOTA_ENABLED=False)
    def test_create_message_does_not_charge_the_resent_prefix(self):
        # A single-shot create_message call never charges by itself: the agent
        # loop records the work it measured. So a huge resent prompt leaves the
        # hour's usage at zero.
        self.client.hf_key = "test-key"  # nosec B105 - test fixture
        big_messages = [{"role": "user", "content": "x" * 40000}]
        with patch.object(
            self.client, "_call_huggingface", new=AsyncMock(return_value="ok"),
        ):
            run_async(self.client.create_message(
                messages=big_messages, system="system", provider="huggingface",
                user_id=4242,
            ))
        used, _ = _read_hourly_usage(4242)
        self.assertEqual(used, 0)

        self.client.record_work_tokens(42, 4242)
        used, _ = _read_hourly_usage(4242)
        self.assertEqual(used, 42)

    def test_usage_older_than_an_hour_stops_counting(self):
        t = 1_000_000.0
        self.client._record_token_usage(1000, 77, now=t)
        used, _ = _read_hourly_usage(77, now=t)
        self.assertEqual(used, 1000)

        # The user keeps calling; a later call is added to the same hour.
        self.client._record_token_usage(500, 77, now=t + 600)
        used, _ = _read_hourly_usage(77, now=t + 600)
        self.assertEqual(used, 1500)

        # Once the first call's bucket leaves the window it stops counting,
        # even though the user has been active the whole time.
        later = t + QUOTA_WINDOW_SECONDS + QUOTA_BUCKET_SECONDS + 1
        used, _ = _read_hourly_usage(77, now=later)
        self.assertEqual(used, 500)

        much_later = t + 2 * QUOTA_WINDOW_SECONDS + QUOTA_BUCKET_SECONDS
        used, _ = _read_hourly_usage(77, now=much_later)
        self.assertEqual(used, 0)

    def test_default_limit_is_at_least_twice_the_turn_budget(self):
        self.assertGreaterEqual(
            settings.LLM_TOKEN_LIMIT_PER_USER_PER_HOUR,
            2 * DEFAULT_TURN_TOKEN_BUDGET,
        )

    def test_a_blocked_call_says_when_capacity_frees_up(self):
        exc = TokenQuotaExceeded(1800, reason="limit")
        self.assertIn("frees up", str(exc))
        self.assertIn("minute", str(exc))

    def test_read_hourly_usage_raises_when_the_cache_cannot_be_read(self):
        with patch(
            "orchestration.llm_client.cache.get_many",
            side_effect=RuntimeError("cache down"),
        ):
            with self.assertRaises(RuntimeError):
                _read_hourly_usage(4242)

    def test_unreadable_usage_fails_closed(self):
        with patch.object(
            self.client, "_get_user_token_budget",
            new=AsyncMock(
                return_value={"limit": 100, "used": None, "exempt": False},
            ),
        ):
            with self.assertRaises(TokenQuotaExceeded) as ctx:
                run_async(self.client._check_token_quota(0, 4242))
        self.assertEqual(ctx.exception.reason, "unreadable")


class QuotaEnforcementTests(TransactionTestCase):
    """The check refuses a full hour and exempts staff (T-F3b)."""

    def setUp(self):
        cache.clear()
        self.addCleanup(cache.clear)
        self.client = LLMClient()

    def test_blocked_call_reports_when_capacity_frees_up(self):
        user = User.objects.create_user(
            username="quota-blocked", email="qb@example.com", password="secret",  # nosec B106 - test fixture
        )
        self.client._record_token_usage(250000, user.pk)
        with self.assertRaises(TokenQuotaExceeded) as ctx:
            run_async(self.client._check_token_quota(0, user.pk))
        self.assertEqual(ctx.exception.reason, "limit")
        self.assertGreaterEqual(ctx.exception.retry_after_seconds, 0)
        self.assertLessEqual(ctx.exception.retry_after_seconds, QUOTA_WINDOW_SECONDS)
        self.assertIn("frees up", str(ctx.exception))
        self.assertIn("hour", str(ctx.exception).lower())

    def test_staff_are_exempt_from_the_hourly_quota(self):
        user = User.objects.create_user(
            username="quota-regular", email="qr@example.com", password="secret",  # nosec B106 - test fixture
        )
        self.client._record_token_usage(300000, user.pk)
        with self.assertRaises(TokenQuotaExceeded):
            run_async(self.client._check_token_quota(0, user.pk))

        staff = User.objects.create_superuser(
            username="quota-staff", email="qs@example.com", password="secret",  # nosec B106 - test fixture
        )
        self.client._record_token_usage(300000, staff.pk)
        # Does not raise: staff are exempt even when over the limit.
        run_async(self.client._check_token_quota(0, staff.pk))


class LoopRecordsWorkTokensTests(SimpleTestCase):
    """The loop charges its measured work, never the resent prefix."""

    def setUp(self):
        from orchestration.agent_loop import reset_circuit_breakers

        reset_circuit_breakers()
        cache.clear()
        self.addCleanup(cache.clear)

    def _patch_stack(self, llm):
        return [
            patch("orchestration.agent_loop.enforce_agent_caps", return_value=True),
            patch("orchestration.agent_loop._search_backend_available", return_value=False),
            patch("orchestration.agent_loop.get_llm_client", return_value=llm),
            patch("orchestration.agent_loop.execute_tool", new_callable=AsyncMock),
            patch("orchestration.agent_loop.cache"),
        ]

    def _dict_cache(self, mock_cache):
        store = {}
        mock_cache.get.side_effect = lambda key, default=None: store.get(key, default)
        mock_cache.set.side_effect = lambda key, value, ttl=None: store.__setitem__(key, value)
        mock_cache.delete.side_effect = lambda key: store.pop(key, None)
        return store

    def test_turn_charges_work_not_the_resent_prefix(self):
        real = LLMClient()
        real.create_message = AsyncMock(side_effect=[
            _make_llm_response(
                [_tool_use_block("t1", "get_weather", {})],
                stop_reason="tool_use",
                usage={"input_tokens": 60000, "output_tokens": 20},
            ),
            _make_llm_response(
                [_text_block("Done.")],
                stop_reason="end_turn",
                usage={"input_tokens": 60500, "output_tokens": 10},
            ),
        ])
        patches = self._patch_stack(real)
        mocks = [p.start() for p in patches]
        self.addCleanup(lambda: [p.stop() for p in patches])
        mock_cache, mock_exec = mocks[4], mocks[3]
        self._dict_cache(mock_cache)
        mock_exec.return_value = {"status": "success"}

        run_async(collect_events(run_agent_loop(
            user_message="do things",
            context={"user_id": 555, "room_id": 556, "username": "t"},
        )))

        # Only work is charged: 20 generated, then 500 newly-read + 10 generated.
        # The 60k/60.5k resent prompt is never billed.
        used, _ = _read_hourly_usage(555)
        self.assertEqual(used, 530)


class AgentLoopQuotaStopTests(SimpleTestCase):
    """A quota block ends the turn honestly and keeps its state (T-F3b)."""

    def setUp(self):
        from orchestration.agent_loop import reset_circuit_breakers

        reset_circuit_breakers()

    def _patch_stack(self):
        return [
            patch("orchestration.agent_loop.enforce_agent_caps", return_value=True),
            patch("orchestration.agent_loop._search_backend_available", return_value=False),
            patch("orchestration.agent_loop.get_llm_client"),
            patch("orchestration.agent_loop.execute_tool", new_callable=AsyncMock),
            patch("orchestration.agent_loop.cache"),
        ]

    def test_quota_stop_keeps_state_and_says_when_it_frees(self):
        patches = self._patch_stack()
        mocks = [p.start() for p in patches]
        self.addCleanup(lambda: [p.stop() for p in patches])
        mock_cache, mock_llm_client = mocks[4], mocks[2]
        store = {}
        mock_cache.get.side_effect = lambda key, default=None: store.get(key, default)
        mock_cache.set.side_effect = lambda key, value, ttl=None: store.__setitem__(key, value)
        mock_cache.delete.side_effect = lambda key: store.pop(key, None)

        llm = MagicMock()
        llm.default_provider = MagicMock(return_value="deepseek")
        llm.create_message = AsyncMock(
            side_effect=TokenQuotaExceeded(1800, reason="limit"),
        )
        mock_llm_client.return_value = llm

        events = run_async(collect_events(run_agent_loop(
            user_message="long task",
            context={"user_id": 7, "room_id": 9, "username": "t"},
        )))

        errors = [e for e in events if e.kind == "error"]
        self.assertTrue(errors)
        self.assertIn("hour", errors[-1].data["message"].lower())
        self.assertIn("frees up", errors[-1].data["message"].lower())
        self.assertIn("more budget", errors[-1].data["message"].lower())

        done = next(e for e in events if e.kind == "done")
        self.assertEqual(done.data["end_reason"], "quota")

        # The state is kept so the user can resume without redoing finished work.
        state = load_loop_state(9, 7)
        self.assertTrue(state and state.awaiting_budget)
