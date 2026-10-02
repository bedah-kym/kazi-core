"""Tests for shadow replay (v0.7 W-D2, issue #169)."""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

from asgiref.sync import async_to_sync
from django.contrib.auth import get_user_model
from django.test import TestCase

from workflows.models import UserWorkflow, WorkflowExecution
from workflows.shadow_replay import (
    collect_replay_inputs,
    replay_candidate,
    shadow_replay_window,
)

User = get_user_model()


def _definition(**overrides):
    definition = {
        "workflow_name": "Shadow demo",
        "workflow_description": "Demo",
        "triggers": [{"trigger_type": "manual"}],
        "steps": [
            {
                "id": "step_1",
                "service": "weather",
                "action": "get_weather",
                "params": {"city": "Nairobi"},
            }
        ],
    }
    definition.update(overrides)
    return definition


class ShadowReplayTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="shadowuser", email="example@example.com",
            password="fake-token",  # nosec B106 — test fixture — fake credential
        )
        self.workflow = UserWorkflow.objects.create(
            user=self.user,
            name="Shadow demo",
            description="Demo",
            definition=_definition(),
            status="active",
        )
        self.recorded = WorkflowExecution.objects.create(
            workflow=self.workflow,
            temporal_workflow_id="wf-shadow-1",
            trigger_type="manual",
            trigger_data={"city": "Nairobi"},
            status="completed",
            result={"step_1": {"status": "success", "results": []}},
        )

    def test_collect_replay_inputs_uses_recorded_context(self):
        inputs = collect_replay_inputs(self.workflow, k=5)

        self.assertEqual(len(inputs), 1)
        self.assertEqual(inputs[0]["execution_id"], self.recorded.id)
        self.assertIn("step_1", inputs[0]["seed"])

    def test_shadow_replay_window_from_definition_config(self):
        workflow = UserWorkflow.objects.create(
            user=self.user,
            name="Configured window",
            description="Demo",
            definition=_definition(config={"shadow_replay_window": 3}),
            status="active",
        )
        self.assertEqual(shadow_replay_window(workflow.definition), 3)

    @patch("orchestration.connector_registry.discover_connectors")
    def test_passing_candidate_replays_cleanly(self, mock_discover):
        connector = MagicMock()
        connector.execute = AsyncMock(return_value={"status": "success"})
        mock_discover.return_value = {"get_weather": connector}

        result = async_to_sync(replay_candidate)(self.workflow, self.workflow.definition)

        self.assertTrue(result["passed"])
        self.assertEqual(result["replays"], 1)
        self.assertEqual(result["regressions"], [])

    @patch("orchestration.connector_registry.discover_connectors")
    def test_regressing_candidate_is_detected(self, mock_discover):
        connector = MagicMock()
        connector.execute = AsyncMock(return_value={"status": "error", "error": "source missing"})
        mock_discover.return_value = {"get_weather": connector}

        result = async_to_sync(replay_candidate)(self.workflow, self.workflow.definition)

        self.assertFalse(result["passed"])
        self.assertEqual(result["regressions"][0]["steps"], ["step_1"])

    def test_no_recorded_inputs_means_no_regression(self):
        self.recorded.delete()
        result = async_to_sync(replay_candidate)(self.workflow, self.workflow.definition)
        self.assertTrue(result["passed"])
        self.assertEqual(result["replays"], 0)
