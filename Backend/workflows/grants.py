"""Standing grants: per-rule durable permissions (v0.7 W-E, issue #159).

A grant is created only from a human decision on a real approval card. It is
scoped to ``(workflow, workflow version, trigger, capability)`` and can
``allow_once``, ``always_allow``, or ``deny``. Ask-first (an explicit user
override) always wins over any allow grant. Grants lapse on use (allow-once),
expiry, an out-of-scope attempt, or a new workflow version — never silently.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from django.conf import settings
from django.utils import timezone

from .capability_manifest import capability_for_step, manifest_from_definition
from .models import StandingGrant, UserWorkflow, WorkflowApprovalRecord

logger = logging.getLogger(__name__)

LAPSE_USED_ONCE = "used_once"
LAPSE_EXPIRED = "expired"
LAPSE_OUT_OF_SCOPE = "out_of_scope_action"
LAPSE_VERSION_CHANGED = "version_changed"
LAPSE_REVOKED = "revoked"


def grant_lifetime_days() -> int:
    try:
        return max(1, int(getattr(settings, "STANDING_GRANT_LIFETIME_DAYS", 30) or 30))
    except (TypeError, ValueError):
        return 30


def lapse_grant(grant: StandingGrant, reason: str) -> None:
    grant.status = "lapsed"
    grant.lapse_reason = reason[:255]
    grant.lapsed_at = timezone.now()
    grant.save(update_fields=["status", "lapse_reason", "lapsed_at", "updated_at"])


def revoke_grant(grant: StandingGrant) -> None:
    grant.status = "revoked"
    grant.lapse_reason = LAPSE_REVOKED
    grant.lapsed_at = timezone.now()
    grant.save(update_fields=["status", "lapse_reason", "lapsed_at", "updated_at"])


def lapse_grants_for_workflow(workflow: UserWorkflow, reason: str) -> int:
    grants = list(StandingGrant.objects.filter(workflow=workflow, status="active"))
    for grant in grants:
        lapse_grant(grant, reason)
    return len(grants)


def _trigger_matches(grant: StandingGrant, trigger_type: str, trigger_id: Any) -> bool:
    scope = grant.trigger_scope or {}
    if not scope:
        return True
    scoped_type = str(scope.get("trigger_type") or "").strip().lower()
    if scoped_type and scoped_type != str(trigger_type or "").strip().lower():
        return False
    if scope.get("trigger_id") is not None:
        try:
            scoped_id = int(scope.get("trigger_id"))
            actual_id = int(trigger_id)
        except (TypeError, ValueError):
            return False
        if scoped_id != actual_id:
            return False
    return True


def _grant_covers(
    grant: StandingGrant,
    definition_version: int,
    trigger_type: str,
    trigger_id: Any,
    capability: str,
) -> bool:
    if grant.workflow_version != int(definition_version or 1):
        return False
    if not _trigger_matches(grant, trigger_type, trigger_id):
        return False
    if capability and capability not in set(grant.capability_scope or []):
        return False
    if grant.expires_at and grant.expires_at <= timezone.now():
        lapse_grant(grant, LAPSE_EXPIRED)
        return False
    return True


def user_asks_first(user_id: Optional[int], action: str) -> bool:
    """An explicit per-action ``always`` override wins over any allow grant."""
    if not user_id or not action:
        return False
    try:
        from orchestration.user_preferences import get_user_preferences

        overrides = (get_user_preferences(user_id) or {}).get("approval_overrides") or {}
    except Exception:
        return False
    return overrides.get(action) == "always"


def resolve_standing_grant(
    workflow_id: int,
    definition_version: int,
    trigger_type: str,
    trigger_id: Any,
    capability: str,
    action: str,
    user_id: Optional[int],
) -> Optional[Dict[str, Any]]:
    """The deterministic grant gate the workflow run consults before pausing.

    Returns ``None`` to keep the normal ask path, or a serialized grant
    decision. Out-of-scope attempts lapse the offending grants.
    """
    workflow = UserWorkflow.objects.filter(id=workflow_id).first()
    if workflow is None:
        return None

    if user_asks_first(user_id, action):
        return None

    grants = list(workflow.standing_grants.filter(status="active").order_by("created_at"))
    for grant in grants:
        if not _grant_covers(grant, definition_version, trigger_type, trigger_id, capability):
            continue
        return {"grant_id": grant.id, "decision": grant.decision}

    for grant in grants:
        if grant.status != "active":
            continue
        if grant.workflow_version != int(definition_version or 1):
            continue
        if not _trigger_matches(grant, trigger_type, trigger_id):
            continue
        if capability and capability not in set(grant.capability_scope or []):
            # Only lapse when the capability is genuinely new relative to the
            # version the grant was approved under. A different pre-existing
            # approval step is simply not covered by this grant, not out of
            # scope.
            if capability not in _manifest_for_grant_version(grant):
                lapse_grant(grant, LAPSE_OUT_OF_SCOPE)

    return None


def _manifest_for_grant_version(grant: StandingGrant) -> set:
    from .models import WorkflowVersion

    version = WorkflowVersion.objects.filter(
        workflow_id=grant.workflow_id, version=grant.workflow_version
    ).first()
    if version is None:
        return set()
    return set(version.capabilities or [])


def _step_for_approval(workflow: UserWorkflow, approval: WorkflowApprovalRecord) -> Optional[Dict[str, Any]]:
    from .runtime import get_step_id

    for index, step in enumerate(workflow.get_steps()):
        if get_step_id(step, index) == approval.step_id:
            return step
    return None


def create_grant_from_approval(
    approval: WorkflowApprovalRecord,
    *,
    decision: str = "always_allow",
) -> StandingGrant:
    """Turn a human approval into a standing grant (never self-authorized).

    Reuses the same approval row: the grant traces back to the decision the
    human made. Routine triggers additionally require the W-F test-run gate.
    """
    from .routine import has_event_trigger, routine_ready_for_grant

    if approval.workflow_id is None:
        raise ValueError("This approval is not attached to a workflow.")
    if decision not in {"allow_once", "always_allow", "deny"}:
        raise ValueError(f"Unknown grant decision: {decision}")

    workflow = approval.workflow
    step = _step_for_approval(workflow, approval)
    if step is None:
        raise ValueError(f"No step '{approval.step_id}' in the current definition.")

    capability = capability_for_step(step)
    manifest = set(manifest_from_definition(workflow.definition or {}))
    if capability and manifest and capability not in manifest:
        raise ValueError("The step is outside the current capability manifest.")

    if has_event_trigger(workflow.definition or {}):
        ready, reason = routine_ready_for_grant(workflow)
        if not ready:
            raise ValueError(f"Routine is not ready for a standing grant: {reason}")

    trigger_type = str((approval.metadata or {}).get("trigger_type") or "manual").strip().lower()
    grant = StandingGrant.objects.create(
        user_id=approval.requested_by_id,
        workflow=workflow,
        workflow_version=workflow.definition_version or 1,
        trigger_scope={"trigger_type": trigger_type},
        capability_scope=[capability] if capability else [],
        decision=decision,
        approval_record=approval,
        expires_at=timezone.now() + timezone.timedelta(days=grant_lifetime_days()),
    )
    return grant


def apply_grant_decision(
    *,
    execution_id: int,
    workflow_id: int,
    user_id: Optional[int],
    step_id: str,
    service: str,
    action: str,
    params: Optional[Dict[str, Any]],
    grant_id: int,
    decision: str,
) -> int:
    """Write the receipt for an autonomous run and consume allow-once grants."""
    workflow = UserWorkflow.objects.filter(id=workflow_id).first()
    owner_id = (user_id or (workflow.user_id if workflow else None))
    if owner_id is None:
        raise ValueError("Cannot record a grant receipt without a user.")

    record = WorkflowApprovalRecord.objects.create(
        workflow_id=workflow_id,
        execution_id=execution_id,
        requested_by_id=owner_id,
        reviewed_by_id=user_id or None,
        kind="workflow",
        step_id=step_id,
        service=service or "",
        action=action or "",
        sanitized_params=params or {},
        status="approved" if decision != "deny" else "rejected",
        review_comment=(
            "Approved by standing rule"
            if decision != "deny"
            else "Denied by standing rule"
        ),
        metadata={"standing_grant_id": grant_id, "auto": True, "decision": decision},
        reviewed_at=timezone.now(),
    )

    grant = StandingGrant.objects.filter(id=grant_id).first()
    if grant and grant.decision == "allow_once":
        lapse_grant(grant, LAPSE_USED_ONCE)
    return record.id


def sweep_expired_grants(now=None) -> Dict[str, int]:
    now = now or timezone.now()
    lapsed = 0
    for grant in StandingGrant.objects.filter(status="active", expires_at__lte=now):
        lapse_grant(grant, LAPSE_EXPIRED)
        lapsed += 1
    return {"lapsed": lapsed}


def active_grants_for_user(user_id: int) -> List[StandingGrant]:
    return list(
        StandingGrant.objects.filter(user_id=user_id, status="active")
        .select_related("workflow")
        .order_by("-created_at")
    )
