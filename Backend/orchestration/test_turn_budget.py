"""T-F3: one work-token budget per turn that the model can see.

Acceptance tests for the turn budget. They fail on ``main``: main charges the
whole resent prefix on every call, raises the tool cap once the shell is used,
never tells the model what is left, cannot resume after the model asks for
more, and builds the search tool only once per turn.
"""
from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

from django.test import SimpleTestCase, override_settings

from orchestration.agent_loop import REQUEST_MORE_BUDGET_TOOL
from orchestration.test_agentic_scenarios import (
    _make_llm_response,
    _text_block,
    _tool_use_block,
    collect_events,
    run_async,
)


class WorkTokenAccountingTests(SimpleTestCase):
    def test_total_input_sums_anthropic_cache_fields(self):
        from orchestration.agent_loop import _total_input_tokens

        response = {"usage": {
            "input_tokens": 10,
            "cache_read_input_tokens": 100,
            "cache_creation_input_tokens": 5,
        }}
        self.assertEqual(_total_input_tokens(response), 115)

    def test_first_call_charges_output_only(self):
        from orchestration.agent_loop import _work_tokens

        work, baseline = _work_tokens(
            {"usage": {"input_tokens": 6000, "output_tokens": 30}}, 0,
        )
        self.assertEqual(work, 30)
        self.assertEqual(baseline, 6000)

    def test_later_calls_charge_growth_plus_output(self):
        from orchestration.agent_loop import _work_tokens

        work, baseline = _work_tokens(
            {"usage": {"input_tokens": 6500, "output_tokens": 40}}, 6000,
        )
        self.assertEqual(work, 540)  # 500 newly-read + 40 generated
        self.assertEqual(baseline, 6500)

    def test_input_shrink_is_never_negative(self):
        from orchestration.agent_loop import _work_tokens

        work, _ = _work_tokens({"usage": {"input_tokens": 100, "output_tokens": 7}}, 6000)
        self.assertEqual(work, 7)


class _LoopHarness(SimpleTestCase):
    def _run(self, coro):
        return run_async(coro)

    def _patch_stack(self):
        return [
            patch("orchestration.agent_loop.enforce_agent_caps", return_value=True),
            patch("orchestration.agent_loop._search_backend_available", return_value=False),
            patch("orchestration.agent_loop.get_llm_client"),
            patch("orchestration.agent_loop.execute_tool", new_callable=AsyncMock),
            patch("orchestration.agent_loop.cache"),
        ]

    def _llm(self, provider="deepseek", side_effect=None):
        llm = MagicMock()
        llm.default_provider = MagicMock(return_value=provider)
        llm.create_message = AsyncMock(side_effect=side_effect)
        return llm


class ResentHistoryBudgetTests(_LoopHarness):
    """A long conversation's resent history must not spend the turn's budget."""

    @override_settings(AGENT_TURN_TOKEN_BUDGET=50000, AGENT_TURN_TOKEN_BUDGET_MAX=500000)
    def test_resent_history_is_not_charged(self):
        from orchestration.agent_loop import run_agent_loop

        patches = self._patch_stack()
        mocks = [p.start() for p in patches]
        self.addCleanup(lambda: [p.stop() for p in patches])
        mock_cache, mock_exec, mock_llm_client = mocks[4], mocks[3], mocks[2]
        mock_cache.get.return_value = None
        mock_exec.return_value = {"status": "success"}
        llm = self._llm(side_effect=[
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
        mock_llm_client.return_value = llm
        history = [{"role": "user", "content": "x" * 1000} for _ in range(40)]

        events = self._run(collect_events(run_agent_loop(
            user_message="do it",
            context={"user_id": 1, "room_id": 1, "username": "t"},
            history=history,
        )))

        done = next(e for e in events if e.kind == "done")
        # 20 generated + (500 growth + 10 generated); the 60k prefix is not work.
        self.assertEqual(done.data["budget_used"], 530)
        self.assertEqual(done.data["end_reason"], "finished")


class SingleBoundTests(_LoopHarness):
    def test_shell_session_cap_helper_is_gone(self):
        with self.assertRaises(ImportError):
            from orchestration.agent_loop import _session_tool_call_cap  # noqa: F401

    @override_settings(AGENT_TURN_TOKEN_BUDGET=50000, AGENT_TURN_TOKEN_BUDGET_MAX=500000)
    @patch("orchestration.agent_loop.HARD_CAP_TOOL_CALLS", 2)
    def test_shell_usage_does_not_raise_the_tool_bound(self):
        from orchestration.agent_loop import run_agent_loop

        patches = self._patch_stack()
        mocks = [p.start() for p in patches]
        self.addCleanup(lambda: [p.stop() for p in patches])
        mock_cache, mock_exec, mock_llm_client = mocks[4], mocks[3], mocks[2]
        mock_cache.get.return_value = None
        mock_exec.return_value = {"status": "success"}
        llm = self._llm(side_effect=[
            _make_llm_response(
                [_tool_use_block("t1", "run_command", {"command": "dir"})],
                stop_reason="tool_use",
                usage={"input_tokens": 6000, "output_tokens": 10},
            ),
            _make_llm_response(
                [_tool_use_block("t2", "run_command", {"command": "dir"})],
                stop_reason="tool_use",
                usage={"input_tokens": 6100, "output_tokens": 10},
            ),
            _make_llm_response(
                [_tool_use_block("t3", "run_command", {"command": "dir"})],
                stop_reason="tool_use",
                usage={"input_tokens": 6200, "output_tokens": 10},
            ),
        ])
        mock_llm_client.return_value = llm

        events = self._run(collect_events(run_agent_loop(
            user_message="investigate",
            context={"user_id": 1, "room_id": 1, "username": "t"},
            preferences={"shell_profile": "open", "_shell_autopilot": True},
        )))

        # Shell use no longer buys a higher cap: the runaway backstop is uniform.
        self.assertEqual([e.kind for e in events].count("tool_result"), 2)

    @override_settings(AGENT_TURN_TOKEN_BUDGET=100, AGENT_TURN_TOKEN_BUDGET_MAX=100)
    def test_reaching_the_bound_ends_with_a_harness_note_and_no_success_claim(self):
        from orchestration.agent_loop import run_agent_loop

        patches = self._patch_stack()
        mocks = [p.start() for p in patches]
        self.addCleanup(lambda: [p.stop() for p in patches])
        mock_cache, mock_exec, mock_llm_client = mocks[4], mocks[3], mocks[2]
        mock_cache.get.return_value = None
        mock_exec.return_value = {"status": "success"}
        llm = self._llm(side_effect=[
            _make_llm_response(
                [_tool_use_block("t1", "get_weather", {})],
                stop_reason="tool_use",
                usage={"input_tokens": 6000, "output_tokens": 500},
            ),
        ])
        mock_llm_client.return_value = llm

        events = self._run(collect_events(run_agent_loop(
            user_message="do it",
            context={"user_id": 1, "room_id": 1, "username": "t"},
        )))

        errors = [e for e in events if e.kind == "error"]
        self.assertTrue(errors)
        self.assertIn("budget", errors[-1].data["message"].lower())
        self.assertNotIn("finish", errors[-1].data["message"].lower())


class BudgetVisibilityTests(_LoopHarness):
    @override_settings(AGENT_TURN_TOKEN_BUDGET=50000, AGENT_TURN_TOKEN_BUDGET_MAX=500000)
    def test_model_sees_the_budget_on_the_first_call_and_after(self):
        from orchestration.agent_loop import run_agent_loop

        patches = self._patch_stack()
        mocks = [p.start() for p in patches]
        self.addCleanup(lambda: [p.stop() for p in patches])
        mock_cache, mock_exec, mock_llm_client = mocks[4], mocks[3], mocks[2]
        mock_cache.get.return_value = None
        mock_exec.return_value = {"status": "success"}
        snapshots = []
        responses = [
            _make_llm_response(
                [_tool_use_block("t1", "get_weather", {})],
                stop_reason="tool_use",
                usage={"input_tokens": 6000, "output_tokens": 100},
            ),
            _make_llm_response(
                [_text_block("Done.")],
                stop_reason="end_turn",
                usage={"input_tokens": 6100, "output_tokens": 10},
            ),
        ]

        async def _create(**kwargs):
            snapshots.append({
                "system": kwargs.get("system"),
                "messages": json.loads(json.dumps(kwargs.get("messages"))),
            })
            return responses.pop(0)

        llm = self._llm()
        llm.create_message = AsyncMock(side_effect=_create)
        mock_llm_client.return_value = llm

        self._run(collect_events(run_agent_loop(
            user_message="do it",
            context={"user_id": 1, "room_id": 1, "username": "t"},
        )))

        first, second = snapshots[0], snapshots[1]
        # The model now sees the budget on the very first call, on the new user
        # message; the system prompt (the cached prefix) is never touched.
        first_text = json.dumps(first["messages"])
        self.assertIn("[Harness: turn budget", first_text)
        self.assertIn("left", first_text)
        self.assertNotIn("[Harness: turn budget", first["system"])
        self.assertNotIn("[Harness: turn budget", second["system"])
        second_text = json.dumps(second["messages"])
        self.assertIn("[Harness: turn budget", second_text)
        self.assertIn("left", second_text)


class RequestMoreBudgetTests(_LoopHarness):
    def _dict_cache(self, mock_cache):
        store = {}
        mock_cache.get.side_effect = lambda key, default=None: store.get(key, default)
        mock_cache.set.side_effect = lambda key, value, ttl=None: store.__setitem__(key, value)
        mock_cache.delete.side_effect = lambda key: store.pop(key, None)
        return store

    @override_settings(AGENT_TURN_TOKEN_BUDGET=50000, AGENT_TURN_TOKEN_BUDGET_MAX=500000)
    def test_asking_for_budget_ends_the_turn_honestly(self):
        from orchestration.agent_loop import load_loop_state, run_agent_loop

        patches = self._patch_stack()
        mocks = [p.start() for p in patches]
        self.addCleanup(lambda: [p.stop() for p in patches])
        mock_cache, mock_exec, mock_llm_client = mocks[4], mocks[3], mocks[2]
        self._dict_cache(mock_cache)
        mock_exec.return_value = {"status": "success"}
        llm = self._llm(side_effect=[
            _make_llm_response(
                [_text_block("I finished A; B is left."),
                 _tool_use_block("t1", REQUEST_MORE_BUDGET_TOOL, {"reason": "B is left"})],
                stop_reason="tool_use",
                usage={"input_tokens": 6000, "output_tokens": 40},
            ),
        ])
        mock_llm_client.return_value = llm

        events = self._run(collect_events(run_agent_loop(
            user_message="long task",
            context={"user_id": 7, "room_id": 9, "username": "t"},
        )))

        kinds = [e.kind for e in events]
        self.assertIn("budget_request", kinds)
        done = next(e for e in events if e.kind == "done")
        self.assertEqual(done.data["end_reason"], "asked_for_more")
        mock_exec.assert_not_awaited()
        state = load_loop_state(9, 7)
        self.assertTrue(state and state.awaiting_budget)

    @override_settings(AGENT_TURN_TOKEN_BUDGET=50000, AGENT_TURN_TOKEN_BUDGET_MAX=500000)
    def test_resuming_after_extend_does_not_repeat_a_finished_call(self):
        from orchestration.agent_loop import resume_with_more_budget, run_agent_loop

        patches = self._patch_stack()
        mocks = [p.start() for p in patches]
        self.addCleanup(lambda: [p.stop() for p in patches])
        mock_cache, mock_exec, mock_llm_client = mocks[4], mocks[3], mocks[2]
        self._dict_cache(mock_cache)
        mock_exec.return_value = {"status": "success"}
        llm = self._llm(side_effect=[
            _make_llm_response(
                [_tool_use_block("t1", "get_weather", {"city": "Nairobi"})],
                stop_reason="tool_use",
                usage={"input_tokens": 6000, "output_tokens": 30},
            ),
            _make_llm_response(
                [_text_block("Need more."),
                 _tool_use_block("t2", REQUEST_MORE_BUDGET_TOOL, {"reason": "more"})],
                stop_reason="tool_use",
                usage={"input_tokens": 6200, "output_tokens": 20},
            ),
        ])
        mock_llm_client.return_value = llm

        self._run(collect_events(run_agent_loop(
            user_message="long task",
            context={"user_id": 7, "room_id": 9, "username": "t"},
        )))
        self.assertEqual(mock_exec.await_count, 1)

        # The model re-issues the same finished call after the grant; it must be
        # answered from the earlier result, not run again.
        llm.create_message = AsyncMock(side_effect=[
            _make_llm_response(
                [_text_block("Resuming."),
                 _tool_use_block("t3", "get_weather", {"city": "Nairobi"})],
                stop_reason="tool_use",
                usage={"input_tokens": 6300, "output_tokens": 20},
            ),
            _make_llm_response(
                [_text_block("All done.")],
                stop_reason="end_turn",
                usage={"input_tokens": 6400, "output_tokens": 10},
            ),
        ])
        events = self._run(collect_events(resume_with_more_budget(
            context={"user_id": 7, "room_id": 9, "username": "t"},
        )))

        self.assertEqual(mock_exec.await_count, 1)
        done = next(e for e in events if e.kind == "done")
        self.assertEqual(done.data["end_reason"], "finished")

    @override_settings(AGENT_TURN_TOKEN_BUDGET=1000, AGENT_TURN_TOKEN_BUDGET_MAX=100000)
    def test_a_bound_reached_turn_resumes_on_a_grant_without_repeating(self):
        from orchestration.agent_loop import (
            load_loop_state, resume_with_more_budget, run_agent_loop,
        )

        patches = self._patch_stack()
        mocks = [p.start() for p in patches]
        self.addCleanup(lambda: [p.stop() for p in patches])
        mock_cache, mock_exec, mock_llm_client = mocks[4], mocks[3], mocks[2]
        self._dict_cache(mock_cache)
        mock_exec.return_value = {"status": "success"}
        llm = self._llm(side_effect=[
            _make_llm_response(
                [_tool_use_block("t1", "get_weather", {"city": "Nairobi"})],
                stop_reason="tool_use",
                usage={"input_tokens": 6000, "output_tokens": 1200},
            ),
        ])
        mock_llm_client.return_value = llm

        events = self._run(collect_events(run_agent_loop(
            user_message="long task",
            context={"user_id": 7, "room_id": 9, "username": "t"},
        )))

        # The bound is reached without the model asking, but the state is kept
        # and the harness line says plainly what ran.
        errors = [e for e in events if e.kind == "error"]
        self.assertTrue(errors)
        self.assertIn("budget", errors[-1].data["message"].lower())
        self.assertIn("tool call", errors[-1].data["message"].lower())
        done = next(e for e in events if e.kind == "done")
        self.assertEqual(done.data["end_reason"], "budget")
        self.assertEqual(mock_exec.await_count, 1)
        state = load_loop_state(9, 7)
        self.assertTrue(state and state.awaiting_budget)

        # A grant resumes from where it stopped; the finished call is answered
        # from its earlier result, not run a second time.
        llm.create_message = AsyncMock(side_effect=[
            _make_llm_response(
                [_text_block("Resuming."),
                 _tool_use_block("t2", "get_weather", {"city": "Nairobi"})],
                stop_reason="tool_use",
                usage={"input_tokens": 6200, "output_tokens": 20},
            ),
            _make_llm_response(
                [_text_block("All done.")],
                stop_reason="end_turn",
                usage={"input_tokens": 6400, "output_tokens": 10},
            ),
        ])
        events = self._run(collect_events(resume_with_more_budget(
            context={"user_id": 7, "room_id": 9, "username": "t"},
        )))
        self.assertEqual(mock_exec.await_count, 1)
        done = next(e for e in events if e.kind == "done")
        self.assertEqual(done.data["end_reason"], "finished")


class DelegatedBudgetTests(SimpleTestCase):
    def _state(self, budget, used):
        from orchestration.agent_loop import LoopState

        return LoopState(messages=[], token_budget=budget, tokens_used=used)

    def test_delegated_run_gets_a_share_of_the_parent_remaining(self):
        from orchestration.agent_loop import MIN_DELEGATED_BUDGET, _delegated_budget

        self.assertEqual(_delegated_budget(self._state(100000, 20000)), 40000)
        self.assertEqual(_delegated_budget(self._state(1000, 990)), MIN_DELEGATED_BUDGET)

    def test_only_delegation_gets_the_budget_share(self):
        from orchestration.agent_loop import _meta_tool_context

        context = {"user_id": 1}
        self.assertIs(
            _meta_tool_context("handoff_to_workflow", context, self._state(100, 0)),
            context,
        )
        child = _meta_tool_context("delegate_task", context, self._state(100000, 0))
        self.assertEqual(child["sub_agent_budget"], 50000)
        self.assertNotIn("sub_agent_budget", context)


class CapsOffMappingTests(SimpleTestCase):
    @override_settings(AGENT_TURN_TOKEN_BUDGET=40000, AGENT_TURN_TOKEN_BUDGET_MAX=900000)
    def test_caps_off_maps_to_the_largest_bound(self):
        from orchestration.agent_loop import _turn_token_budget

        self.assertEqual(_turn_token_budget(True), 40000)
        self.assertEqual(_turn_token_budget(False), 900000)

    @override_settings(AGENT_TURN_TOKEN_BUDGET=999999, AGENT_TURN_TOKEN_BUDGET_MAX=100000)
    def test_normal_bound_is_clamped_to_the_ceiling(self):
        from orchestration.agent_loop import _turn_token_budget

        self.assertEqual(_turn_token_budget(True), 100000)


class SearchAllowanceTests(_LoopHarness):
    @staticmethod
    def _search_uses(kwargs):
        tool = next(
            (t for t in (kwargs.get("tools") or []) if t.get("name") == "web_search"),
            None,
        )
        return tool.get("max_uses") if tool else None

    @override_settings(AGENT_TURN_TOKEN_BUDGET=50000, AGENT_TURN_TOKEN_BUDGET_MAX=500000)
    @patch("orchestration.agent_loop.get_remaining_searches", side_effect=[5, 5, 2, 0])
    def test_main_loop_rebuilds_the_search_tool_per_request(self, _remaining):
        from orchestration.agent_loop import run_agent_loop

        patches = self._patch_stack()
        mocks = [p.start() for p in patches]
        self.addCleanup(lambda: [p.stop() for p in patches])
        mock_cache, mock_exec, mock_llm_client = mocks[4], mocks[3], mocks[2]
        mock_cache.get.return_value = None
        mock_exec.return_value = {"status": "success"}
        llm = self._llm(provider="anthropic", side_effect=[
            _make_llm_response(
                [_tool_use_block("t1", "get_weather", {})],
                stop_reason="tool_use",
                usage={"input_tokens": 6000, "output_tokens": 10},
            ),
            _make_llm_response(
                [_tool_use_block("t2", "get_weather", {})],
                stop_reason="tool_use",
                usage={"input_tokens": 6200, "output_tokens": 10},
            ),
            _make_llm_response(
                [_text_block("Done.")],
                stop_reason="end_turn",
                usage={"input_tokens": 6400, "output_tokens": 10},
            ),
        ])
        mock_llm_client.return_value = llm

        self._run(collect_events(run_agent_loop(
            user_message="research",
            context={"user_id": 1, "room_id": 1, "username": "t"},
        )))

        calls = llm.create_message.await_args_list
        self.assertEqual(self._search_uses(calls[0].kwargs), 5)
        self.assertEqual(self._search_uses(calls[1].kwargs), 2)
        self.assertIsNone(self._search_uses(calls[2].kwargs))

    @patch("orchestration.agent_loop.get_remaining_searches", side_effect=[4, 1, 0])
    @patch("orchestration.agent_loop.update_memory_state", new=AsyncMock())
    @patch("orchestration.agent_loop._record_receipt", new=AsyncMock())
    @patch("orchestration.agent_loop._resolve_persona", new=AsyncMock(return_value=None))
    @patch("orchestration.agent_loop.get_llm_client")
    @patch("orchestration.agent_loop.execute_tool", new_callable=AsyncMock)
    def test_delegated_run_rebuilds_the_search_tool_per_request(
        self, mock_exec, mock_llm_client, _remaining,
    ):
        from orchestration.agent_loop import _run_sub_agent

        mock_exec.return_value = {"status": "success"}
        llm = self._llm(provider="anthropic", side_effect=[
            _make_llm_response(
                [_tool_use_block("t1", "get_weather", {})],
                stop_reason="tool_use",
                usage={"input_tokens": 100, "output_tokens": 10},
            ),
            _make_llm_response(
                [_tool_use_block("t2", "get_weather", {})],
                stop_reason="tool_use",
                usage={"input_tokens": 200, "output_tokens": 10},
            ),
            _make_llm_response(
                [_text_block("Done.")],
                stop_reason="end_turn",
                usage={"input_tokens": 300, "output_tokens": 10},
            ),
        ])
        mock_llm_client.return_value = llm
        parent_tools = [
            {"name": "web_search", "type": "web_search_20250305", "max_uses": 4},
            {"name": "get_weather", "input_schema": {}},
        ]

        self._run(_run_sub_agent(
            {"task": "research"}, {"user_id": 1, "room_id": 1}, None, "system", parent_tools,
        ))

        calls = llm.create_message.await_args_list
        self.assertEqual(self._search_uses(calls[0].kwargs), 4)
        self.assertEqual(self._search_uses(calls[1].kwargs), 1)
        self.assertIsNone(self._search_uses(calls[2].kwargs))
