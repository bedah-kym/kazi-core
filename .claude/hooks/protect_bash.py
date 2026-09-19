#!/usr/bin/env python3
"""PreToolUse hook for Bash: block obviously destructive commands and
mutations of protected paths.

Same charter as protect_paths.py: a local guardrail that fails open
(malformed input is allowed with a warning), never a security boundary.
No arbitrary shell parsing: only explicitly recognized wrappers and a
small list of known mutating tools are checked. Anything unrecognized
passes.

Exit code 2 = block; stderr is shown to the agent.
"""
import json
import os
import re
import sys
from pathlib import Path

ROOT = Path(os.environ.get("CLAUDE_PROJECT_DIR", ".")).resolve()
PATTERN_FILE = ROOT / ".claude" / "protected_paths.txt"

_WRAPPER_RE = re.compile(
    r"^(?:/usr/bin/env\s+|env\s+|sudo\s+(?:-n\s+)?|nohup\s+|cmd\s+/c\s+)",
    re.IGNORECASE,
)
_ABS_PATH_RE = re.compile(r"^(?:/usr/bin/|/bin/|/usr/local/bin/|/sbin/)")
_DESTRUCTIVE_PREFIXES = (
    "rm -rf ",
    "rm -fr ",
    "rm --recursive ",
    "git push --force",
    "git push -f ",
    "git reset --hard",
)
_MUTATING_TOOLS = (
    "set-content",
    "add-content",
    "out-file",
    "remove-item",
    "move-item",
    "copy-item",
    "del ",
    "erase ",
    "rm ",
    "rmdir ",
    "mv ",
    "cp ",
    "truncate",
)


def _patterns() -> list[str]:
    if not PATTERN_FILE.exists():
        return []
    return [
        line.strip()
        for line in PATTERN_FILE.read_text().splitlines()
        if line.strip() and not line.startswith("#")
    ]


def _static_prefix(pattern: str) -> str:
    # Only patterns with wildcards confined to the tail ("prefix/*") yield a
    # usable literal prefix. Anything fancier returns "" (no partial match).
    body = pattern
    while body.endswith("*"):
        body = body[:-1]
    if "*" in body or "?" in body or "[" in body:
        return ""
    return body


def _mentions_protected_path(command: str) -> bool:
    lowered = command.lower()
    for pattern in _patterns():
        prefix = _static_prefix(pattern)
        if prefix and prefix.lower() in lowered:
            return True
    return False


def _is_mutating(command: str) -> bool:
    lowered = command.lower()
    return any(tool in lowered for tool in _MUTATING_TOOLS)


def normalize(command: str) -> str:
    command = _WRAPPER_RE.sub("", command.strip(), count=1)
    return _ABS_PATH_RE.sub("", command, count=1)


def main() -> int:
    if os.environ.get("KAZI_ALLOW_PROTECTED") == "1":
        return 0
    try:
        payload = json.load(sys.stdin)
        command = str((payload.get("tool_input") or {}).get("command") or "")
    except Exception as exc:  # malformed input: warn, allow
        print(f"protect_bash: cannot parse hook input ({exc}); allowing", file=sys.stderr)
        return 0
    if not command.strip():
        return 0

    normalized = normalize(command).lower()
    for prefix in _DESTRUCTIVE_PREFIXES:
        if normalized.startswith(prefix):
            print(
                f"BLOCKED: command starts with a denied destructive form ('{prefix.strip()}').\n"
                "Refusing to run. Ask the human if this command is truly needed.",
                file=sys.stderr,
            )
            return 2

    if _is_mutating(command) and _mentions_protected_path(command):
        print(
            "BLOCKED: command mutates a file under a protected path "
            "(see .claude/protected_paths.txt).\n"
            "Do not route around this. Write a plan and ask the human; they can "
            "allow edits for a session with KAZI_ALLOW_PROTECTED=1.",
            file=sys.stderr,
        )
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
