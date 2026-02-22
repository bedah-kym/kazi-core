"""Deterministic manager verifier for ad-hoc workflow plans."""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple
import copy

from workflows.capabilities import SYSTEM_CAPABILITIES

_AUTO_EMAIL_SUMMARY_TOKEN = "__AUTO_SUMMARY__"


class ManagerVerifier:
    """
    Deterministic plan verifier and fixer.
    Runs before execution to catch missing params and bad ordering.
    """

    def __init__(self, capabilities: Optional[Dict[str, Any]] = None):
        self.capabilities = capabilities or SYSTEM_CAPABILITIES
        self._cap_index = self._build_cap_index(self.capabilities)

    def review_steps(self, steps: List[Dict[str, Any]], message: str) -> Dict[str, Any]:
        if not isinstance(steps, list) or not steps:
            return {
                "verdict": "ask_user",
                "assistant_message": "I need a bit more detail to proceed.",
                "steps": [],
                "missing_fields": [],
            }

        steps = copy.deepcopy(steps)
        steps = self._reorder_booking_steps(steps)
        steps = self._reorder_delivery_steps(steps)
        steps = self._ensure_step_ids(steps)

        missing: List[Tuple[str, str]] = []
        for step in steps:
            service = step.get("service")
            action = step.get("action")
            action_def = self._cap_index.get(service, {}).get(action)
            if not action_def:
                return {
                    "verdict": "ask_user",
                    "reason": "unknown_action",
                    "assistant_message": "I couldn't map one of the actions. Please rephrase with explicit steps.",
                    "steps": steps,
                    "missing_fields": [],
                }

            params = step.get("params") or {}
            if not isinstance(params, dict):
                params = {}
            params = self._normalize_param_aliases(params, action)
            params = self._coerce_param_types(params, action_def.get("params") or {})
            step["params"] = params

            for param_name, param_def in (action_def.get("params") or {}).items():
                if param_def.get("required") and not params.get(param_name):
                    missing.append((step.get("id") or action or "step", param_name))

        if missing:
            first_missing = missing[0][1]
            return {
                "verdict": "ask_user",
                "reason": "missing_param",
                "assistant_message": self._missing_param_message(first_missing),
                "steps": steps,
                "missing_fields": missing,
            }

        return {
            "verdict": "approve",
            "reason": "approved",
            "assistant_message": "",
            "steps": steps,
            "missing_fields": [],
        }

    def review_execution_result(
        self,
        execution_result: Dict[str, Any],
        workflow_definition: Optional[Dict[str, Any]] = None,
    ) -> Optional[str]:
        if not isinstance(execution_result, dict):
            return None
        errors = []
        for key, value in execution_result.items():
            if not isinstance(value, dict):
                continue
            if value.get("status") == "error":
                error_text = value.get("error") or "Unknown error"
                errors.append(f"{key}: {error_text}")
        if errors:
            joined = "; ".join(errors[:3])
            return f"I hit a snag while running the workflow. {joined}"

        if isinstance(workflow_definition, dict):
            expected_steps = []
            for step in workflow_definition.get("steps", []):
                if not isinstance(step, dict):
                    continue
                step_id = step.get("id") or step.get("action")
                if step_id:
                    expected_steps.append(step_id)
            missing = [step_id for step_id in expected_steps if step_id not in execution_result]
            if missing:
                joined = ", ".join(missing[:3])
                return (
                    "I could not confirm results for every step. "
                    f"Missing results for: {joined}. Want me to retry or adjust?"
                )
        return None

    def _reorder_booking_steps(self, steps: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """
        Ensure search steps run before booking when item_id is missing.
        """
        if len(steps) < 2:
            return steps

        def _is_search(step: Dict[str, Any]) -> bool:
            return str(step.get("action") or "").startswith("search_")

        for idx, step in enumerate(list(steps)):
            if step.get("action") != "book_travel_item":
                continue
            params = step.get("params") or {}
            if params.get("item_id"):
                continue
            for later_idx in range(idx + 1, len(steps)):
                if _is_search(steps[later_idx]):
                    steps[idx], steps[later_idx] = steps[later_idx], steps[idx]
                    break
        return steps

    def _ensure_step_ids(self, steps: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        seen = set()
        for idx, step in enumerate(steps, start=1):
            step_id = step.get("id") or f"step_{idx}"
            base = step_id
            counter = 1
            while step_id in seen:
                counter += 1
                step_id = f"{base}_{counter}"
            step["id"] = step_id
            seen.add(step_id)
        return steps

    def _reorder_delivery_steps(self, steps: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        if len(steps) < 2:
            return steps

        delivery_actions = {"send_email", "send_message"}

        def _needs_results(step: Dict[str, Any]) -> bool:
            action = step.get("action")
            params = step.get("params") or {}
            if action == "send_email":
                text = params.get("text") or ""
                if not text or text == _AUTO_EMAIL_SUMMARY_TOKEN:
                    return True
                lowered = str(text).lower()
                return any(token in lowered for token in ("results", "summary", "options", "details"))
            if action == "send_message":
                message = params.get("message") or ""
                if not message:
                    return True
                lowered = str(message).lower()
                return any(token in lowered for token in ("results", "summary", "options", "details"))
            return False

        delayed = []
        ordered = []
        for step in steps:
            action = step.get("action")
            if action in delivery_actions and _needs_results(step):
                delayed.append(step)
            else:
                ordered.append(step)

        if not delayed:
            return steps
        return ordered + delayed

    def _coerce_param_types(self, params: Dict[str, Any], schema: Dict[str, Any]) -> Dict[str, Any]:
        normalized = dict(params)
        for key, spec in schema.items():
            if key not in normalized:
                continue
            value = normalized[key]
            expected = spec.get("type")
            if expected == "integer":
                if isinstance(value, str) and value.isdigit():
                    normalized[key] = int(value)
            elif expected == "number":
                if isinstance(value, str):
                    try:
                        normalized[key] = float(value)
                    except ValueError:
                        pass
        return normalized

    def _normalize_param_aliases(self, params: Dict[str, Any], action: Optional[str]) -> Dict[str, Any]:
        normalized = dict(params)
        alias_map = {}
        if action == "send_email":
            alias_map = {
                "body": "text",
                "message": "text",
                "recipient": "to",
                "email": "to",
            }
        elif action == "send_message":
            alias_map = {
                "text": "message",
                "phone": "phone_number",
            }
        for source, target in alias_map.items():
            if source in normalized and target not in normalized:
                value = normalized.get(source)
                if value is not None and value != "":
                    normalized[target] = value
        return normalized

    def _build_cap_index(self, capabilities: Dict[str, Any]) -> Dict[str, Dict[str, Dict[str, Any]]]:
        index: Dict[str, Dict[str, Dict[str, Any]]] = {}
        for service in capabilities.get("integrations", []):
            service_name = service.get("service")
            if not service_name:
                continue
            index[service_name] = {}
            for action in service.get("actions", []):
                action_name = action.get("name")
                if not action_name:
                    continue
                index[service_name][action_name] = action
        return index

    def _missing_param_message(self, param: str) -> str:
        label = param.replace("_", " ")
        suffix = ""
        if "date" in param:
            suffix = " (YYYY-MM-DD)"
        elif "time" in param:
            suffix = " (e.g., 15:00)"
        return f"I still need {label}{suffix} to proceed."
