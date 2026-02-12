"""Workflow chat agent for @mathia workflow creation."""
import json
import logging
from typing import Dict, Any, Optional
from asgiref.sync import sync_to_async

from orchestration.llm_client import get_llm_client

from .capabilities import get_capabilities_prompt, validate_workflow_definition
from .models import WorkflowDraft, UserWorkflow, WorkflowTrigger
from .temporal_integration import create_schedule_for_trigger

logger = logging.getLogger(__name__)

_CONFIRM_WORDS = {
    'yes', 'approve', 'approved', 'confirm', 'confirmed', 'create', 'create it', 'looks good', 'go ahead'
}
_CANCEL_WORDS = {'cancel', 'stop', 'never mind', 'discard'}


def _is_confirmation(message: str) -> bool:
    lowered = message.strip().lower()
    return any(word in lowered for word in _CONFIRM_WORDS)


def _is_cancellation(message: str) -> bool:
    lowered = message.strip().lower()
    return any(word in lowered for word in _CANCEL_WORDS)


def _format_summary(definition: Dict[str, Any]) -> str:
    triggers = definition.get('triggers', [])
    steps = definition.get('steps', [])
    lines = [
        f"Workflow: {definition.get('workflow_name', 'Unnamed')}",
        f"Description: {definition.get('workflow_description', '')}",
        "Triggers:"
    ]
    for trig in triggers:
        service = trig.get('service', 'manual')
        event = trig.get('event', '')
        lines.append(f"- {service} {event}".strip())
    lines.append("Steps:")
    for step in steps:
        lines.append(f"- {step.get('id', '')}: {step.get('service')} {step.get('action')}")
    return "\n".join(lines)


async def _get_active_draft(user_id: int, room_id: Optional[int]) -> Optional[WorkflowDraft]:
    def _fetch():
        qs = WorkflowDraft.objects.filter(user_id=user_id, status__in=['draft', 'awaiting_confirmation'])
        if room_id:
            qs = qs.filter(room_id=room_id)
        return qs.order_by('-updated_at').first()
    return await sync_to_async(_fetch)()


async def _save_draft(user_id: int, room_id: Optional[int], definition: Dict[str, Any]) -> WorkflowDraft:
    def _save():
        draft = WorkflowDraft.objects.filter(user_id=user_id, room_id=room_id, status__in=['draft', 'awaiting_confirmation']).first()
        if not draft:
            draft = WorkflowDraft(user_id=user_id, room_id=room_id)
        draft.definition = definition
        draft.status = 'awaiting_confirmation'
        draft.save()
        return draft
    return await sync_to_async(_save)()


async def _close_draft(draft: WorkflowDraft, status: str) -> None:
    def _update():
        draft.status = status
        draft.save(update_fields=['status'])
    await sync_to_async(_update)()


async def _create_workflow(user_id: int, room_id: Optional[int], definition: Dict[str, Any], draft: WorkflowDraft) -> UserWorkflow:
    def _create():
        workflow = UserWorkflow.objects.create(
            user_id=user_id,
            name=definition.get('workflow_name', 'Untitled Workflow'),
            description=definition.get('workflow_description', ''),
            definition=definition,
            status='active',
            created_from_room_id=room_id,
            created_from_draft=draft,
        )
        return workflow

    workflow = await sync_to_async(_create)()
    await _register_triggers(workflow)
    return workflow


async def _register_triggers(workflow: UserWorkflow) -> None:
    triggers = workflow.definition.get('triggers', [])

    for trig in triggers:
        trigger_type = trig.get('trigger_type')
        service = trig.get('service', '')
        event = trig.get('event', '')
        config = trig.get('config', {}) or {}

        if not trigger_type:
            if service == 'schedule' or event == 'cron':
                trigger_type = 'schedule'
            elif service and event:
                trigger_type = 'webhook'
            else:
                trigger_type = 'manual'

        def _create_trigger():
            return WorkflowTrigger.objects.create(
                workflow=workflow,
                trigger_type=trigger_type,
                service=service,
                event=event,
                config=config,
                schedule_cron=trig.get('cron') or config.get('cron'),
                schedule_timezone=trig.get('timezone') or config.get('timezone', 'UTC'),
            )

        trigger = await sync_to_async(_create_trigger)()

        if trigger.trigger_type == 'schedule':
            try:
                await create_schedule_for_trigger(trigger)
            except Exception as exc:
                logger.exception("Failed to create Temporal schedule for workflow %s: %s", workflow.id, exc)


async def handle_workflow_message(user_id: int, room_id: Optional[int], message: str, history_text: str = '') -> str:
    draft = await _get_active_draft(user_id, room_id)

    if draft and _is_cancellation(message):
        await _close_draft(draft, 'cancelled')
        return "Workflow draft cancelled."

    if draft and _is_confirmation(message):
        definition = draft.definition or {}
        valid, error = validate_workflow_definition(definition)
        if not valid:
            return f"Draft is invalid: {error}"
        workflow = await _create_workflow(user_id, room_id, definition, draft)
        await _close_draft(draft, 'confirmed')
        return f"Workflow created and activated: {workflow.name}"

    llm = get_llm_client()
    system_prompt = get_capabilities_prompt()

    draft_context = "null"
    if draft and draft.definition:
        try:
            draft_context = json.dumps(draft.definition, indent=2)
        except TypeError:
            draft_context = "null"

    history_block = history_text or ""
    user_prompt = "\n".join([
        "Conversation context (most recent last):",
        history_block,
        "",
        "Existing draft (if any):",
        draft_context,
        "",
        f"User message: {message}",
        "",
        "Return JSON only."
    ])

    response_text = await llm.generate_text(
        system_prompt=system_prompt,
        user_prompt=user_prompt,
        temperature=0.2,
        max_tokens=1200,
        json_mode=True
    )

    parsed = llm.extract_json(response_text) or {}
    assistant_message = parsed.get('assistant_message') or "I need a bit more detail to build that workflow."
    workflow_definition = parsed.get('workflow_definition')

    if workflow_definition:
        if not isinstance(workflow_definition, dict):
            return f"{assistant_message}\n\nValidation issue: workflow_definition must be an object"
        valid, error = validate_workflow_definition(workflow_definition)
        if not valid:
            return f"{assistant_message}\n\nValidation issue: {error}"

        await _save_draft(user_id, room_id, workflow_definition)
        summary = _format_summary(workflow_definition)
        return f"{assistant_message}\n\n{summary}\n\nReply 'approve' to create it or tell me what to change."

    return assistant_message
