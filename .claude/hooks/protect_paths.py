#!/usr/bin/env python3
"""PreToolUse hook: block agent edits to protected paths.

Local guardrail against accidents, not a security boundary. The hard layer is
CODEOWNERS + branch protection. Fails open (with a warning) if input is malformed,
so a hook bug never bricks the session.

A human can allow edits for a session:  KAZI_ALLOW_PROTECTED=1 claude
Exit code 2 = block; stderr is shown to the agent.
"""
import fnmatch
import json
import os
import sys
from pathlib import Path

ROOT = Path(os.environ.get("CLAUDE_PROJECT_DIR", ".")).resolve()
PATTERN_FILE = ROOT / ".claude" / "protected_paths.txt"


def matches(rel: str, pattern: str) -> bool:
    return fnmatch.fnmatch(rel, pattern) or fnmatch.fnmatch(rel, pattern.replace("**/", ""))


def matching_pattern(rel: str) -> str | None:
    if not PATTERN_FILE.exists():
        return None
    for line in PATTERN_FILE.read_text().splitlines():
        pattern = line.strip()
        if not pattern or pattern.startswith("#"):
            continue
        if matches(rel, pattern):
            return pattern
    return None


def main() -> int:
    if os.environ.get("KAZI_ALLOW_PROTECTED") == "1":
        return 0
    try:
        payload = json.load(sys.stdin)
        file_path = (payload.get("tool_input") or {}).get("file_path")
    except Exception as exc:  # malformed input: warn, allow
        print(f"protect_paths: cannot parse hook input ({exc}); allowing", file=sys.stderr)
        return 0
    if not file_path:
        return 0
    path = Path(file_path)
    if not path.is_absolute():
        path = ROOT / path
    try:
        rel = path.resolve().relative_to(ROOT).as_posix()
    except ValueError:
        return 0  # outside the project
    pattern = matching_pattern(rel)
    if pattern:
        print(
            f"BLOCKED: '{rel}' is a protected path (matches '{pattern}').\n"
            "Do not route around this. Copy docs/plans/TEMPLATE.md, state why the change "
            "is needed and what verifies it, then ask the human. They can allow edits for a "
            "session with KAZI_ALLOW_PROTECTED=1.",
            file=sys.stderr,
        )
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
