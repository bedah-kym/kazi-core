"""Deterministic workflow capability manifest and delta (v0.7 W-A2, #167).

A workflow definition may declare ``capabilities`` — the ``service:action``
pairs it is allowed to call. When it does, every step must stay inside that
list. When it does not, the manifest is derived from the steps themselves so
older definitions remain executable while still getting a stable snapshot.

``capability_delta`` is pure code (never an LLM): the approval path uses it to
decide whether a proposed change widens what the workflow may do.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Sequence, Tuple

from orchestration.action_catalog import resolve_action_alias

_MANIFEST_MISSING = "__missing__"


def capability_for_step(step: Dict[str, Any]) -> Optional[str]:
    """Return the normalized ``service:action`` capability for a step."""
    if not isinstance(step, dict):
        return None
    service = str(step.get("service") or "").strip().lower()
    raw_action = str(step.get("action") or "").strip().lower()
    if not raw_action:
        return None
    action = resolve_action_alias(raw_action) or raw_action
    if service in ("mailgun",):
        service = "gmail"
    return f"{service}:{action}" if service else f":{action}"


def normalize_capabilities(values: Optional[Sequence[Any]]) -> List[str]:
    """Normalize, de-duplicate and sort a declared capability list."""
    if not values or isinstance(values, (str, bytes, dict)):
        return []
    normalized = set()
    for value in values:
        if not isinstance(value, str):
            continue
        text = value.strip().lower()
        if not text:
            continue
        if ":" not in text:
            text = f":{text}"
        service, _, action = text.partition(":")
        if service == "mailgun":
            service = "gmail"
        action = resolve_action_alias(action) or action
        normalized.add(f"{service}:{action}" if service else f":{action}")
    return sorted(normalized)


def derived_capabilities(definition: Dict[str, Any]) -> List[str]:
    """Manifest derived from the step list (fallback when none is declared)."""
    capabilities = {
        capability_for_step(step)
        for step in (definition or {}).get("steps", [])
        if isinstance(step, dict)
    }
    capabilities.discard(None)
    return sorted(capabilities)


def manifest_from_definition(definition: Dict[str, Any]) -> List[str]:
    """Declared manifest when present, otherwise the derived step manifest."""
    if not isinstance(definition, dict):
        return []
    declared = definition.get("capabilities")
    if declared is None:
        return derived_capabilities(definition)
    return normalize_capabilities(declared)


def validate_declared_capabilities(definition: Dict[str, Any]) -> Tuple[bool, Optional[str]]:
    """A declared manifest must cover every step and reference known actions."""
    if not isinstance(definition, dict):
        return False, "Workflow definition must be a JSON object"
    if definition.get("capabilities") is None:
        return True, None

    declared = definition.get("capabilities")
    if not isinstance(declared, list):
        return False, "capabilities must be a list of 'service:action' strings"

    normalized = normalize_capabilities(declared)
    if len(normalized) != len(declared):
        return False, "capabilities must not contain duplicates or empty entries"

    declared_set = set(normalized)
    for step in definition.get("steps", []) or []:
        capability = capability_for_step(step)
        if capability and capability not in declared_set:
            step_id = step.get("id") or step.get("action") or "step"
            return False, (
                f"step '{step_id}' calls {capability}, which is not in the "
                "declared capabilities manifest"
            )

    unknown = sorted(declared_set - _known_capabilities())
    if unknown:
        return False, f"Unknown capabilities: {', '.join(unknown)}"

    return True, None


def capability_delta(
    current_definition: Dict[str, Any],
    proposed_definition: Dict[str, Any],
) -> Dict[str, Any]:
    """Code-computed delta between two definitions' manifests."""
    current = set(manifest_from_definition(current_definition))
    proposed = set(manifest_from_definition(proposed_definition))
    added = sorted(proposed - current)
    removed = sorted(current - proposed)
    return {
        "added": added,
        "removed": removed,
        "widening": bool(added),
        "reason": "capabilities-added" if added else "no-new-capabilities",
    }


def proposed_definition_from_changes(
    current_definition: Dict[str, Any],
    proposed_changes: Dict[str, Any],
) -> Optional[Dict[str, Any]]:
    """Resolve a suggestion payload into a full proposed definition, if possible."""
    if not isinstance(proposed_changes, dict):
        return None
    for key in ("definition", "workflow_definition"):
        candidate = proposed_changes.get(key)
        if isinstance(candidate, dict):
            return candidate

    candidate = dict(current_definition or {})
    changed = False
    if isinstance(proposed_changes.get("capabilities"), list):
        candidate["capabilities"] = proposed_changes["capabilities"]
        changed = True
    if isinstance(proposed_changes.get("steps"), list):
        candidate["steps"] = proposed_changes["steps"]
        if "capabilities" not in proposed_changes:
            # A step change re-derives the effective scope: otherwise a stale
            # declared manifest would mask a newly proposed action.
            candidate.pop("capabilities", None)
        changed = True
    return candidate if changed else None


def capability_delta_for_suggestion(
    current_definition: Dict[str, Any],
    proposed_changes: Dict[str, Any],
) -> Dict[str, Any]:
    """Fail-closed delta for a reviewer suggestion.

    When the proposal cannot be resolved to a definition, the change is treated
    as widening so it cannot be auto-allowed; the human review path still sees
    the original proposal.
    """
    proposed = proposed_definition_from_changes(current_definition, proposed_changes)
    if proposed is None:
        return {
            "added": [],
            "removed": [],
            "widening": True,
            "reason": _MANIFEST_MISSING,
        }
    return capability_delta(current_definition, proposed)


def approval_kind_for_delta(delta: Dict[str, Any]) -> str:
    """A widening change escalates; a delta of zero is a fast approval."""
    return "escalation" if (delta or {}).get("widening") else "fast"


def _known_capabilities() -> set:
    from orchestration.action_catalog import build_capabilities_catalog

    known = set()
    for integration in build_capabilities_catalog().get("integrations", []):
        service = str(integration.get("service") or "").strip().lower()
        for action in integration.get("actions", []):
            name = str(action.get("name") or "").strip().lower()
            if name:
                known.add(f"{service}:{name}" if service else f":{name}")
    return known
