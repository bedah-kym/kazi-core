"""Check docs/architecture/model.js against the code.

Fails when a referenced file is missing, a referenced symbol no longer appears
in its file, or a scenario step points at a level, edge or node the model does
not define. Run: python scripts/check_architecture_model.py
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MODEL = ROOT / "docs" / "architecture" / "model.js"
PREFIX = "window.KAZI_ARCH ="


def load_model() -> dict:
    text = MODEL.read_text(encoding="utf-8").strip()
    if not text.startswith(PREFIX):
        raise SystemExit(f"{MODEL}: must start with '{PREFIX}'")
    return json.loads(text[len(PREFIX):].rstrip().rstrip(";"))


def check_ref(ref: dict, where: str, errors: list[str], cache: dict[str, str]) -> None:
    path = ROOT / ref["file"]
    if not path.is_file():
        errors.append(f"{where}: file not found: {ref['file']}")
        return
    if ref["file"] not in cache:
        cache[ref["file"]] = path.read_text(encoding="utf-8", errors="replace")
    symbol = ref.get("symbol")
    if symbol and not re.search(r"(?<![\w-])" + re.escape(symbol) + r"(?![\w-])", cache[ref["file"]]):
        errors.append(f"{where}: '{symbol}' not found in {ref['file']}")


def main() -> int:
    model = load_model()
    errors: list[str] = []
    cache: dict[str, str] = {}
    levels = model["levels"]
    refs_checked = 0
    for level_id, level in levels.items():
        if level.get("parent") and level["parent"] not in levels:
            errors.append(f"level {level_id}: unknown parent {level['parent']}")
        node_ids = {n["id"] for n in level["nodes"]}
        for node in level["nodes"]:
            for key in ("zoom", "goto"):
                if node.get(key) and node[key] not in levels:
                    errors.append(f"{level_id}/{node['id']}: {key} points at unknown level {node[key]}")
            for ref in node.get("refs", []):
                check_ref(ref, f"{level_id}/{node['id']}", errors, cache)
                refs_checked += 1
        for edge in level["edges"]:
            for end in ("from", "to"):
                if edge[end] not in node_ids:
                    errors.append(f"{level_id} edge {edge['id']}: unknown node {edge[end]}")
    for scenario in model["scenarios"]:
        for i, step in enumerate(scenario["steps"], 1):
            where = f"scenario {scenario['id']} step {i}"
            level = levels.get(step["level"])
            if level is None:
                errors.append(f"{where}: unknown level {step['level']}")
                continue
            if step["edge"] not in {e["id"] for e in level["edges"]}:
                errors.append(f"{where}: edge {step['edge']} is not in level {step['level']}")
            for ref in step.get("refs", []):
                check_ref(ref, where, errors, cache)
                refs_checked += 1
    if errors:
        print(f"architecture model: {len(errors)} problem(s)")
        for line in errors:
            print("  - " + line)
        return 1
    print(f"architecture model OK: {len(levels)} levels, {len(model['scenarios'])} scenarios, {refs_checked} code references")
    return 0


if __name__ == "__main__":
    sys.exit(main())
