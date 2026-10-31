"""Measure the tool payload and system prompt for two rooms, offline.

Deliverable 9 (T-F2): report the tool count and tokens-per-call for an empty
conversation (a) in a plain room and (b) in a room with the shell enabled, on
this install's configuration. No network, no API key, no LLM call — it builds
the real tool list and system prompt the way the agent loop does.

Run from the repo root with the project venv:

    PYTHONPATH=Backend DJANGO_DEBUG=True KAZI_DEV_INMEMORY=1 \\
        C:/.../kazi-core/.venv/Scripts/python.exe scripts/measure_tool_payload.py

Tokens are estimated at 4 characters/token (the same ratio the roadmap uses).
The same script runs on any revision: it introspects the prompt builder so the
"before" and "after" numbers come from the identical measurement.
"""
from __future__ import annotations

import inspect
import json
import os
import sys

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "Backend.settings")
os.environ.setdefault("DJANGO_DEBUG", "True")
os.environ.setdefault("KAZI_DEV_INMEMORY", "1")

import django  # noqa: E402

django.setup()

from orchestration.action_catalog import resolve_action_alias  # noqa: E402
from orchestration.agent_loop import META_TOOL_DEFINITIONS  # noqa: E402
from orchestration.agent_prompts import (  # noqa: E402
    build_environment_block,
    build_system_prompt,
)
from orchestration.connector_registry import reset_registry  # noqa: E402
from orchestration.tool_schemas import get_tool_definitions  # noqa: E402
from orchestration.user_preferences import normalize_preferences  # noqa: E402

OPEN_ENV = {
    "profile": "open", "sandboxed": False, "platform": "Windows",
    "shell": "cmd.exe", "taint_minutes": 15,
}

PLAIN_CTX = "ROOM CONTEXT:\n- nothing yet"


def _build_room(*, shell_enabled: bool):
    if shell_enabled:
        os.environ["SHELL_EXEC_TOKEN"] = "local-measure-only"  # nosec B105 - placeholder for an offline measurement, not a credential
    else:
        os.environ.pop("SHELL_EXEC_TOKEN", None)
    os.environ.pop("TELEGRAM_BOT_TOKEN", None)
    reset_registry()

    prefs = normalize_preferences({})
    tools = get_tool_definitions(user_capabilities=prefs)
    tools.extend(META_TOOL_DEFINITIONS)
    names = [t["name"] for t in tools]

    params = inspect.signature(build_system_prompt).parameters
    if "environment_block" in params:
        # After T-F2: the shell section rides only when run_command is offered.
        env_block = build_environment_block(OPEN_ENV) if shell_enabled else ""
        system = build_system_prompt(
            preferences=prefs, context_prompt=PLAIN_CTX,
            environment_block=env_block, tool_names=names,
        )
    else:
        # Before T-F2: the coordinator always folded an environment block into
        # the room context, offered shell or not.
        context = "\n\n".join(
            [build_environment_block(OPEN_ENV), PLAIN_CTX]
        ).strip()
        system = build_system_prompt(preferences=prefs, context_prompt=context)

    tools_chars = sum(len(json.dumps(t)) for t in tools)
    return {
        "tool_count": len(tools),
        "tools_chars": tools_chars,
        "system_chars": len(system),
        "total_chars": tools_chars + len(system),
        "has_run_command": "run_command" in names,
        "has_telegram": any(n.startswith("send_telegram") or n == "telegram_health" for n in names),
        "env_in_prompt": "Where you are running" in system,
        "resolved_run_command": resolve_action_alias("run_command"),
    }


def main() -> int:
    rows = []
    for label, shell in (("plain room", False), ("shell-enabled room", True)):
        m = _build_room(shell_enabled=shell)
        rows.append((label, m))

    print("T-F2 tool-payload measurement (empty conversation, no network)")
    print("tokens = 4 chars/token")
    print(f"{'room':<20} {'tools':>5} {'tool tok':>9} {'prompt tok':>11} {'total tok':>10}  run_command telegram env_in_prompt")
    for label, m in rows:
        print(
            f"{label:<20} {m['tool_count']:>5} {m['tools_chars'] // 4:>9} "
            f"{m['system_chars'] // 4:>11} {m['total_chars'] // 4:>10}  "
            f"{str(m['has_run_command']):>11} {str(m['has_telegram']):>8} {str(m['env_in_prompt']):>13}"
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
