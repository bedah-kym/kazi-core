import asyncio
import os
from unittest.mock import AsyncMock, MagicMock, patch

from django.test import SimpleTestCase, override_settings


def run_async(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


class WorkflowPlannerUngatingTests(SimpleTestCase):
    @override_settings(MANAGER_LLM_ENABLED=False)
    @patch("orchestration.workflow_planner.get_llm_client")
    def test_plan_user_request_returns_real_adhoc_workflow(self, mock_get_llm):
        mock_llm = MagicMock()
        mock_llm.generate_text = AsyncMock(return_value='{"mode":"adhoc_workflow"}')
        mock_llm.extract_json.return_value = {
            "mode": "adhoc_workflow",
            "assistant_message": "Working on it.",
            "confidence": 0.95,
            "steps": [
                {
                    "id": "step_1",
                    "service": "weather",
                    "action": "get_weather",
                    "params": {"city": "Nairobi"},
                },
                {
                    "id": "step_2",
                    "service": "currency",
                    "action": "convert_currency",
                    "params": {
                        "amount": 100,
                        "from_currency": "USD",
                        "to_currency": "KES",
                    },
                },
            ],
        }
        mock_get_llm.return_value = mock_llm

        from orchestration.workflow_planner import plan_user_request

        result = run_async(
            plan_user_request(
                "Check Nairobi weather then convert 100 USD to KES",
                history_text="",
                user_id=None,
                preferences={},
            )
        )
        self.assertEqual(result.get("mode"), "adhoc_workflow")
        definition = result.get("workflow_definition") or {}
        steps = definition.get("steps") or []
        self.assertGreaterEqual(len(steps), 2)
        actions = [step.get("action") for step in steps if isinstance(step, dict)]
        self.assertIn("get_weather", actions)
        self.assertIn("convert_currency", actions)


class ManagerVerifierUngatingTests(SimpleTestCase):
    def test_manager_verifier_reorders_delivery_after_results(self):
        from orchestration.manager_verifier import ManagerVerifier

        steps = [
            {
                "id": "step_1",
                "service": "gmail",
                "action": "send_email",
                "params": {
                    "to": "user@example.com",
                    "subject": "Travel options",
                    "text": "Please send results",
                },
            },
            {
                "id": "step_2",
                "service": "search",
                "action": "search_info",
                "params": {"query": "best Nairobi to Mombasa flights"},
            },
        ]

        review = ManagerVerifier().review_steps(steps, "Find flights and email me the results")
        self.assertEqual(review.get("verdict"), "approve")
        reviewed_steps = review.get("steps") or []
        self.assertEqual(reviewed_steps[0].get("action"), "search_info")
        self.assertEqual(reviewed_steps[1].get("action"), "send_email")
        self.assertEqual(reviewed_steps[1].get("depends_on"), [reviewed_steps[0].get("id")])


class ConnectorRegistryUngatingTests(SimpleTestCase):
    @patch.dict(os.environ, {"KAZI_ENABLE_EXAMPLE_CONNECTOR": "false"}, clear=False)
    def test_example_connector_is_disabled_by_default(self):
        from orchestration.connector_registry import discover_connectors, reset_registry

        reset_registry()
        connectors = discover_connectors()
        self.assertNotIn("echo", connectors)

    @patch.dict(os.environ, {"KAZI_ENABLE_EXAMPLE_CONNECTOR": "false"}, clear=False)
    def test_router_integrity_still_passes_after_dynamic_discovery(self):
        from orchestration.connector_registry import discover_connectors, reset_registry
        from orchestration.mcp_router import MCPRouter

        reset_registry()
        discover_connectors()
        MCPRouter()
