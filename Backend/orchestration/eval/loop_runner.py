"""Run golden loop scenarios through the real agent loop.

A *loop* scenario scripts the model's turns and stubs tool execution so the
gate behaviour (auto-run, pause, refuse) can be checked with no key, no
network and no database. The runner patches the same seams the unit tests do
(``get_llm_client``, ``_execute_with_timeout``) and collects the
``AgentEvent``s the loop yields.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple
from unittest.mock import AsyncMock, MagicMock, patch

from django.test.utils import override_settings

from orchestration import agent_loop as agent_loop_module


@dataclass
class _FakePersona:
    """Minimal stand-in for an active Persona (#203)."""

    tool_scope: List[str] = field(default_factory=list)
    approval_boundary: List[str] = field(default_factory=list)
    risk_ceiling: str = "high"
    name: str = "eval-persona"
    description: str = ""


@dataclass
class LoopRunResult:
    executed: List[str] = field(default_factory=list)
    paused_on: Optional[str] = None
    refused: List[str] = field(default_factory=list)
    events: List[Dict[str, Any]] = field(default_factory=list)


def _scripted_response(turn: Dict[str, Any], index: int) -> Dict[str, Any]:
    """Build one mocked LLM response from a scripted turn."""
    turn = turn or {}
    blocks: List[Dict[str, Any]] = []
    text = turn.get("text") or ""
    if text:
        blocks.append({"type": "text", "text": text})
    tool_calls = turn.get("tool_calls") or []
    for position, call in enumerate(tool_calls):
        blocks.append({
            "type": "tool_use",
            "id": f"script{index}_{position}",
            "name": (call or {}).get("name", ""),
            "input": (call or {}).get("input") or {},
        })
    return {
        "content": blocks,
        "stop_reason": "tool_use" if tool_calls else "end_turn",
        "usage": {"input_tokens": 12, "output_tokens": 4},
    }


def _tool_result_for(tool_results: Dict[str, Any], tool_name: str) -> Dict[str, Any]:
    configured = (tool_results or {}).get(tool_name)
    if isinstance(configured, dict):
        return dict(configured)
    return {"status": "success"}


async def _run_loop(scenario: Dict[str, Any]) -> LoopRunResult:
    loop_cfg = scenario.get("loop") or {}
    responses = [
        _scripted_response(turn, index)
        for index, turn in enumerate(loop_cfg.get("llm_script") or [])
    ]
    mock_llm = MagicMock()
    mock_llm.create_message = AsyncMock(side_effect=responses)

    tool_results = loop_cfg.get("tool_results") or {}

    async def _fake_execute(tool_name, tool_input, context, timeout=None):
        return _tool_result_for(tool_results, tool_name)

    async def _fake_meta_tool(
        tool_name, tool_input, context, preferences, parent_system, parent_tools,
    ):
        return _tool_result_for(tool_results, tool_name)

    refused: List[str] = []
    original_bucket = agent_loop_module._bucket_tool_calls

    def _recording_bucket(tool_calls, preferences, persona=None, tainted=False):
        auto, pause, denied = original_bucket(
            tool_calls, preferences, persona=persona, tainted=tainted,
        )
        refused.extend(tc.get("name") for tc, _reason in denied)
        return auto, pause, denied

    mock_cache = MagicMock()
    mock_cache.get.return_value = None
    if loop_cfg.get("tainted"):
        mock_cache.get.side_effect = (
            lambda key, *args, **kwargs: 1 if "taint" in str(key) else None
        )

    patchers = [
        patch.object(agent_loop_module, "get_llm_client", return_value=mock_llm),
        patch.object(agent_loop_module, "_execute_with_timeout", new=_fake_execute),
        patch.object(agent_loop_module, "_execute_meta_tool", new=_fake_meta_tool),
        patch.object(agent_loop_module, "_bucket_tool_calls", new=_recording_bucket),
        patch.object(agent_loop_module, "_record_receipt", new=AsyncMock()),
        patch.object(agent_loop_module, "update_memory_state", new=AsyncMock()),
        patch.object(agent_loop_module, "save_memory_summary", new=AsyncMock()),
        patch.object(
            agent_loop_module, "preview_tool", new=AsyncMock(return_value=None),
        ),
        patch.object(
            agent_loop_module, "save_pending_confirmation", new=AsyncMock(),
        ),
        patch.object(agent_loop_module, "record_event", new=MagicMock()),
        patch.object(agent_loop_module, "cache", new=mock_cache),
    ]

    if loop_cfg.get("persona_scope") is not None:
        persona = _FakePersona(tool_scope=list(loop_cfg.get("persona_scope") or []))
        patchers.append(
            patch.object(
                agent_loop_module, "_resolve_persona",
                new=AsyncMock(return_value=persona),
            )
        )

    for patcher in patchers:
        patcher.start()
    try:
        result = LoopRunResult()
        context = {"user_id": None, "room_id": 1, "username": "eval"}
        async for event in agent_loop_module.run_agent_loop(
            user_message=scenario.get("message") or "",
            context=context,
            preferences=loop_cfg.get("preferences"),
        ):
            result.events.append({"kind": event.kind, "data": event.data})
            if event.kind == "tool_start":
                result.executed.append(event.data.get("name"))
            elif event.kind == "confirmation":
                result.paused_on = event.data.get("tool_name")
        result.refused = list(refused)
        return result
    finally:
        for patcher in reversed(patchers):
            patcher.stop()


def run_loop_scenario(scenario: Dict[str, Any]) -> LoopRunResult:
    """Run one loop scenario hermetically and return what the loop did."""
    with override_settings(
        SHELL_EGRESS_PROXY=False,
        SHELL_EXEC_NETWORK_ALLOWLIST=[],
        SHELL_EXEC_PROFILE="standard",
        AGENT_TAINT_TTL_SECONDS=900,
    ):
        return asyncio.run(_run_loop(scenario))


def evaluate_loop_scenario(
    scenario: Dict[str, Any],
) -> Tuple[bool, List[str], LoopRunResult]:
    """Compare a loop scenario's expectations against what the loop did."""
    result = run_loop_scenario(scenario)
    details: List[str] = []

    expected_executed = scenario.get("expected_executed")
    if expected_executed is not None and list(expected_executed) != result.executed:
        details.append(f"executed {result.executed} != {list(expected_executed)}")

    if "expected_paused_on" in scenario:
        expected_pause = scenario.get("expected_paused_on")
        if expected_pause != result.paused_on:
            details.append(f"paused_on {result.paused_on!r} != {expected_pause!r}")

    expected_refused = scenario.get("expected_refused")
    if expected_refused is not None and list(expected_refused) != result.refused:
        details.append(f"refused {result.refused} != {list(expected_refused)}")

    return (not details), details, result
