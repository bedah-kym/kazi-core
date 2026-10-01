"""Tests for the deterministic capability manifest and delta (v0.7 W-A2, #167)."""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

from asgiref.sync import async_to_sync
from django.contrib.auth import get_user_model
from django.test import SimpleTestCase, TestCase

from workflows.activity_executors import execute_workflow_step
from workflows.capabilities import validate_workflow_definition
from workflows.capability_manifest import (
    approval_kind_for_delta,
    capability_delta,
    capability_delta_for_suggestion,
    capability_for_step,
    manifest_from_definition,
)
from workflows.models import UserWorkflow, WorkflowImprovementSuggestion
from workflows.versioning import create_workflow_version

User = get_user_model()


def _base_definition(**overrides):
    definition = {
        "workflow_name": "Manifest demo",
        "workflow_description": "Demo",
        "triggers": [{"trigger_type": "manual"}],
        "steps": [
            {
                "id": "step_1",
                "service": "gmail",
                "action": "send_email",
                "params": {"to": "example@example.com", "subject": "Hello", "text": "Hi"},
            }
        ],
    }
    definition.update(overrides)
    return definition


class CapabilityManifestUnitTests(SimpleTestCase):
    def test_capability_for_step_normalizes_service_and_alias(self):
        step = {"service": "Mailgun", "action": "send_email"}
        self.assertEqual(capability_for_step(step), "gmail:send_email")

    def test_derived_manifest_from_steps(self):
        definition = _base_definition()
        definition["steps"].append(
            {"id": "step_2", "service": "weather", "action": "get_weather", "params": {"city": "Nairobi"}}
        )
        self.assertEqual(
            manifest_from_definition(definition),
            ["gmail:send_email", "weather:get_weather"],
        )

    def test_declared_manifest_wins_over_derived(self):
        definition = _base_definition(capabilities=["gmail:send_email", "weather:get_weather"])
        self.assertEqual(
            manifest_from_definition(definition),
            ["gmail:send_email", "weather:get_weather"],
        )

    def test_validation_rejects_step_outside_declared_manifest(self):
        definition = _base_definition(capabilities=["gmail:send_email"])
        definition["steps"].append(
            {"id": "step_2", "service": "weather", "action": "get_weather", "params": {"city": "Nairobi"}}
        )
        valid, error = validate_workflow_definition(definition)
        self.assertFalse(valid)
        self.assertIn("weather:get_weather", error)

    def test_validation_accepts_declared_manifest_covering_steps(self):
        definition = _base_definition(capabilities=["gmail:send_email"])
        valid, error = validate_workflow_definition(definition)
        self.assertTrue(valid, error)

    def test_validation_rejects_unknown_declared_capability(self):
        definition = _base_definition(capabilities=["gmail:send_email", "weather:make_rain"])
        valid, error = validate_workflow_definition(definition)
        self.assertFalse(valid)
        self.assertIn("weather:make_rain", error)

    def test_delta_flags_added_action_as_widening(self):
        current = _base_definition()
        proposed = _base_definition()
        proposed["steps"].append(
            {"id": "step_2", "service": "weather", "action": "get_weather", "params": {"city": "Nairobi"}}
        )

        delta = capability_delta(current, proposed)
        self.assertTrue(delta["widening"])
        self.assertEqual(delta["added"], ["weather:get_weather"])
        self.assertEqual(approval_kind_for_delta(delta), "escalation")

    def test_prompt_only_change_is_not_widening(self):
        current = _base_definition()
        proposed = _base_definition()
        proposed["steps"][0]["params"]["subject"] = "A friendlier subject"

        delta = capability_delta(current, proposed)
        self.assertFalse(delta["widening"])
        self.assertEqual(delta["added"], [])
        self.assertEqual(approval_kind_for_delta(delta), "fast")

    def test_suggestion_delta_for_added_action(self):
        current = _base_definition()
        proposed_step = {
            "id": "step_2",
            "service": "weather",
            "action": "get_weather",
            "params": {"city": "Nairobi"},
        }
        delta = capability_delta_for_suggestion(current, {"steps": [*current["steps"], proposed_step]})
        self.assertTrue(delta["widening"])

    def test_suggestion_delta_fails_closed_when_scope_unverifiable(self):
        delta = capability_delta_for_suggestion(
            _base_definition(), {"step_id": "step_1", "requires_approval": True}
        )
        self.assertTrue(delta["widening"])
        self.assertEqual(delta["reason"], "__missing__")


class CapabilityExecutorTests(SimpleTestCase):
    def test_out_of_manifest_step_is_denied(self):
        step = {"id": "s2", "service": "weather", "action": "get_weather", "params": {"city": "Nairobi"}}
        context = {
            "user_id": None,
            "room_id": None,
            "preferences": {},
            "workflow": {"id": 1, "capabilities": ["gmail:send_email"]},
        }
        with patch("orchestration.connector_registry.discover_connectors") as mock_discover:
            result = async_to_sync(execute_workflow_step)(step, context)

        self.assertEqual(result.get("status"), "error")
        self.assertIn("capability manifest", result.get("error", ""))
        mock_discover.assert_not_called()

    @patch("orchestration.connector_registry.discover_connectors")
    def test_in_manifest_step_dispatches(self, mock_discover):
        connector = MagicMock()
        connector.execute = AsyncMock(return_value={"status": "success"})
        mock_discover.return_value = {"get_weather": connector}

        step = {"id": "s1", "service": "weather", "action": "get_weather", "params": {"city": "Nairobi"}}
        context = {
            "user_id": None,
            "room_id": None,
            "preferences": {},
            "workflow": {"id": 1, "capabilities": ["weather:get_weather"]},
        }
        result = async_to_sync(execute_workflow_step)(step, context)

        self.assertEqual(result.get("status"), "success")
        connector.execute.assert_awaited_once()


class CapabilityVersioningTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="capowner", email="example@example.com",
            password="fake-token",  # nosec B106 — test fixture — fake credential
        )
        self.workflow = UserWorkflow.objects.create(
            user=self.user,
            name="Manifest demo",
            description="Demo",
            definition=_base_definition(capabilities=["gmail:send_email"]),
            status="active",
        )

    def test_version_stores_declared_manifest(self):
        version = self.workflow.versions.get(version=1)
        self.assertEqual(version.capabilities, ["gmail:send_email"])

    def test_new_version_manifest_reflects_widening(self):
        proposed = _base_definition(capabilities=["gmail:send_email", "weather:get_weather"])
        proposed["steps"].append(
            {"id": "step_2", "service": "weather", "action": "get_weather", "params": {"city": "Nairobi"}}
        )
        version = create_workflow_version(self.workflow, proposed)

        self.assertEqual(version.version, 2)
        self.assertEqual(
            version.capabilities, ["gmail:send_email", "weather:get_weather"]
        )

    def test_improvement_suggestion_gets_code_computed_delta(self):
        from workflows import temporal_integration as ti

        execution = self.workflow.executions.create(
            temporal_workflow_id="wf-manifest-1",
            trigger_type="manual",
            trigger_data={},
            status="completed",
        )
        proposed_step = {
            "id": "step_2",
            "service": "weather",
            "action": "get_weather",
            "params": {"city": "Nairobi"},
        }
        suggestions = [
            {
                "suggestion_type": "add_step",
                "title": "Add weather",
                "summary": "Fetch the weather first.",
                "proposed_changes": {"steps": [*self.workflow.definition["steps"], proposed_step]},
            }
        ]

        async_to_sync(ti.create_improvement_suggestions)(
            self.workflow.id, execution.id, self.user.id, suggestions
        )

        suggestion = WorkflowImprovementSuggestion.objects.get(workflow=self.workflow)
        self.assertTrue(suggestion.capability_delta["widening"])
        self.assertEqual(suggestion.capability_delta["added"], ["weather:get_weather"])
