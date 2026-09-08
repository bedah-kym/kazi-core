"""Phase 2/3 — unified orchestration: workflow handoff + lazy Celery load."""
from unittest.mock import AsyncMock, MagicMock, patch

from asgiref.sync import async_to_sync
from django.test import SimpleTestCase


class WorkflowHandoffTests(SimpleTestCase):
    """_create_workflow_handoff routes through execute_adhoc_workflow with
    the validated definition shape (Phase 3)."""

    @patch(
        "orchestration.workflow_planner.execute_adhoc_workflow",
        new_callable=AsyncMock,
    )
    def test_handoff_manual_routes_through_adhoc_workflow(self, mock_exec):
        mock_exec.return_value = {
            "status": "completed",
            "message": "done",
            "workflow": MagicMock(id=5),
        }

        from orchestration.agent_loop import _create_workflow_handoff

        result = async_to_sync(_create_workflow_handoff)(
            {
                "description": "Do a thing",
                "steps": [
                    {
                        "id": "s1",
                        "service": "weather",
                        "action": "get_weather",
                        "params": {},
                    }
                ],
                "trigger_type": "manual",
            },
            {"user_id": 1, "room_id": 2},
        )

        self.assertEqual(result["status"], "success")
        self.assertEqual(result["workflow_id"], 5)
        definition = mock_exec.await_args[0][0]
        self.assertEqual(definition["workflow_name"], "Do a thing")
        self.assertIn("workflow_description", definition)
        self.assertEqual(definition["triggers"], [{"trigger_type": "manual"}])
        self.assertEqual(len(definition["steps"]), 1)

    def test_handoff_rejects_invalid_steps_json(self):
        from orchestration.agent_loop import _create_workflow_handoff

        result = async_to_sync(_create_workflow_handoff)(
            {"description": "Do a thing", "steps": "not-json", "trigger_type": "manual"},
            {"user_id": 1},
        )
        self.assertEqual(result["status"], "error")


class BackendCeleryLazyLoadTests(SimpleTestCase):
    """Backend/__init__.py lazy-loads celery_app (the manage.py check fix)."""

    def test_unknown_attribute_raises(self):
        import Backend

        with self.assertRaises(AttributeError):
            Backend.__getattr__("nonexistent_attribute_xyz")

    def test_celery_app_attribute_is_lazy_imported(self):
        from Backend import celery_app

        self.assertIsNotNone(celery_app)
