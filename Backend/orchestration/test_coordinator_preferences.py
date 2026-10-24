"""A chat turn's capability gate denies when the preference lookup fails."""
import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from asgiref.sync import async_to_sync
from django.contrib.auth import get_user_model
from django.test import SimpleTestCase, TestCase

from orchestration.agent_loop import AgentEvent
from orchestration.coordinator import OrchestrationCoordinator
from orchestration.test_coordinator_history import _run_agent_turn
from orchestration.turns import CLAIMED, ClaimResult
from orchestration.user_preferences import conservative_default_preferences


class CoordinatorCapabilityLookupFailsClosedTests(SimpleTestCase):

    def test_failed_lookup_sends_conservative_preferences_to_the_loop(self):
        def _boom(user_id, profile_obj=None):
            raise RuntimeError("db down")

        _result, captured = _run_agent_turn("hello", load_preferences=_boom)

        prefs = captured["preferences"]
        self.assertFalse(prefs.get("allow_email", True))
        self.assertFalse(prefs.get("allow_payments", True))
        self.assertFalse(prefs.get("allow_whatsapp", True))
        self.assertTrue(prefs.get("allow_web_search"))

    def test_failed_lookup_applies_no_learned_approval_shortcut(self):
        def _boom(user_id, profile_obj=None):
            raise RuntimeError("db down")

        def _learned(preferences, user_id, room_id):
            merged = dict(preferences or {})
            merged["approval_overrides"] = {"run_command": "auto"}
            return merged

        with patch("orchestration.preference_mining.merge_learned_overrides", side_effect=_learned) as merge:
            _result, captured = _run_agent_turn("hello", load_preferences=_boom)

        merge.assert_not_called()
        self.assertFalse(captured["preferences"].get("approval_overrides"))

    def test_successful_lookup_still_gets_learned_shortcuts(self):
        def _learned(preferences, user_id, room_id):
            merged = dict(preferences or {})
            merged["approval_overrides"] = {"run_command": "auto"}
            return merged

        with patch("orchestration.preference_mining.merge_learned_overrides", side_effect=_learned):
            _result, captured = _run_agent_turn("hello", load_preferences=lambda user_id, profile_obj=None: {"allow_email": True})

        self.assertEqual(captured["preferences"]["approval_overrides"], {"run_command": "auto"})

    def test_execute_tool_still_denies_email_with_conservative_preferences(self):
        from orchestration.tool_executor import execute_tool

        context = {
            "user_id": 1,
            "room_id": 1,
            "preferences": conservative_default_preferences(),
        }
        result = asyncio.run(
            execute_tool(
                "send_email",
                {"to": "example@example.com", "subject": "Hi", "text": "Hello"},
                context,
            )
        )

        self.assertEqual(result["status"], "error")
        self.assertEqual(result["message"], "The send email capability is disabled in your settings.")

    def test_tool_definitions_hide_email_but_keep_weather(self):
        from orchestration.tool_schemas import get_tool_definitions

        available = {"send_email", "get_weather"}
        names = {
            t["name"] for t in get_tool_definitions(
                user_capabilities=conservative_default_preferences(),
                available_actions=available,
            )
        }

        self.assertNotIn("send_email", names)
        self.assertIn("get_weather", names)


class CoordinatorLoadedProfileFailsClosedTests(TestCase):
    def test_normalisation_failure_after_profile_load_uses_conservative_defaults(self):
        from orchestration import user_preferences as prefs_module

        user = get_user_model().objects.create_user(
            username="loaded-profile-user", password="pw",  # nosec B106
        )
        profile = user.profile
        profile.notification_preferences = {"allow_email": False}
        profile.save(update_fields=["notification_preferences"])
        real_get = prefs_module.get_user_preferences
        real_normalize = prefs_module.normalize_preferences
        seen = {}
        captured = {}

        def _wrapped(user_id, **kwargs):
            seen.update(kwargs)
            return real_get(user_id, **kwargs)

        def _normalize(raw_prefs, **kwargs):
            if raw_prefs:
                raise RuntimeError("bad preferences")
            return real_normalize(raw_prefs, **kwargs)

        async def _fake_loop(**kwargs):
            captured.update(kwargs)
            yield AgentEvent("text", {"text": "ok"})

        async def run():
            return await OrchestrationCoordinator().handle_message(
                query="hello",
                user_id=user.id,
                room_id="1",
                username=user.username,
                message_id=1,
                send_chunk=AsyncMock(),
                send_step_event=AsyncMock(),
                get_context_prompt=AsyncMock(return_value=""),
                bump_signals=MagicMock(),
            )

        with (
            patch("orchestration.coordinator.get_user_preferences", side_effect=_wrapped),
            patch(
                "orchestration.user_preferences.normalize_preferences",
                side_effect=_normalize,
            ),
            patch("orchestration.coordinator.load_memory_summary", AsyncMock(return_value=None)),
            patch("orchestration.coordinator.has_pending_agent_state", AsyncMock(return_value=False)),
            patch("orchestration.coordinator.run_agent_loop", _fake_loop),
            patch("orchestration.coordinator.record_event"),
            patch("orchestration.coordinator.cache", MagicMock()),
            patch("orchestration.coordinator.aget_paused_turn", new=AsyncMock(return_value=None)),
            patch(
                "orchestration.coordinator.aclaim_turn",
                new=AsyncMock(return_value=ClaimResult(CLAIMED, SimpleNamespace(
                    pk="00000000-0000-0000-0000-000000000001",
                    version=0,
                    state="running",
                ))),
            ),
            patch("orchestration.coordinator.atransition", new=AsyncMock(return_value=True)),
            patch("orchestration.coordinator.alink_message", new=AsyncMock(return_value=True)),
            patch("orchestration.coordinator._room_exists", new=lambda room_id: True),
            patch(
                "orchestration.preference_mining.merge_learned_overrides",
                side_effect=AssertionError("must not merge after a failed load"),
            ),
        ):
            async_to_sync(run)()

        self.assertIsNotNone(seen.get("profile_obj"))
        self.assertEqual(seen["profile_obj"].pk, user.profile.pk)
        prefs = captured["preferences"]
        self.assertFalse(prefs.get("allow_email", True))
        self.assertFalse(prefs.get("allow_payments", True))
        self.assertFalse(prefs.get("allow_whatsapp", True))
        self.assertTrue(prefs.get("allow_web_search"))
