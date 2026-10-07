"""Workflow chat agent for @Kazi workflow creation."""
import json
import logging
from typing import Dict, Any, Optional
from asgiref.sync import sync_to_async

from orchestration.llm_client import get_llm_client

from .capabilities import get_capabilities_prompt, validate_workflow_definition
from .creation import WorkflowCreationError, create_workflow_with_triggers
from .models import WorkflowDraft, UserWorkflow
from .promotion import PromotionError, is_save_skill_request, save_session_as_skill
from .routine import normalize_routine, validate_routine_contract

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
        if room_id:
            from orchestration.personas import resolve_room_persona

            draft.owner_persona = resolve_room_persona(room_id, user_id)
        draft.save()
        return draft
    return await sync_to_async(_save)()


async def _close_draft(draft: WorkflowDraft, status: str) -> None:
    def _update():
        draft.status = status
        draft.save(update_fields=['status'])
    await sync_to_async(_update)()


async def _create_workflow(user_id: int, room_id: Optional[int], definition: Dict[str, Any], draft: WorkflowDraft) -> UserWorkflow:
    workflow, _triggers = await create_workflow_with_triggers(
        user_id=user_id,
        room_id=room_id,
        definition=definition,
        draft=draft,
    )
    return workflow


async def handle_workflow_message(user_id: int, room_id: Optional[int], message: str, history_text: str = '') -> str:
    if is_save_skill_request(message):
        try:
            saved = await save_session_as_skill(user_id, room_id)
        except PromotionError as exc:
            return str(exc)
        summary = _format_summary(saved.definition or {})
        return (
            f"I saved that as a staged skill and drafted the workflow '{saved.skill_name}'.\n\n"
            f"{summary}\n\nReply 'approve' to create it, or tell me what to change."
        )

    draft = await _get_active_draft(user_id, room_id)

    if draft and _is_cancellation(message):
        await _close_draft(draft, 'cancelled')
        return "Workflow draft cancelled."

    if draft and _is_confirmation(message):
        definition = draft.definition or {}
        valid, error = validate_workflow_definition(definition)
        if not valid:
            return f"Draft is invalid: {error}"
        routine_valid, routine_error = validate_routine_contract(definition)
        if not routine_valid:
            return f"This can't be enabled yet: {routine_error}"
        try:
            workflow = await _create_workflow(user_id, room_id, definition, draft)
        except WorkflowCreationError as exc:
            return str(exc)
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

        await _save_draft(user_id, room_id, normalize_routine(workflow_definition))
        summary = _format_summary(workflow_definition)
        return f"{assistant_message}\n\n{summary}\n\nReply 'approve' to create it or tell me what to change."

    return assistant_message
