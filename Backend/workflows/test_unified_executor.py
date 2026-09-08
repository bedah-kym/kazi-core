"""Phase 2 — workflow steps dispatch through the connector registry."""
from unittest.mock import AsyncMock, MagicMock, patch

from asgiref.sync import async_to_sync
from django.test import SimpleTestCase

from workflows.activity_executors import execute_workflow_step


class WorkflowExecutorUnifiedDispatchTests(SimpleTestCase):
    """The unified registry dispatch is the single source of truth for
    workflow step execution (removed the hardcoded action maps)."""

    @patch("orchestration.connector_registry.discover_connectors")
    def test_catalog_action_dispatches_via_registry(self, mock_discover):
        mock_connector = MagicMock()
        mock_connector.execute = AsyncMock(
            return_value={"status": "success", "data": {"temp": 22}}
        )
        mock_discover.return_value = {"get_weather": mock_connector}

        step = {
            "id": "t",
            "service": "weather",
            "action": "get_weather",
            "params": {"city": "Nairobi"},
        }
        result = async_to_sync(execute_workflow_step)(
            step, {"user_id": None, "room_id": None, "preferences": {}}
        )

        self.assertEqual(result.get("status"), "success")
        mock_connector.execute.assert_awaited_once()
        params = mock_connector.execute.await_args[0][0]
        self.assertEqual(params.get("action"), "get_weather")

    @patch("orchestration.connector_registry.discover_connectors")
    def test_connector_only_action_is_not_a_workflow_step(self, mock_discover):
        with patch(
            "workflows.activity_executors.get_action_definition",
            return_value={"service": "telegram", "action": "send_telegram_message"},
        ):
            step = {
                "id": "t",
                "service": "telegram",
                "action": "send_telegram_message",
                "params": {"message": "hi"},
            }
            result = async_to_sync(execute_workflow_step)(
                step, {"user_id": None, "room_id": None, "preferences": {}}
            )

        self.assertEqual(result.get("status"), "error")
        self.assertIn("not available", result.get("error", ""))
        mock_discover.assert_not_called()
