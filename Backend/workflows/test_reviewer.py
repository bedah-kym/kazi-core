"""Tests for the reviewer agent (v0.7 W-D/W-D2, issues #158/#169)."""
from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

from asgiref.sync import async_to_sync
from django.contrib.auth import get_user_model
from django.test import TestCase

from workflows.models import (
    UserWorkflow,
    WorkflowExecution,
    WorkflowImprovementSuggestion,
    WorkflowVersion,
)
from workflows.reviewer import accept_suggestion, report_suggestion_outcomes, review_workflow

User = get_user_model()


def _definition(**overrides):
    definition = {
        "workflow_name": "Review demo",
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


class _FakeLLM:
    def __init__(self, payload):
        self._payload = payload
        self.kwargs = []

    async def generate_text(self, **kwargs):
        self.kwargs.append(kwargs)
        return json.dumps(self._payload)

    def extract_json(self, text):
        return json.loads(text)


class ReviewerTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="reviewowner", email="example@example.com",
            password="fake-token",  # nosec B106 — test fixture — fake credential
        )
        self.workflow = UserWorkflow.objects.create(
            user=self.user,
            name="Review demo",
            description="Demo",
            definition=_definition(),
            status="active",
        )
        WorkflowExecution.objects.create(
            workflow=self.workflow,
            temporal_workflow_id="wf-review-1",
            trigger_type="manual",
            trigger_data={"city": "Nairobi"},
            status="completed",
            result={"step_1": {"status": "success", "results": []}},
        )

    def _suggestion_payload(self, proposed_definition):
        return {
            "suggestions": [
                {
                    "title": "Add a second weather check",
                    "summary": "Cross-check Mombasa before replying.",
                    "cited_metric": "duration",
                    "proposed_changes": {"definition": proposed_definition},
                }
            ]
        }

    @patch("orchestration.connector_registry.discover_connectors")
    def test_passing_suggestion_is_proposed_with_shadow_evidence(self, mock_discover):
        connector = MagicMock()
        connector.execute = AsyncMock(return_value={"status": "success"})
        mock_discover.return_value = {"get_weather": connector}
        proposed = _definition()
        proposed["steps"].append({
            "id": "step_2",
            "service": "gmail",
            "action": "send_email",
            "params": {"to": "example@example.com", "subject": "Hi", "text": "Hello"},
            "requires_approval": True,
        })

        suggestions = async_to_sync(review_workflow)(
            self.workflow, llm=_FakeLLM(self._suggestion_payload(proposed))
        )

        self.assertEqual(len(suggestions), 1)
        suggestion = suggestions[0]
        self.assertEqual(suggestion.status, "proposed")
        self.assertEqual(suggestion.suggestion_type, "reviewer")
        self.assertGreaterEqual(suggestion.metadata["shadow"]["replays"], 1)
        self.assertEqual(suggestion.metadata["cited_metric"], "duration")
        self.assertTrue(suggestion.capability_delta["widening"])

    @patch("orchestration.connector_registry.discover_connectors")
    def test_regressing_suggestion_is_auto_rejected(self, mock_discover):
        connector = MagicMock()
        connector.execute = AsyncMock(return_value={"status": "error", "error": "source missing"})
        mock_discover.return_value = {"get_weather": connector}

        suggestions = async_to_sync(review_workflow)(
            self.workflow, llm=_FakeLLM(self._suggestion_payload(_definition()))
        )

        suggestion = suggestions[0]
        self.assertEqual(suggestion.status, "dismissed")
        self.assertIn("shadow replay", suggestion.metadata["auto_rejected_reason"])

    def test_unresolvable_suggestion_is_auto_rejected(self):
        payload = {
            "suggestions": [
                {
                    "title": "Vague change",
                    "summary": "Do better.",
                    "cited_metric": "duration",
                    "proposed_changes": {"step_id": "step_1", "hint": "improve"},
                }
            ]
        }
        suggestions = async_to_sync(review_workflow)(self.workflow, llm=_FakeLLM(payload))

        suggestion = suggestions[0]
        self.assertEqual(suggestion.status, "dismissed")
        self.assertIn("could not be resolved", suggestion.metadata["auto_rejected_reason"])

    def test_reviewer_uses_the_reviewer_model_role(self):
        llm = _FakeLLM(self._suggestion_payload(_definition()))
        async_to_sync(review_workflow)(self.workflow, llm=llm)

        self.assertTrue(llm.kwargs)
        self.assertEqual(llm.kwargs[0].get("model_role"), "reviewer")

    @patch("orchestration.connector_registry.discover_connectors")
    def test_accept_creates_a_new_version(self, mock_discover):
        connector = MagicMock()
        connector.execute = AsyncMock(return_value={"status": "success"})
        mock_discover.return_value = {"get_weather": connector}
        proposed = _definition()
        proposed["workflow_name"] = "Review demo v2"
        suggestions = async_to_sync(review_workflow)(
            self.workflow, llm=_FakeLLM(self._suggestion_payload(proposed))
        )

        version = accept_suggestion(suggestions[0], by_user=self.user)

        self.workflow.refresh_from_db()
        self.assertEqual(version.version, 2)
        self.assertEqual(self.workflow.definition_version, 2)
        self.assertEqual(self.workflow.definition["workflow_name"], "Review demo v2")
        self.assertEqual(WorkflowVersion.objects.filter(workflow=self.workflow).count(), 2)
        suggestions[0].refresh_from_db()
        self.assertEqual(suggestions[0].status, "accepted")
        self.assertEqual(suggestions[0].metadata["accepted_version"], 2)

    def test_accept_invalid_proposal_raises(self):
        suggestion = WorkflowImprovementSuggestion.objects.create(
            workflow=self.workflow,
            user=self.user,
            suggestion_type="reviewer",
            title="Broken",
            summary="Broken proposal.",
            proposed_changes={"steps": [{"id": "x", "service": "weather", "action": "make_rain"}]},
            status="proposed",
        )
        with self.assertRaises(ValueError):
            accept_suggestion(suggestion, by_user=self.user)

    @patch("workflows.reviewer.record_event")
    def test_report_outcomes_logs_metric_check(self, mock_record):
        suggestion = WorkflowImprovementSuggestion.objects.create(
            workflow=self.workflow,
            user=self.user,
            suggestion_type="reviewer",
            title="Done",
            summary="Done.",
            proposed_changes={},
            status="accepted",
            metadata={"cited_metric": "step_count", "metric_before": 1.0},
        )
        outcomes = report_suggestion_outcomes(self.workflow)

        self.assertEqual(len(outcomes), 1)
        self.assertEqual(outcomes[0]["suggestion_id"], suggestion.id)
        self.assertEqual(outcomes[0]["metric"], "step_count")
        self.assertTrue(mock_record.called)
