"""Agent personas (v0.7 W-G, issue #203).

Deterministic bounds applied in the tool path — never by prompt. A room
resolves to at most one active persona owned by the requesting user; a lookup
failure or a persona owned by someone else means "no persona = today's
behavior". An out-of-scope call is denied; above the risk ceiling or on the
approval boundary it escalates through the existing confirmation pause.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

RISK_RANK = {"low": 0, "medium": 1, "high": 2}


def resolve_room_persona(room_id: Optional[int], user_id: Optional[int]):
    """The active persona bound to a room, owned by ``user_id`` — or None.

    Fail closed: another user's persona is invisible, an inactive persona is
    invisible, and any lookup error yields None.
    """
    if not room_id or not user_id:
        return None
    try:
        from workflows.models import Persona

        persona = Persona.objects.filter(
            room_id=room_id, user_id=user_id, status="active",
        ).first()
    except Exception as exc:
        logger.warning("Persona resolution failed: %s", exc)
        return None
    return persona


def create_persona(
    user,
    *,
    name: str,
    description: str = "",
    tool_scope: Optional[List[str]] = None,
    risk_ceiling: str = "medium",
    approval_boundary: Optional[List[str]] = None,
    skills: Optional[List[str]] = None,
    room=None,
) -> Any:
    from workflows.models import Persona

    return Persona.objects.create(
        user=user,
        name=name[:100],
        description=description or "",
        tool_scope=sorted({str(item) for item in (tool_scope or []) if item}),
        risk_ceiling=risk_ceiling if risk_ceiling in RISK_RANK else "medium",
        approval_boundary=sorted({str(item) for item in (approval_boundary or []) if item}),
        skills=sorted({str(item) for item in (skills or []) if item}),
        room=room,
        status="draft",
    )


def confirm_persona(persona) -> Any:
    """Human promotion: draft -> active."""
    persona.status = "active"
    persona.save(update_fields=["status", "updated_at"])
    return persona


def archive_persona(persona) -> Any:
    persona.status = "archived"
    persona.save(update_fields=["status", "updated_at"])
    return persona


def duplicate_persona(persona, *, new_name: str) -> Any:
    """Duplicate a persona as a draft. History is never copied."""
    from workflows.models import Persona

    return Persona.objects.create(
        user=persona.user,
        name=new_name[:100],
        description=persona.description,
        tool_scope=list(persona.tool_scope or []),
        risk_ceiling=persona.risk_ceiling,
        approval_boundary=list(persona.approval_boundary or []),
        skills=list(persona.skills or []),
        status="draft",
        created_from=persona,
    )


def persona_bounds(persona) -> Dict[str, Any]:
    scope = {str(item).strip().lower() for item in (persona.tool_scope or []) if item}
    boundary = {str(item).strip().lower() for item in (persona.approval_boundary or []) if item}
    return {
        "scope": scope,
        "boundary": boundary,
        "ceiling_rank": RISK_RANK.get(persona.risk_ceiling, 1),
    }


def _split_scope_by_availability(scope, user_capabilities=None) -> Tuple[List[str], List[str]]:
    """Split a persona's scope into tools that can run here and catalog tools that
    cannot: their connector is not configured, or the user's capability flag
    turns them off (the same rule the tool list applies).

    Internal and meta tools are not catalog actions and always stay. If the
    registry cannot be read, nothing is dropped.
    """
    try:
        from orchestration.action_catalog import iter_action_definitions, resolve_action_alias
        from orchestration.connector_registry import get_available_actions
        from orchestration.tool_schemas import _gate_allows

        gates = {item["action"]: item.get("capability_gate") for item in iter_action_definitions()}
        available = set(get_available_actions())
    except Exception:
        return list(scope), []
    capabilities = user_capabilities or {}
    usable, missing = [], []
    for name in scope:
        action = resolve_action_alias(str(name))
        if action not in gates:
            usable.append(name)
            continue
        gate = gates[action]
        gate_off = bool(gate) and not _gate_allows(gate, capabilities, user_capabilities is not None)
        (missing if action not in available or gate_off else usable).append(name)
    return usable, missing


def persona_identity_prompt(persona, user_capabilities=None) -> str:
    """System-prompt block so the agent knows who it is and what it may do."""
    from orchestration.skill_registry import get_skill

    parts = [f"You are Kazi, operating as the named persona \"{persona.name}\"."]
    if persona.description:
        parts.append(persona.description.strip())
    if persona.tool_scope:
        usable, missing = _split_scope_by_availability(persona.tool_scope, user_capabilities)
        if usable:
            parts.append("Your tool scope: " + ", ".join(usable) + ".")
        if missing:
            parts.append(
                "In your scope but not available here (not configured, or turned off in settings): "
                + ", ".join(missing) + "."
            )
        parts.append("Anything outside that scope is refused, not attempted.")
    parts.append(
        f"Your risk ceiling is '{persona.risk_ceiling}'; anything above it, or on "
        "your approval boundary, must be confirmed with the human first."
    )
    if persona.approval_boundary:
        parts.append("Always ask before: " + ", ".join(persona.approval_boundary) + ".")

    skill_blocks = []
    for name in list(persona.skills or [])[:5]:
        skill = get_skill(name)
        if skill:
            skill_blocks.append(f"[Skill: {skill['name']}]\n{skill['body'][:2000]}")
    if skill_blocks:
        parts.append("Your assigned skills:\n" + "\n\n".join(skill_blocks))

    return "\n".join(parts)


def apply_persona_bounds(
    risk_info: Dict[str, Any],
    bounds: Dict[str, Any],
    action: str,
) -> Tuple[Dict[str, Any], str]:
    """Apply deterministic persona bounds to a risk decision.

    Returns ``(risk_info, denial_reason)``. A non-empty ``denial_reason``
    means the call is out of scope and must be denied, never prompted.
    """
    risk_info = dict(risk_info or {})
    action = str(action or "").strip().lower()
    scope = bounds.get("scope") or set()
    if scope and action not in scope:
        risk_info["persona_denied"] = True
        return risk_info, "Refused: this action is outside the persona's tool scope."

    boundary = bounds.get("boundary") or set()
    if action in boundary:
        risk_info["requires_confirmation"] = True

    level = str(risk_info.get("risk_level") or "low").strip().lower()
    # Unknown risk levels fail closed: they rank as high, never as low.
    action_rank = RISK_RANK.get(level, RISK_RANK["high"])
    if action_rank > int(bounds.get("ceiling_rank", 1)):
        risk_info["requires_confirmation"] = True
        risk_info["risk_level"] = "high"

    return risk_info, ""
