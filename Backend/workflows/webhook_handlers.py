"""Service-specific webhook handlers for workflow triggers."""
from typing import Any, Dict, Optional
from asgiref.sync import async_to_sync

from users.models import CalendlyProfile

from .models import WorkflowTrigger
from .temporal_integration import start_workflow_execution


def _extract_calendly_owner_uri(payload: Dict[str, Any]) -> Optional[str]:
    for path in [
        ("payload", "config", "webhook_subscription", "owner"),
        ("event", "payload", "config", "webhook_subscription", "owner"),
        ("config", "webhook_subscription", "owner"),
    ]:
        current = payload
        found = True
        for key in path:
            if not isinstance(current, dict) or key not in current:
                found = False
                break
            current = current[key]
        if found and isinstance(current, str):
            return current
    return None




def _extract_calendly_subscription_id(payload: Dict[str, Any]) -> Optional[str]:
    for path in [
        ("payload", "config", "webhook_subscription", "uuid"),
        ("event", "payload", "config", "webhook_subscription", "uuid"),
        ("config", "webhook_subscription", "uuid"),
    ]:
        current = payload
        found = True
        for key in path:
            if not isinstance(current, dict) or key not in current:
                found = False
                break
            current = current[key]
        if found and isinstance(current, str):
            return current
    return None


def handle_calendly_webhook_event(payload: Dict[str, Any]) -> None:
    event = payload.get('event') or {}
    event_type = event.get('type')
    if not event_type:
        return

    owner_uri = _extract_calendly_owner_uri(payload)
    profile = None

    if owner_uri and hasattr(CalendlyProfile, 'calendly_user_uri'):
        profile = CalendlyProfile.objects.filter(calendly_user_uri=owner_uri).first()

    if not profile:
        return

    _trigger_workflows_for_event(profile.user_id, 'calendly', event_type, payload)


def handle_intasend_webhook_event(user_id: int, payload: Dict[str, Any]) -> None:
    state = payload.get('state')
    if state == 'COMPLETE':
        event = 'payment.completed'
    elif state in ('FAILED', 'CANCELLED'):
        event = 'payment.failed'
    else:
        event = 'payment.updated'

    _trigger_workflows_for_event(user_id, 'payments', event, payload)


def _trigger_workflows_for_event(user_id: int, service: str, event: str, payload: Dict[str, Any]) -> None:
    triggers = WorkflowTrigger.objects.filter(
        workflow__user_id=user_id,
        trigger_type='webhook',
        service=service,
        event=event,
        is_active=True
    )

    for trigger in triggers:
        async_to_sync(start_workflow_execution)(
            trigger.workflow,
            trigger_data={
                'event': event,
                'payload': payload
            },
            trigger_type='webhook'
        )
