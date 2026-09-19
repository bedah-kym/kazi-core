#!/usr/bin/env python3
"""Architecture boundary ratchet for Kazi Core.

Fails only on NEW violations. Existing ones live in scripts/boundary_baseline.json
and may only shrink. First install: run `--update-baseline` once and review the diff.
Never run `--update-baseline` to hide a violation you just introduced.

Error messages say what to do instead. Agents read them.
"""
from __future__ import annotations

import ast
import fnmatch
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BASELINE = ROOT / "scripts" / "boundary_baseline.json"
SCAN_ROOT = ROOT / "Backend"  # imports are relative to Backend/ (e.g. `orchestration.base_connector`)
SKIP_PARTS = {"migrations", "tests", "test", "__pycache__", ".venv", "venv", "node_modules"}

RULES = [
    {
        "id": "connector-isolation",
        "applies_to": "Backend/orchestration/connectors/*.py",
        "exclude": [],
        "forbid_import": [
            "orchestration.agent_loop",
            "orchestration.tool_executor",
            "orchestration.tool_router",
            "orchestration.security_policy",
            "orchestration.action_receipts",
            "orchestration.workflow_planner",
        ],
        "forbid_call": [],
        "fix": "Connectors must not reach into core. Return data from execute(); "
        "the executor owns policy, approvals and receipts.",
    },
    {
        "id": "no-raw-shell",
        "applies_to": "Backend/*.py",
        # Add the shell-exec sidecar path here once it exists: the sidecar is the
        # one deliberate exception to "no subprocess in Django code".
        "exclude": [],
        "forbid_import": ["subprocess"],
        "forbid_call": ["os.system", "os.popen", "eval", "exec"],
        "fix": "Shell reach goes through the run_command connector and the sandbox. "
        "Do not spawn processes or eval strings from Django code.",
    },
]


def match(rel: str, pattern: str) -> bool:
    return fnmatch.fnmatch(rel, pattern) or fnmatch.fnmatch(rel, pattern.replace("**/", ""))


def module_of(path: Path) -> str:
    rel = path.relative_to(SCAN_ROOT).with_suffix("")
    parts = list(rel.parts)
    if parts and parts[-1] == "__init__":
        parts.pop()
    return ".".join(parts)


def dotted(node: ast.AST) -> str | None:
    parts: list[str] = []
    while isinstance(node, ast.Attribute):
        parts.append(node.attr)
        node = node.value
    if isinstance(node, ast.Name):
        parts.append(node.id)
        return ".".join(reversed(parts))
    return None


def imported_modules(tree: ast.AST, pkg: list[str]) -> set[str]:
    mods: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            mods.update(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                base = pkg[: len(pkg) - (node.level - 1)] if node.level > 1 else pkg
                name = ".".join(base + ([node.module] if node.module else []))
            else:
                name = node.module or ""
            if name:
                mods.add(name)
                mods.update(f"{name}.{a.name}" for a in node.names)
    return mods


def check_rules(rel: str, mods: set[str], calls: set[str]) -> dict[str, str]:
    found: dict[str, str] = {}
    for rule in RULES:
        if not match(rel, rule["applies_to"]) or any(match(rel, e) for e in rule["exclude"]):
            continue
        for bad in rule["forbid_import"]:
            if any(m == bad or m.startswith(bad + ".") for m in mods):
                found[f"{rule['id']}|{rel}|import {bad}"] = rule["fix"]
        for bad in rule["forbid_call"]:
            if bad in calls:
                found[f"{rule['id']}|{rel}|call {bad}"] = rule["fix"]
    return found


def scan() -> dict[str, str]:
    """Return {violation_key: fix_message}."""
    found: dict[str, str] = {}
    if not SCAN_ROOT.exists():
        return found
    for path in sorted(SCAN_ROOT.rglob("*.py")):
        if SKIP_PARTS & set(path.relative_to(ROOT).parts):
            continue
        rel = path.relative_to(ROOT).as_posix()
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except (SyntaxError, UnicodeDecodeError):
            continue
        pkg = module_of(path).split(".")[:-1]
        mods = imported_modules(tree, pkg)
        calls = {dotted(n.func) for n in ast.walk(tree) if isinstance(n, ast.Call)} - {None}
        found.update(check_rules(rel, mods, calls))
    return found


def main(argv: list[str]) -> int:
    found = scan()
    if "--update-baseline" in argv:
        BASELINE.write_text(json.dumps(sorted(found), indent=2) + "\n")
        print(f"baseline written: {len(found)} known violation(s)")
        return 0
    if not BASELINE.exists() and found:
        print("No baseline yet. Run `python scripts/check_boundaries.py --update-baseline` once "
              "and review the resulting file.")
        return 1
    baseline = set(json.loads(BASELINE.read_text())) if BASELINE.exists() else set()
    new = sorted(set(found) - baseline)
    stale = sorted(baseline - set(found))
    for key in new:
        rule_id, rel, detail = key.split("|", 2)
        print(f"BOUNDARY VIOLATION [{rule_id}] {rel}: {detail}\n  fix: {found[key]}")
    if stale:
        print(f"note: {len(stale)} baseline entr{'y' if len(stale) == 1 else 'ies'} resolved. "
              "Run --update-baseline to lock in the improvement.")
    return 1 if new else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
