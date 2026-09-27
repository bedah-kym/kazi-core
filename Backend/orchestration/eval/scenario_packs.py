"""Scenario pack helpers for the golden eval harness.

A *pack* is a capability-labeled group of scenarios. #171 makes each capability
ship at least ``SCENARIO_PACK_MIN_SIZE`` scenarios (default 3) so coverage is
explicit and reviewable instead of an ever-growing flat list.
"""
from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path
from typing import Dict, Iterable, List, Optional

DEFAULT_PACK = "uncategorized"


def load_scenarios(path: str) -> List[Dict]:
    """Load a golden scenario JSON file (a list). Returns [] for a non-list."""
    with open(path, "r", encoding="utf-8") as handle:
        data = json.load(handle)
    return data if isinstance(data, list) else []


def default_scenarios_path() -> Path:
    return Path(__file__).resolve().parent / "golden_scenarios.json"


def pack_name(scenario: Dict) -> str:
    return str(scenario.get("pack") or DEFAULT_PACK)


def group_by_pack(scenarios: Iterable[Dict]) -> Dict[str, List[Dict]]:
    grouped: Dict[str, List[Dict]] = defaultdict(list)
    for scenario in scenarios:
        grouped[pack_name(scenario)].append(scenario)
    return dict(grouped)


def select_pack(scenarios: Iterable[Dict], name: str) -> List[Dict]:
    return [scenario for scenario in scenarios if pack_name(scenario) == name]


def undersized_packs(
    scenarios: Iterable[Dict],
    min_size: int = 3,
) -> Dict[str, int]:
    """Return ``{pack: size}`` for every pack smaller than ``min_size``.

    Empty dict means every pack meets the bar. Callers (the test suite) fail
    on a non-empty result.
    """
    grouped = group_by_pack(scenarios)
    return {name: len(items) for name, items in grouped.items() if len(items) < int(min_size)}


def scenario_pack_min_size(default: int = 3) -> int:
    """Read the (versioned) minimum pack size from settings when available."""
    try:
        from django.conf import settings
        return int(getattr(settings, "SCENARIO_PACK_MIN_SIZE", default) or default)
    except Exception:
        return default


def verify_scenarios(scenarios: Optional[Iterable[Dict]] = None) -> Dict[str, int]:
    """Convenience: load the default file when none is given and check sizes."""
    if scenarios is None:
        scenarios = load_scenarios(str(default_scenarios_path()))
    return undersized_packs(scenarios, scenario_pack_min_size())
