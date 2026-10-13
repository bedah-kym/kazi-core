"""A workflow step's capability gate denies when the owner's preferences cannot be read."""
from unittest.mock import AsyncMock, MagicMock, patch

from asgiref.sync import async_to_sync
from django.test import SimpleTestCase

from workflows.activity_executors import execute_workflow_step

SEND_EMAIL = {
    "id": "s1",
    "service": "gmail",
    "action": "send_email",
    "params": {"to": "example@example.com", "subject": "Hi", "text": "Hello"},
}
WITHDRAW = {
    "id": "s1",
    "service": "payments",
    "action": "withdraw",
    "params": {"amount": 100, "phone_number": "+254700000000"},
}
SEND_WHATSAPP = {
    "id": "s1",
    "service": "whatsapp",
    "action": "send_message",
    "params": {"phone_number": "+254700000000", "message": "Hello"},
}
GET_WEATHER = {
    "id": "s1",
    "service": "weather",
    "action": "get_weather",
    "params": {"city": "Nairobi"},
}


def _lookup_down():
    user_model = MagicMock()
    user_model.objects.select_related.side_effect = RuntimeError("db down")
    return patch("django.contrib.auth.get_user_model", return_value=user_model)


def _user_not_found():
    user_model = MagicMock()
    user_model.objects.select_related.return_value.filter.return_value.first.return_value = None
    return patch("django.contrib.auth.get_user_model", return_value=user_model)


class StepCapabilityLookupFailsClosedTests(SimpleTestCase):

    def _run_step(self, step, context):
        connector = MagicMock()
        connector.execute = AsyncMock(return_value={"status": "success", "data": {}})
        with patch(
            "orchestration.connector_registry.discover_connectors",
            return_value={step["action"]: connector},
        ):
            result = async_to_sync(execute_workflow_step)(dict(step), context)
        return result, connector

    def _assert_denied(self, step, gate, context):
        result, connector = self._run_step(step, context)
        self.assertEqual(result.get("status"), "error")
        self.assertIn(gate, result.get("error", ""))
        connector.execute.assert_not_awaited()

    def test_email_step_is_denied_when_the_lookup_fails(self):
        with _lookup_down():
            self._assert_denied(SEND_EMAIL, "allow_email", {"user_id": 7, "room_id": None})

    def test_withdraw_step_is_denied_when_the_lookup_fails(self):
        with _lookup_down():
            self._assert_denied(WITHDRAW, "allow_payments", {"user_id": 7, "room_id": None})

    def test_whatsapp_step_is_denied_when_the_lookup_fails(self):
        with _lookup_down():
            self._assert_denied(SEND_WHATSAPP, "allow_whatsapp", {"user_id": 7, "room_id": None})

    def test_weather_step_still_runs_when_the_lookup_fails(self):
        with _lookup_down():
            result, connector = self._run_step(GET_WEATHER, {"user_id": 7, "room_id": None})
        self.assertEqual(result.get("status"), "success")
        connector.execute.assert_awaited_once()

    def test_email_step_with_no_owner_is_denied(self):
        self._assert_denied(SEND_EMAIL, "allow_email", {"room_id": None})

    def test_weather_step_with_no_owner_still_runs(self):
        result, connector = self._run_step(GET_WEATHER, {"room_id": None})
        self.assertEqual(result.get("status"), "success")
        connector.execute.assert_awaited_once()

    def test_email_step_is_denied_when_the_owner_switched_email_off(self):
        # The case that was already blocked before this change must stay blocked.
        self._assert_denied(
            SEND_EMAIL, "allow_email", {"user_id": 7, "room_id": None, "preferences": {"allow_email": False}}
        )


class LoadPreferencesFailClosedTests(SimpleTestCase):

    def test_failed_lookup_turns_off_only_the_sensitive_gates(self):
        from orchestration.user_preferences import load_preferences_fail_closed

        with _lookup_down():
            prefs = load_preferences_fail_closed(7)

        self.assertFalse(prefs["allow_payments"])
        self.assertFalse(prefs["allow_email"])
        self.assertFalse(prefs["allow_whatsapp"])
        self.assertTrue(prefs["allow_web_search"])
        self.assertTrue(prefs["allow_travel"])
        self.assertTrue(prefs["allow_reminders"])
        self.assertTrue(prefs["allow_calendar"])

    def test_user_without_stored_preferences_is_not_restricted(self):
        from orchestration.user_preferences import load_preferences_fail_closed

        with _user_not_found():
            prefs = load_preferences_fail_closed(7)

        self.assertTrue(prefs["allow_payments"])
        self.assertTrue(prefs["allow_email"])
        self.assertTrue(prefs["allow_whatsapp"])
