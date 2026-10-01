"""Versioned workflow definitions (v0.7 W-A, issue #155).

A live ``UserWorkflow.definition`` is never edited in place. Changes go through
``create_workflow_version``, which snapshots the new definition as vN+1, moves
the workflow's ``definition_version`` pointer, and leaves every earlier version
readable. Executions store the version they started with, so an in-flight run
can never be reshaped by a later change.
"""
from __future__ import annotations

import difflib
import json
import logging
from typing import Any, Dict, List, Optional

from django.db import transaction

from .capability_manifest import manifest_from_definition
from .models import UserWorkflow, WorkflowVersion

logger = logging.getLogger(__name__)


def record_initial_version(workflow: UserWorkflow, *, created_by=None) -> WorkflowVersion:
    """Ensure a workflow has a version row for its current definition.

    Idempotent: safe to call from the creation signal and from the data
    migration. Also repairs a workflow whose ``definition_version`` points at a
    missing row (e.g. legacy rows created before versioning landed).
    """
    version = workflow.definition_version or 1
    row, _ = WorkflowVersion.objects.get_or_create(
        workflow=workflow,
        version=version,
        defaults={
            'definition': workflow.definition or {},
            'capabilities': manifest_from_definition(workflow.definition or {}),
            'change_summary': 'Initial definition',
            'created_by': created_by,
        },
    )
    return row


def create_workflow_version(
    workflow: UserWorkflow,
    definition: Dict[str, Any],
    *,
    created_by=None,
    change_summary: str = '',
) -> WorkflowVersion:
    """Append ``definition`` as the next version and point the workflow at it.

    This is the only supported write path for a live definition change. It
    locks the workflow row, writes the immutable snapshot, then updates the
    pointer and the cached ``name``/``description`` mirror.
    """
    if not isinstance(definition, dict):
        raise ValueError("definition must be a dict")

    with transaction.atomic():
        locked = UserWorkflow.objects.select_for_update().get(pk=workflow.pk)
        next_version = (locked.definition_version or 1) + 1
        version = WorkflowVersion.objects.create(
            workflow=locked,
            version=next_version,
            definition=definition,
            capabilities=manifest_from_definition(definition),
            change_summary=(change_summary or '')[:255],
            created_by=created_by,
        )
        locked.definition = definition
        locked.definition_version = next_version
        locked.name = definition.get('workflow_name') or locked.name
        locked.description = definition.get('workflow_description') or locked.description
        locked.save(update_fields=['definition', 'definition_version', 'name', 'description', 'updated_at'])

    workflow.definition = locked.definition
    workflow.definition_version = locked.definition_version
    workflow.name = locked.name
    workflow.description = locked.description
    return version


def definition_for_version(workflow: UserWorkflow, version: int) -> Optional[Dict[str, Any]]:
    """Return the definition snapshot for ``version``, or None if unknown."""
    try:
        version = int(version)
    except (TypeError, ValueError):
        return None
    row = WorkflowVersion.objects.filter(workflow=workflow, version=version).first()
    if row is not None:
        return row.definition
    if workflow.definition_version == version:
        return workflow.definition or {}
    return None


def diff_definition_versions(
    workflow: UserWorkflow,
    from_version: int,
    to_version: int,
) -> Optional[List[str]]:
    """Unified diff between two version snapshots, or None if either is unknown."""
    left = definition_for_version(workflow, from_version)
    right = definition_for_version(workflow, to_version)
    if left is None or right is None:
        return None
    left_lines = _render(left).splitlines()
    right_lines = _render(right).splitlines()
    return list(
        difflib.unified_diff(
            left_lines,
            right_lines,
            fromfile=f"v{from_version}",
            tofile=f"v{to_version}",
            lineterm="",
        )
    )


def _render(definition: Dict[str, Any]) -> str:
    return json.dumps(definition, indent=2, sort_keys=True, default=str)
