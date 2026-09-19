#!/usr/bin/env python3
"""Architecture boundary ratchet for Kazi Core.

Fails on NEW violations AND on stale baseline entries (a resolved violation
must be removed from the baseline in the same PR). `--update-baseline` refuses
to add entries — it only removes resolved ones. The baseline may only shrink.

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


def package_of(path: Path) -> list[str]:
    # __init__.py IS the package: keep all parts so relative imports resolve
    # against the package name itself. Regular modules drop their own name.
    parts = module_of(path).split(".")
    if path.name == "__init__.py":
        return parts
    return parts[:-1]


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


def alias_map(tree: ast.AST, pkg: list[str]) -> dict[str, str]:
    aliases: dict[str, str] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for a in node.names:
                if a.asname:
                    aliases[a.asname] = a.name
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                base = pkg[: len(pkg) - (node.level - 1)] if node.level > 1 else pkg
                module = ".".join(base + ([node.module] if node.module else []))
            else:
                module = node.module or ""
            if module:
                for a in node.names:
                    if a.asname:
                        aliases[a.asname] = f"{module}.{a.name}"
    return aliases


def resolve_call(name: str, aliases: dict[str, str]) -> str:
    root, _, rest = name.partition(".")
    if root in aliases:
        return aliases[root] + (f".{rest}" if rest else "")
    return name


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
        pkg = package_of(path)
        mods = imported_modules(tree, pkg)
        aliases = alias_map(tree, pkg)
        calls: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                name = dotted(node.func)
                if name:
                    calls.add(resolve_call(name, aliases))
        found.update(check_rules(rel, mods, calls))
    return found


def main(argv: list[str]) -> int:
    found = scan()
    if "--update-baseline" in argv:
        if BASELINE.exists():
            old = set(json.loads(BASELINE.read_text()))
            new_keys = sorted(set(found) - old)
            if new_keys:
                print("Refusing to grow the baseline. New violations must be fixed, not baselined:")
                for key in new_keys:
                    print(f"  - {key}")
                print("Only resolved entries may be removed. Ask a human if this is wrong.")
                return 1
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
        print(f"{len(stale)} baseline entr{'y' if len(stale) == 1 else 'ies'} resolved. "
              "Run --update-baseline in the same PR to lock in the improvement.")
    return 1 if (new or stale) else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
