"""A run only uses what it was offered: delegated calls and the provider's native search."""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

from django.test import SimpleTestCase, override_settings

from orchestration.agent_loop import _run_sub_agent, run_agent_loop
from orchestration.agent_prompts import NO_SEARCH_TOOL_NOTE
from orchestration.test_agentic_scenarios import (
    _make_llm_response,
    _text_block,
    _tool_use_block,
    collect_events,
    run_async,
)
from orchestration.tool_schemas import native_search_allowed

WHO = {"user_id": 1, "room_id": 2}
NATIVE_SEARCH = {"name": "web_search", "type": "web_search_20250305", "max_uses": 4}


def _persona(*scope):
    return MagicMock(tool_scope=list(scope), approval_boundary=[], risk_ceiling="high")


def _tool(name):
    return {"name": name, "description": name, "input_schema": {"type": "object"}}


def _offered_names(call):
    return {t.get("name") for t in (call.kwargs.get("tools") or [])}


class _Base(SimpleTestCase):
    def setUp(self):
        super().setUp()
        from orchestration.agent_loop import reset_circuit_breakers

        reset_circuit_breakers()
        self.addCleanup(reset_circuit_breakers)
        for target, kwargs in (
            ("orchestration.agent_loop.get_remaining_searches", {"return_value": 5}),
            ("orchestration.agent_loop._record_receipt", {"new": AsyncMock()}),
            ("orchestration.agent_loop.record_event", {}),
        ):
            p = patch(target, **kwargs)
            p.start()
            self.addCleanup(p.stop)

    def _llm(self, responses, provider="anthropic"):
        llm = MagicMock()
        llm.default_provider = MagicMock(return_value=provider)
        llm.create_message = AsyncMock(side_effect=responses)
        return llm


@override_settings(AGENT_TURN_TOKEN_BUDGET=50000, AGENT_TURN_TOKEN_BUDGET_MAX=500000)
class MainLoopNativeSearchTests(_Base):
    """The main loop offers native search only where search_info would be allowed."""

    def _first_request(self, persona=None, preferences=None):
        llm = self._llm([_make_llm_response([_text_block("Done.")], stop_reason="end_turn")])
        with patch("orchestration.agent_loop.get_llm_client", return_value=llm), \
                patch("orchestration.agent_loop.enforce_agent_caps", return_value=True), \
                patch("orchestration.agent_loop._search_backend_available", return_value=False), \
                patch("orchestration.agent_loop._resolve_model_override", return_value=(None, None)), \
                patch("orchestration.agent_loop._resolve_persona", new=AsyncMock(return_value=persona)), \
                patch("orchestration.agent_loop.cache") as mock_cache:
            mock_cache.get.return_value = None
            run_async(collect_events(run_agent_loop(
                user_message="what's new?",
                context={**WHO, "username": "t"},
                preferences=preferences,
            )))
        first = llm.create_message.await_args_list[0]
        return _offered_names(first), first.kwargs.get("system") or ""

    def test_a_persona_scope_without_search_gets_no_native_search(self):
        tools, system = self._first_request(persona=_persona("get_weather"))

        self.assertNotIn("web_search", tools)
        # The model is told there is no search, so it does not invent one.
        self.assertIn(NO_SEARCH_TOOL_NOTE, system)

    def test_web_search_switched_off_in_settings_gets_no_native_search(self):
        tools, system = self._first_request(preferences={"allow_web_search": False})

        self.assertNotIn("web_search", tools)
        self.assertIn(NO_SEARCH_TOOL_NOTE, system)

    def test_a_persona_scope_with_search_still_gets_native_search(self):
        tools, system = self._first_request(persona=_persona("get_weather", "search_info"))

        self.assertIn("web_search", tools)
        self.assertNotIn(NO_SEARCH_TOOL_NOTE, system)

    def test_no_persona_and_default_settings_still_get_native_search(self):
        tools, system = self._first_request()

        self.assertIn("web_search", tools)
        self.assertNotIn(NO_SEARCH_TOOL_NOTE, system)


class DelegatedNativeSearchTests(_Base):
    def _sub_run_tools(self, persona, parent_tools, preferences=None):
        llm = self._llm([_make_llm_response([_text_block("Done.")], stop_reason="end_turn")])
        with patch("orchestration.agent_loop.get_llm_client", return_value=llm), \
                patch("orchestration.agent_loop._resolve_persona", new=AsyncMock(return_value=persona)):
            run_async(_run_sub_agent(
                {"task": "research"}, dict(WHO), dict(preferences or {}), "system", parent_tools,
            ))
        first = llm.create_message.await_args_list[0]
        return _offered_names(first), first.kwargs.get("system") or ""

    def test_a_delegated_run_drops_native_search_the_room_persona_excludes(self):
        tools, system = self._sub_run_tools(_persona("get_weather"), [NATIVE_SEARCH, _tool("get_weather")])

        self.assertNotIn("web_search", tools)
        self.assertIn("get_weather", tools)
        # The parent's prompt was written with search; the child is told it is gone.
        self.assertIn(NO_SEARCH_TOOL_NOTE, system)

    def test_a_delegated_run_keeps_native_search_the_room_persona_allows(self):
        tools, system = self._sub_run_tools(
            _persona("get_weather", "search"), [NATIVE_SEARCH, _tool("get_weather")],
        )

        self.assertIn("web_search", tools)
        self.assertNotIn(NO_SEARCH_TOOL_NOTE, system)

    def test_a_delegated_run_drops_native_search_switched_off_in_settings(self):
        tools, system = self._sub_run_tools(
            None, [NATIVE_SEARCH, _tool("get_weather")], preferences={"allow_web_search": False},
        )

        self.assertNotIn("web_search", tools)
        self.assertIn("get_weather", tools)
        self.assertIn(NO_SEARCH_TOOL_NOTE, system)

    def test_a_delegated_run_with_no_native_search_to_drop_keeps_the_parent_prompt(self):
        tools, system = self._sub_run_tools(_persona("get_weather"), [_tool("get_weather")])

        self.assertEqual(tools, {"get_weather"})
        self.assertEqual(system, "system")


class DelegatedOfferedToolsTests(_Base):
    """A delegated call to a tool the run was not given is refused before it runs."""

    AUTO_EMAIL = {"approval_overrides": {"send_email": "auto"}}

    def _delegate(self, tool_input, parent_tools):
        llm = self._llm([
            _make_llm_response(
                [_tool_use_block("t1", "send_email", {"to": "a@example.com"})],
                stop_reason="tool_use",
            ),
            _make_llm_response([_text_block("Done.")], stop_reason="end_turn"),
        ])
        executor = AsyncMock(return_value={"status": "success"})
        with patch("orchestration.agent_loop.get_llm_client", return_value=llm), \
                patch("orchestration.agent_loop._resolve_persona", new=AsyncMock(return_value=None)), \
                patch("orchestration.agent_loop._execute_with_timeout", new=executor):
            result = run_async(_run_sub_agent(
                tool_input, dict(WHO), dict(self.AUTO_EMAIL), "system", parent_tools,
            ))
        # The messages list is the run's own, so read the result block by id.
        returned = [
            block
            for message in llm.create_message.await_args_list[1].kwargs["messages"]
            if isinstance(message.get("content"), list)
            for block in message["content"]
            if block.get("type") == "tool_result" and block.get("tool_use_id") == "t1"
        ]
        return result, executor, returned

    def test_a_call_to_a_tool_the_parent_never_offered_does_not_run(self):
        result, executor, returned = self._delegate({"task": "email them"}, [_tool("get_weather")])

        executor.assert_not_called()
        self.assertIn("not one of the tools offered", returned[0]["content"])

    def test_a_call_outside_the_delegate_tools_filter_does_not_run(self):
        result, executor, returned = self._delegate(
            {"task": "check the weather", "tools": "get_weather"},
            [_tool("get_weather"), _tool("send_email")],
        )

        executor.assert_not_called()
        self.assertIn("not one of the tools offered", returned[0]["content"])

    def test_an_offered_call_still_runs(self):
        _result, executor, _returned = self._delegate({"task": "email them"}, [_tool("send_email")])

        executor.assert_awaited_once()


class NativeSearchRuleTests(SimpleTestCase):
    def test_the_rule(self):
        cases = [
            (None, None, True),
            ({}, None, True),
            ({"allow_web_search": False}, None, False),
            ({"allow_web_search": True}, ["get_weather"], False),
            ({"allow_web_search": True}, ["search"], True),
            ({"allow_web_search": True}, ["web_search"], True),
            ({"allow_web_search": False}, ["search_info"], False),
            ({"allow_web_search": True}, [], True),
        ]
        for caps, scope, allowed in cases:
            with self.subTest(caps=caps, scope=scope):
                self.assertIs(native_search_allowed(caps, scope), allowed)
