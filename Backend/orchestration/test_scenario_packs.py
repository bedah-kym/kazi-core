from __future__ import annotations

import os
import unittest

from django.conf import settings

from orchestration.eval.scenario_packs import (
    group_by_pack,
    load_scenarios,
    scenario_pack_min_size,
    undersized_packs,
)


def _path() -> str:
    return os.path.join(settings.BASE_DIR, "orchestration", "eval", "golden_scenarios.json")


class ScenarioPackTests(unittest.TestCase):
    def test_min_size_is_three(self):
        self.assertEqual(scenario_pack_min_size(), 3)

    def test_every_pack_meets_min_size(self):
        scenarios = load_scenarios(_path())
        min_size = scenario_pack_min_size()
        undersized = undersized_packs(scenarios, min_size)
        self.assertEqual(undersized, {}, f"packs below {min_size}: {undersized}")

    def test_expected_capability_packs_exist(self):
        groups = group_by_pack(load_scenarios(_path()))
        self.assertTrue({"orchestration", "injection", "shell"} <= set(groups))

    def test_shell_pack_covers_every_tier(self):
        shell = group_by_pack(load_scenarios(_path()))["shell"]
        tiers = {scenario.get("expected_shell_tier") for scenario in shell}
        self.assertTrue({"safe", "bounded", "destructive", "denied"} <= tiers)

    def test_undersized_packs_flags_a_small_pack(self):
        self.assertEqual(undersized_packs([{"pack": "x"}], 3), {"x": 1})
        self.assertEqual(undersized_packs([{"pack": "x"}] * 3, 3), {})
