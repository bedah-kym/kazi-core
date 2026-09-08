"""Contract snapshot test for the execution-detail endpoint.

Locks the serialized shape produced by ``workflows.views._serialize_execution``
to the keys documented in ``docs/contracts/execution-detail.md`` so the doc and
the serializer cannot drift apart silently.
"""
from datetime import datetime, timezone

from django.test import SimpleTestCase

from workflows.models import UserWorkflow, WorkflowExecution
from workflows.views import _serialize_execution


class ExecutionDetailContractTests(SimpleTestCase):
    """The v0.4.x execution-detail contract keys."""

    CONTRACT_KEYS = {
        "id",
        "workflow_id",
        "status",
        "current_step",
        "last_completed_step",
        "waiting_on",
        "attempts",
        "trigger_type",
        "trigger_data",
        "result_summary",
        "receipt_ids",
        "pending_approval",
        "temporal_ids",
        "failure_summary",
        "recovery_suggestion",
        "result",
        "error_message",
        "started_at",
        "completed_at",
    }

    # Fields a previous contract draft claimed but the shipped serializer
    # does NOT expose. Kept explicit so reintroducing them without updating
    # the contract fails loudly.
    ABSENT_KEYS = {
        "workflow_name",
        "waiting_reason",
        "receipts",
        "replay_hints",
        "dead_letter_reason",
        "updated_at",
    }

    def _make_execution(self):
        workflow = UserWorkflow(id=1, name="demo", definition={}, status="active")
        execution = WorkflowExecution(
            id=42,
            workflow_id=1,
            status="running",
            trigger_type="manual",
            temporal_workflow_id="twf-1",
            temporal_run_id=None,
            result_summary="step completed",
        )
        execution.workflow = workflow
        execution.pending_approval = None
        execution.started_at = datetime(2026, 5, 7, 4, 21, 29, tzinfo=timezone.utc)
        execution.completed_at = None
        return execution

    def test_serializer_exposes_contract_keys(self):
        out = _serialize_execution(self._make_execution())
        for key in self.CONTRACT_KEYS:
            self.assertIn(key, out, f"missing contract key: {key}")

    def test_serializer_does_not_expose_removed_keys(self):
        out = _serialize_execution(self._make_execution())
        for key in self.ABSENT_KEYS:
            self.assertNotIn(key, out, f"unexpected key in serializer: {key}")

    def test_status_enum_matches_contract(self):
        execution = self._make_execution()
        for status in ("pending", "running", "waiting", "completed", "failed", "cancelled"):
            execution.status = status
            out = _serialize_execution(execution)
            self.assertEqual(out["status"], status)
