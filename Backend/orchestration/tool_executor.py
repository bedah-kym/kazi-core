"""
Tool Executor for the Agentic Loop.

Single entry point to execute any tool call from the agent loop.
Reuses the existing MCPRouter connector map, applies safety checks,
and returns a standardized result dict.
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import Any, Dict, List, Optional

from orchestration.action_catalog import (
    get_action_definition,
    is_high_risk_action,
    requires_confirmation,
    resolve_action_alias,
)
from orchestration.security_policy import is_prompt_injection, sanitize_parameters, should_block_action

logger = logging.getLogger(__name__)

_MAX_ERROR_CHARS = 300
PREVIEW_TIMEOUT_SECONDS = 5


def _normalize_error(raw: str) -> str:
    """Clamp + flatten an error so raw upstream bodies never enter LLM context."""
    if not raw:
        return "Tool execution failed."
    flat = " ".join(raw.split())
    if len(flat) > _MAX_ERROR_CHARS:
        flat = flat[:_MAX_ERROR_CHARS] + "…"
    return f"Tool execution failed: {flat}"

# Lazy-loaded singleton for the connector map


def _get_connector_map() -> Dict[str, Any]:
    """Lazy-load the connector map via auto-discovery registry."""
    from orchestration.connector_registry import discover_connectors
    return discover_connectors()


async def execute_tool(
    tool_name: str,
    tool_input: Dict[str, Any],
    context: Dict[str, Any],
) -> Dict[str, Any]:
    """
    Execute a tool call from the agent loop.

    Args:
        tool_name: The action name (e.g., 'search_flights', 'send_email').
        tool_input: The parameters the LLM provided for the tool.
        context: User context dict with user_id, room_id, username, preferences.

    Returns:
        Dict with at minimum {"status": "success"|"error", ...} plus tool-specific data.
    """
    start_time = time.monotonic()
    action = resolve_action_alias(tool_name)

    # 0a. Internal contact tools (no connector needed)
    from orchestration.contact_tools import _CONTACT_TOOL_MAP
    if action in _CONTACT_TOOL_MAP:
        try:
            result = await _CONTACT_TOOL_MAP[action](tool_input, context)
        except Exception as exc:
            logger.error("Contact tool error %s: %s", action, exc, exc_info=True)
            return {"status": "error", "message": f"Contact tool failed: {str(exc)}"}
        elapsed = round(time.monotonic() - start_time, 2)
        logger.info("Contact tool %s executed in %ss", action, elapsed)
        if not isinstance(result, dict):
            result = {"status": "success", "data": result}
        if "status" not in result:
            result["status"] = "success"
        return result

    # 0b. Internal memory tools (no connector needed)
    from orchestration.memory_tools import _MEMORY_TOOL_MAP
    if action in _MEMORY_TOOL_MAP:
        try:
            result = await _MEMORY_TOOL_MAP[action](tool_input, context)
        except Exception as exc:
            logger.error("Memory tool error %s: %s", action, exc, exc_info=True)
            return {"status": "error", "message": f"Memory tool failed: {str(exc)}"}
        elapsed = round(time.monotonic() - start_time, 2)
        logger.info("Memory tool %s executed in %ss", action, elapsed)
        if not isinstance(result, dict):
            result = {"status": "success", "data": result}
        if "status" not in result:
            result["status"] = "success"
        return result

    # 1. Validate action exists
    action_def = get_action_definition(action)
    if not action_def:
        return {
            "status": "error",
            "message": f"Unknown tool: {tool_name}",
        }

    # 2. Check capability gate
    gate = action_def.get("capability_gate")
    if gate:
        preferences = context.get("preferences") or {}
        if not preferences.get(gate, True):
            return {
                "status": "error",
                "message": f"The {action.replace('_', ' ')} capability is disabled in your settings.",
            }

    # 3. Security check — block prompt injection for sensitive actions.
    raw_query = context.get("raw_query") or context.get("user_message") or ""
    if should_block_action(raw_query, action):
        return {
            "status": "error",
            "message": "This request was blocked by the safety policy.",
        }
    # Additional guard when suspicious instructions are embedded directly in tool params.
    try:
        params_text = json.dumps(tool_input, default=str)
    except Exception:
        params_text = str(tool_input)
    if is_prompt_injection(params_text) and is_high_risk_action(action) and action != "run_command":
        # Shell commands are bounded by the sandbox, not this regex — blocking
        # them here is the regex firewall the v0.6 brief rejects, and it
        # false-positives on legitimate text (e.g. the word "root").
        return {
            "status": "error",
            "message": "This request was blocked by the safety policy.",
        }

    # 4. Sanitize parameters
    parameters = sanitize_parameters(dict(tool_input))
    parameters["action"] = action

    # 5. Get connector
    connectors = _get_connector_map()
    connector = connectors.get(action)
    if not connector:
        return {
            "status": "error",
            "message": f"No connector available for: {action}",
        }

    # 6. Execute
    try:
        result = await connector.execute(parameters, context)
    except Exception as exc:
        logger.error("Tool execution error for %s: %s", action, exc, exc_info=True)
        return {
            "status": "error",
            "message": _normalize_error(str(exc)),
        }

    elapsed = round(time.monotonic() - start_time, 2)
    logger.info("Tool %s executed in %ss", action, elapsed)

    # 7. Normalize result format
    if not isinstance(result, dict):
        result = {"status": "success", "data": result}
    if "status" not in result:
        result["status"] = "success"

    return result


async def preview_tool(
    tool_name: str,
    tool_input: Dict[str, Any],
    context: Dict[str, Any],
) -> Optional[List[str]]:
    """
    Compute a connector's predicted effects for an approval card (dry run).

    Never executes the action. Fail-closed: any missing connector, missing
    ``preview()``, or error inside preview yields None so the approval flow
    always proceeds without effects.
    """
    action = resolve_action_alias(tool_name)

    from orchestration.contact_tools import _CONTACT_TOOL_MAP
    from orchestration.memory_tools import _MEMORY_TOOL_MAP
    if action in _CONTACT_TOOL_MAP or action in _MEMORY_TOOL_MAP:
        return None

    if not get_action_definition(action):
        return None

    connector = _get_connector_map().get(action)
    if not connector:
        return None

    parameters = sanitize_parameters(dict(tool_input))
    parameters["action"] = action

    try:
        result = await asyncio.wait_for(
            connector.preview(parameters, context),
            timeout=PREVIEW_TIMEOUT_SECONDS,
        )
    except asyncio.TimeoutError:
        logger.warning("Tool preview timed out for %s", action)
        return None
    except Exception as exc:
        logger.warning("Tool preview error for %s: %s", action, exc)
        return None

    if not isinstance(result, dict):
        return None
    effects = result.get("effects")
    if not isinstance(effects, list):
        return None
    return [str(effect) for effect in effects]


def _shell_profile() -> str:
    try:
        from django.conf import settings
        return str(getattr(settings, "SHELL_EXEC_PROFILE", "standard") or "standard")
    except Exception:
        return "standard"


def _network_allowlist() -> List[str]:
    try:
        from django.conf import settings
        return list(getattr(settings, "SHELL_EXEC_NETWORK_ALLOWLIST", []) or [])
    except Exception:
        return []


def _run_command_risk_info(
    tool_input: Optional[Dict[str, Any]],
    user_preferences: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Dynamic risk for the shell: classify the actual command.

    Phase 2 (#133) makes this tier-aware: `safe` runs without a prompt,
    `bounded` pauses inline, `destructive` pauses durably, `denied` is refused
    by the loop. A `bounded` command whose hosts are all allowlisted (#134) runs
    without a prompt. A request may ask for network explicitly (`network:
    "bridge"`) so a script that pings internally still gets egress.

    The prompt follows the sandbox boundary, not the command (2026-10 auto
    mode). A tainted run (``_shell_tainted``, set by the loop) asks only for a
    command with ``egress``: sandboxed, that is a bridge request; on the
    unsandboxed profile it is every command. ``_shell_autopilot`` (a
    human-armed window injected by the coordinator, never read from the DB
    here) lifts prompts on the unsandboxed profile only. Neither ever covers
    `destructive`/`denied`. Learned ``approval_overrides`` remain ignored for
    ``run_command`` — no mined preference can auto-run shell.

    With the egress proxy on (``SHELL_EGRESS_PROXY``), an HTTPS-capable network
    command on `standard` runs behind an allowlisting proxy. On an untainted
    run it needs no prompt unless it publishes/pushes/uploads. A tainted run
    still asks — an approved host is still an exfiltration path — but the
    approved command is then confined to approved hosts.
    """
    from orchestration.shell.classifier import classify_command

    command = ""
    requested_network = ""
    if isinstance(tool_input, dict):
        command = str(tool_input.get("command") or "")
        requested_network = str(tool_input.get("network") or "")
    profile = ""
    if isinstance(user_preferences, dict):
        profile = str(user_preferences.get("shell_profile") or "")
    profile = profile or _shell_profile()
    classification = classify_command(
        command,
        profile=profile,
        allowlist=_network_allowlist(),
        requested_network=requested_network,
    )
    raw_tier = classification["tier"]
    # An allowlisted network command is effectively safe: it runs without a
    # prompt. Everything else keeps its raw tier.
    if raw_tier == "bounded" and classification.get("needs_network") and classification.get("allowlisted"):
        tier = "safe"
    else:
        tier = raw_tier

    from orchestration.shell.profiles import get_profile

    prefs = user_preferences if isinstance(user_preferences, dict) else {}
    sandboxed = get_profile(profile).backend == "docker"
    needs_network = bool(classification.get("needs_network"))
    # The connector calls the same function, so a command this gate lets
    # through as "proxied" is the command the sidecar runs behind the proxy.
    from orchestration.shell.egress import NETWORK_PROXY, network_mode

    mode = network_mode(classification, profile)
    proxied = mode == NETWORK_PROXY
    # Egress = the command can reach the outside world. Sandboxed, that is any
    # network request — proxied ones included: an approved host is still a
    # place data can be sent. On the unsandboxed profile every command can.
    egress = needs_network or not sandboxed
    tainted = bool(prefs.get("_shell_tainted"))
    armed = bool(prefs.get("_shell_autopilot")) and not sandboxed

    basis = ""
    if tier in ("destructive", "denied"):
        requires_confirmation = True
    elif armed:
        requires_confirmation = False
        basis = "armed_window"
    elif tainted and egress:
        # Rule of Two: untrusted content + a way out = always ask.
        requires_confirmation = True
    elif proxied:
        # Untainted and bounded to approved hosts: no prompt, unless the
        # command itself publishes, pushes or uploads.
        requires_confirmation = bool(classification.get("outside_write"))
        basis = "" if requires_confirmation else "egress_proxy"
    elif tier == "safe":
        requires_confirmation = False
        if not sandboxed:
            basis = "open_profile"
        else:
            basis = "allowlist" if needs_network else "sandbox"
    else:
        requires_confirmation = True

    return {
        "is_high_risk": tier in ("destructive", "denied"),
        "risk_level": {"destructive": "high", "denied": "high", "bounded": "medium"}.get(tier, "low"),
        "requires_confirmation": requires_confirmation,
        "tier": tier,
        "shell_tier": raw_tier,
        "shell_reason": classification["reason"],
        "egress": egress,
        "network_mode": mode,
        "approval_basis": basis,
    }


def get_tool_risk_info(
    tool_name: str,
    user_preferences: Optional[Dict[str, Any]] = None,
    tool_input: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    Return risk metadata for a tool call. Used by the agent loop
    to decide whether to pause for confirmation.

    ``run_command`` is risk-classified from its actual ``tool_input`` (the
    command), so its effective risk is dynamic. All other tools keep the
    static catalog policy below.

    Supports user-configurable approval overrides via preferences:
        preferences.approval_overrides = {"send_email": "auto", "withdraw": "always"}
        "auto"   → skip confirmation
        "always" → force confirmation (default for high-risk)
    """
    action = resolve_action_alias(tool_name)
    if action == "run_command":
        return _run_command_risk_info(tool_input, user_preferences)

    catalog_requires = requires_confirmation(action)

    # Check user overrides
    if user_preferences:
        overrides = user_preferences.get("approval_overrides") or {}
        override = overrides.get(action)
        if override == "auto":
            catalog_requires = False
        elif override == "always":
            catalog_requires = True

    return {
        "is_high_risk": is_high_risk_action(action),
        "requires_confirmation": catalog_requires,
        "risk_level": (get_action_definition(action) or {}).get("risk_level", "low"),
    }
