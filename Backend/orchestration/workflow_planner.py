"""
Plan ad-hoc multi-step workflows from a single user request and execute them.
"""
import asyncio
import hashlib
import json
import logging
import time
from typing import Any, Dict, List, Optional

from asgiref.sync import sync_to_async
from django.core.cache import cache
from django.utils import timezone

from orchestration.llm_client import get_llm_client
from workflows.capabilities import SYSTEM_CAPABILITIES, validate_workflow_definition
from workflows.temporal_integration import start_workflow_execution

logger = logging.getLogger(__name__)

MAX_ADHOC_STEPS = 7
MIN_ADHOC_STEPS = 2
MAX_WAIT_SECONDS = 20
IDEMPOTENCY_TTL_SECONDS = 90
IDEMPOTENCY_CACHE_PREFIX = "adhoc_workflow"
_CONFIRM_WORDS = {
    "yes",
    "approve",
    "approved",
    "confirm",
    "confirmed",
    "go ahead",
    "proceed",
}
_HIGH_RISK_ACTIONS = {
    ("payments", "withdraw"),
}

_AUTOMATION_HINTS = (
    "workflow",
    "automate",
    "automation",
    "every ",
    "whenever",
    "schedule",
    "cron",
)


def _looks_like_automation(message: str) -> bool:
    lowered = message.lower()
    return any(hint in lowered for hint in _AUTOMATION_HINTS)


def _looks_like_confirmation(message: str) -> bool:
    lowered = message.strip().lower()
    return any(word in lowered for word in _CONFIRM_WORDS)


def _has_high_risk_step(steps: List[Dict[str, Any]]) -> bool:
    for step in steps:
        service = str(step.get("service") or "").lower()
        action = str(step.get("action") or "").lower()
        if (service, action) in _HIGH_RISK_ACTIONS:
            return True
    return False


def _normalize_steps(steps: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    normalized = []
    for idx, step in enumerate(steps or []):
        if not isinstance(step, dict):
            continue
        cleaned = dict(step)
        cleaned.setdefault("id", f"step_{idx + 1}")
        if cleaned.get("service"):
            cleaned["service"] = str(cleaned["service"]).lower()
        if cleaned.get("action"):
            cleaned["action"] = str(cleaned["action"]).lower()
        if not isinstance(cleaned.get("params"), dict):
            cleaned["params"] = {}
        normalized.append(cleaned)
    return normalized


def _build_definition(steps: List[Dict[str, Any]], message: str) -> Dict[str, Any]:
    return {
        "workflow_name": "Ad hoc request",
        "workflow_description": message.strip()[:300],
        "triggers": [{"trigger_type": "manual"}],
        "steps": steps,
        "metadata": {"adhoc": True},
    }


async def plan_user_request(message: str, history_text: str = "") -> Dict[str, Any]:
    """
    Decide whether to run a multi-step ad-hoc workflow, ask for clarification,
    or fall back to single-action routing.
    """
    if _looks_like_automation(message):
        return {
            "mode": "automation_request",
            "assistant_message": "Got it. I can build a reusable workflow for that.",
            "workflow_definition": None,
        }

    llm = get_llm_client()
    capabilities_json = json.dumps(SYSTEM_CAPABILITIES, indent=2)

    system_prompt = "\n".join([
        "You are a planner that decides if a user request needs multiple ordered steps.",
        "If multiple steps are required now, return mode 'adhoc_workflow' and list steps.",
        "If it is a single action, return mode 'single'.",
        "If the user is asking to automate or create an ongoing workflow, return mode 'automation_request'.",
        "If required parameters are missing, return mode 'needs_clarification' with a helpful message.",
        "Only use the services/actions listed below.",
        "",
        "Available Integrations:",
        capabilities_json,
        "",
        "Return JSON only in this shape:",
        "{",
        '  "mode": "single|adhoc_workflow|automation_request|needs_clarification",',
        '  "assistant_message": "...",',
        '  "steps": [',
        '    {"id": "step_1", "service": "...", "action": "...", "params": {...}}',
        '  ] or null',
        "}",
    ])

    user_prompt = "\n".join([
        "Conversation context (most recent last):",
        history_text or "",
        "",
        f"User message: {message}",
        "",
        "Return JSON only.",
    ])

    try:
        response_text = await llm.generate_text(
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            temperature=0.2,
            max_tokens=900,
            json_mode=True,
        )
    except Exception as exc:
        logger.error("Planner LLM failed: %s", exc)
        return {"mode": "single", "assistant_message": "", "workflow_definition": None}

    parsed = llm.extract_json(response_text) or {}
    mode = parsed.get("mode") or "single"
    assistant_message = parsed.get("assistant_message") or ""
    steps = parsed.get("steps")

    if mode == "adhoc_workflow":
        if not isinstance(steps, list):
            return {"mode": "single", "assistant_message": "", "workflow_definition": None}

        normalized_steps = _normalize_steps(steps)
        if len(normalized_steps) < MIN_ADHOC_STEPS:
            return {"mode": "single", "assistant_message": "", "workflow_definition": None}

        if len(normalized_steps) > MAX_ADHOC_STEPS:
            return {
                "mode": "needs_clarification",
                "assistant_message": (
                    f"That is a lot to do at once. Please break it into "
                    f"{MAX_ADHOC_STEPS} steps or fewer."
                ),
                "workflow_definition": None,
            }

        if _has_high_risk_step(normalized_steps) and not _looks_like_confirmation(message):
            return {
                "mode": "needs_clarification",
                "assistant_message": (
                    "This includes a sensitive action. Please confirm explicitly "
                    "if you want me to proceed."
                ),
                "workflow_definition": None,
            }

        definition = _build_definition(normalized_steps, message)
        valid, error = validate_workflow_definition(definition)
        if not valid:
            logger.warning("Invalid ad-hoc workflow definition: %s", error)
            return {
                "mode": "needs_clarification",
                "assistant_message": f"I need a bit more detail to run that: {error}",
                "workflow_definition": None,
            }

        return {
            "mode": "adhoc_workflow",
            "assistant_message": assistant_message,
            "workflow_definition": definition,
        }

    if mode == "needs_clarification":
        return {
            "mode": "needs_clarification",
            "assistant_message": assistant_message or "I need a bit more detail to proceed.",
            "workflow_definition": None,
        }

    if mode == "automation_request":
        return {
            "mode": "automation_request",
            "assistant_message": assistant_message or "I can turn that into a workflow.",
            "workflow_definition": None,
        }

    return {"mode": "single", "assistant_message": "", "workflow_definition": None}


def _idempotency_key(user_id: int, definition: Dict[str, Any], trigger_data: Dict[str, Any]) -> str:
    payload = json.dumps(
        {"definition": definition, "trigger": trigger_data},
        sort_keys=True,
        default=str,
    )
    digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]
    return f"{IDEMPOTENCY_CACHE_PREFIX}:{user_id}:{digest}"


async def _enqueue_deferred_execution(
    workflow_obj,
    user_id: int,
    room_id: Optional[int],
    trigger_data: Dict[str, Any],
) -> Optional[int]:
    try:
        from workflows.models import DeferredWorkflowExecution

        def _create():
            return DeferredWorkflowExecution.objects.create(
                workflow=workflow_obj,
                user_id=user_id,
                room_id=room_id,
                trigger_data=trigger_data,
                status='queued',
                attempts=0,
                next_attempt_at=timezone.now(),
            ).id

        return await sync_to_async(_create)()
    except Exception as exc:
        logger.error("Failed to enqueue deferred workflow: %s", exc)
        return None


async def _create_adhoc_workflow(user_id: int, room_id: Optional[int], definition: Dict[str, Any]):
    from workflows.models import UserWorkflow

    def _create():
        return UserWorkflow.objects.create(
            user_id=user_id,
            name=definition.get("workflow_name", "Ad hoc request"),
            description=definition.get("workflow_description", "Ad hoc execution"),
            definition=definition,
            status='active',
            created_from_room_id=room_id,
        )

    return await sync_to_async(_create)()


async def execute_adhoc_workflow(
    definition: Dict[str, Any],
    user_id: int,
    room_id: Optional[int],
    trigger_data: Optional[Dict[str, Any]] = None,
    wait_seconds: int = 12,
) -> Dict[str, Any]:
    """
    Execute an ad-hoc workflow via Temporal when possible, falling back to inline
    execution if Temporal is unavailable.
    """
    trigger_data = trigger_data or {}
    wait_seconds = min(max(wait_seconds, 1), MAX_WAIT_SECONDS)

    idempotency_key = _idempotency_key(user_id, definition, trigger_data)
    if not cache.add(idempotency_key, {"status": "running"}, IDEMPOTENCY_TTL_SECONDS):
        return {
            "status": "duplicate",
            "mode": "noop",
            "workflow": None,
            "execution": None,
            "result": {},
            "message": "I already started that request. Please wait a moment.",
        }

    workflow_obj = await _create_adhoc_workflow(user_id, room_id, definition)

    try:
        execution = await start_workflow_execution(workflow_obj, trigger_data, "manual")
    except Exception as exc:
        logger.error("Temporal start failed, falling back to inline execution: %s", exc)
        deferred_id = await _enqueue_deferred_execution(workflow_obj, user_id, room_id, trigger_data)
        if deferred_id:
            cache.set(idempotency_key, {"status": "queued"}, IDEMPOTENCY_TTL_SECONDS)
            return {
                "status": "queued",
                "mode": "deferred",
                "workflow": workflow_obj,
                "execution": None,
                "result": {},
                "message": "Temporal is unavailable. Your request is queued and will run when it is back up.",
            }
        result = await _run_inline(definition, user_id, trigger_data)
        cache.set(idempotency_key, {"status": "completed"}, IDEMPOTENCY_TTL_SECONDS)
        return {
            "status": "completed",
            "mode": "inline",
            "workflow": workflow_obj,
            "execution": None,
            "result": result,
        }

    completed = await _wait_for_execution(execution.id, wait_seconds)
    if completed and completed.status == "completed":
        cache.set(idempotency_key, {"status": "completed"}, IDEMPOTENCY_TTL_SECONDS)
        return {
            "status": "completed",
            "mode": "temporal",
            "workflow": workflow_obj,
            "execution": completed,
            "result": completed.result or {},
        }

    if completed and completed.status in ("failed", "cancelled"):
        cache.set(idempotency_key, {"status": completed.status}, IDEMPOTENCY_TTL_SECONDS)
        return {
            "status": completed.status,
            "mode": "temporal",
            "workflow": workflow_obj,
            "execution": completed,
            "result": completed.result or {},
            "error": completed.error_message,
        }

    cache.set(idempotency_key, {"status": "running"}, IDEMPOTENCY_TTL_SECONDS)
    return {
        "status": "running",
        "mode": "temporal",
        "workflow": workflow_obj,
        "execution": completed or execution,
        "result": completed.result if completed else {},
    }


async def _wait_for_execution(execution_id: int, wait_seconds: int):
    from workflows.models import WorkflowExecution

    deadline = time.monotonic() + max(wait_seconds, 1)
    while time.monotonic() < deadline:
        def _fetch():
            return WorkflowExecution.objects.filter(id=execution_id).first()
        execution = await sync_to_async(_fetch)()
        if execution and execution.status in ("completed", "failed", "cancelled"):
            return execution
        await asyncio.sleep(0.5)
    return await sync_to_async(lambda: WorkflowExecution.objects.filter(id=execution_id).first())()


async def _run_inline(definition: Dict[str, Any], user_id: int, trigger_data: Dict[str, Any]) -> Dict[str, Any]:
    from workflows.activity_executors import execute_workflow_step
    from workflows.utils import safe_eval_condition, compact_context

    context: Dict[str, Any] = {
        "trigger": trigger_data,
        "workflow": {"id": 0, "policy": definition.get("policy") or {}},
        "user_id": user_id,
    }

    for step in definition.get("steps", []):
        condition = step.get("condition")
        if condition and not safe_eval_condition(condition, context):
            continue
        step_id = step.get("id") or step.get("action") or f"step_{len(context)}"
        on_error = str(step.get("on_error") or "stop").lower()
        try:
            result = await execute_workflow_step(step, context)
            context[step_id] = result
        except Exception as exc:
            context[step_id] = {"status": "error", "error": str(exc)}
            if on_error == "continue":
                continue
            raise

    return compact_context(context)


async def synthesize_workflow_response_stream(
    user_message: str,
    workflow_definition: Dict[str, Any],
    execution_result: Dict[str, Any],
    status: str,
    error: Optional[str] = None,
):
    """
    Stream a natural language response summarizing a workflow run.
    """
    if status == "duplicate":
        yield "I already started that request. Please wait a moment."
        return

    if status == "queued":
        yield "Temporal is unavailable. I queued your request and will run it when it is back up."
        return

    if status in ("failed", "cancelled"):
        suffix = f" Error: {error}" if error else ""
        yield f"The workflow did not complete successfully.{suffix}"
        return

    if status != "completed":
        yield (
            "I started the workflow but it hasn't finished yet. "
            "I'll share the results once it completes."
        )
        return

    llm = get_llm_client()
    system_prompt = (
        "You are Mathia, a helpful assistant. Summarize the results of a multi-step "
        "workflow execution. Be concise and list key outputs per step. "
        "Do not invent details not present in the data."
    )
    user_prompt = json.dumps({
        "user_message": user_message,
        "workflow": workflow_definition,
        "result": execution_result,
    })

    try:
        async for chunk in llm.stream_text(
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            temperature=0.4,
            max_tokens=600,
        ):
            if chunk:
                yield chunk
    except Exception as exc:
        logger.error("Workflow response synthesis failed: %s", exc)
        yield "I ran the workflow, but had trouble summarizing the results."
