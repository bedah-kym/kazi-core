import asyncio
import os
from unittest.mock import AsyncMock, MagicMock, patch

from asgiref.sync import async_to_sync
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
        # plan_user_request uses generate_json() (v0.4.2 validated-output path),
        # not generate_text()+extract_json().
        mock_llm.generate_json = AsyncMock(
            return_value={
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
        )
        mock_get_llm.return_value = mock_llm

        from orchestration.workflow_planner import plan_user_request

        result = async_to_sync(plan_user_request)(
            "Check Nairobi weather then convert 100 USD to KES",
            history_text="",
            user_id=None,
            preferences={},
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
    @patch.dict(os.environ, {"KAZI_DEMO_MODE": "false"}, clear=False)
    def test_echo_connector_is_disabled_by_default(self):
        # v0.4 M5-2: example connectors are off unless KAZI_DEMO_MODE is set.
        from orchestration.connector_registry import discover_connectors, reset_registry

        reset_registry()
        connectors = discover_connectors()
        self.assertNotIn("echo", connectors)

    @patch.dict(os.environ, {"KAZI_DEMO_MODE": "true"}, clear=False)
    def test_echo_connector_loads_in_demo_mode(self):
        # v0.4 M5-3: examples/connectors/echo/ is auto-discovered when demo mode is on.
        from orchestration.connector_registry import discover_connectors, reset_registry

        reset_registry()
        connectors = discover_connectors()
        self.assertIn("echo", connectors)
        self.assertEqual(connectors["echo"].name, "echo")

    @patch.dict(os.environ, {"KAZI_DEMO_MODE": "false"}, clear=False)
    def test_router_integrity_still_passes_after_dynamic_discovery(self):
        from orchestration.connector_registry import discover_connectors, reset_registry
        from orchestration.mcp_router import MCPRouter

        reset_registry()
        discover_connectors()
        MCPRouter()

    @patch.dict(os.environ, {"KAZI_DEMO_MODE": "false"}, clear=False)
    def test_router_consumes_registry_as_single_source(self):
        # v0.4 M2-1: MCPRouter no longer maintains its own action->connector dict;
        # it must reflect exactly what connector_registry.discover_connectors() exposes.
        from orchestration.connector_registry import discover_connectors, reset_registry
        from orchestration.mcp_router import MCPRouter

        reset_registry()
        registry_map = discover_connectors()
        router = MCPRouter()

        self.assertEqual(set(router.connectors.keys()), set(registry_map.keys()))
        for action, connector in registry_map.items():
            self.assertIs(router.connectors[action], connector)
