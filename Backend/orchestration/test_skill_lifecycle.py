"""Tests for the v0.7 skill lifecycle and curator (#138)."""
from __future__ import annotations

import os
import tempfile
from datetime import timedelta

from django.test import SimpleTestCase, override_settings
from django.utils import timezone

from orchestration.skill_registry import (
    curate_skills,
    discover_skills,
    set_skill_pinned,
    transition_skill,
)


def _write_skill(root, name, stage="staging", pinned=False):
    folder = os.path.join(root, name)
    os.makedirs(folder, exist_ok=True)
    lines = [
        "---",
        f"name: {name}",
        "description: lifecycle test skill",
        "tools: []",
        f"stage: {stage}",
    ]
    if pinned:
        lines.append("pinned: true")
    lines.append("---")
    lines.append("body")
    with open(os.path.join(folder, "SKILL.md"), "w", encoding="utf-8") as handle:
        handle.write("\n".join(lines) + "\n")
    return folder


class SkillLifecycleTests(SimpleTestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = self.tmp.name
        self.settings = override_settings(SKILLS_DIR=self.root)
        self.settings.enable()
        self.addCleanup(self.settings.disable)

    def _stage(self, name):
        for skill in discover_skills(include_inactive=True):
            if skill["name"] == name:
                return skill["stage"]
        return None

    def test_promotion_runs_staging_review_active(self):
        _write_skill(self.root, "promote-me", "staging")
        self.assertEqual(transition_skill("promote-me", "review")["status"], "success")
        self.assertEqual(transition_skill("promote-me", "active")["status"], "success")
        self.assertEqual(self._stage("promote-me"), "active")

    def test_illegal_transition_is_rejected(self):
        _write_skill(self.root, "skip-review", "staging")
        result = transition_skill("skip-review", "active")
        self.assertEqual(result["status"], "error")
        self.assertIn("Illegal", result["message"])
        self.assertEqual(self._stage("skip-review"), "staging")

    def test_demotion_is_reversible(self):
        _write_skill(self.root, "reversible", "active")
        self.assertEqual(transition_skill("reversible", "stale")["status"], "success")
        self.assertEqual(transition_skill("reversible", "active")["status"], "success")
        self.assertEqual(self._stage("reversible"), "active")

    def test_archived_returns_to_stale_not_active(self):
        _write_skill(self.root, "archive-me", "stale")
        self.assertEqual(transition_skill("archive-me", "archived")["status"], "success")
        self.assertEqual(transition_skill("archive-me", "active")["status"], "error")
        self.assertEqual(transition_skill("archive-me", "stale")["status"], "success")

    def test_pinned_skill_cannot_be_archived(self):
        _write_skill(self.root, "pinned-skill", "active", pinned=True)
        result = transition_skill("pinned-skill", "archived")
        self.assertEqual(result["status"], "error")
        self.assertIn("pinned", result["message"])
        self.assertEqual(self._stage("pinned-skill"), "active")

    def test_pin_toggle_is_persisted(self):
        _write_skill(self.root, "pin-toggle", "active")
        self.assertEqual(set_skill_pinned("pin-toggle", True)["pinned"], True)
        skill = next(s for s in discover_skills(include_inactive=True) if s["name"] == "pin-toggle")
        self.assertTrue(skill["pinned"])


class SkillCuratorTests(SimpleTestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = self.tmp.name
        self.settings = override_settings(
            SKILLS_DIR=self.root,
            SKILL_STALE_AFTER_DAYS=90,
            SKILL_ARCHIVE_AFTER_DAYS=180,
        )
        self.settings.enable()
        self.addCleanup(self.settings.disable)
        self.now = timezone.now()

    def _stage(self, name):
        for skill in discover_skills(include_inactive=True):
            if skill["name"] == name:
                return skill["stage"]
        return None

    def test_curator_demotes_unused_active_skill(self):
        _write_skill(self.root, "old-skill", "active")
        result = curate_skills(
            now=self.now,
            usage={"old-skill": self.now - timedelta(days=120)},
        )
        self.assertEqual(result["stale"], ["old-skill"])
        self.assertEqual(self._stage("old-skill"), "stale")

    def test_curator_leaves_recently_used_skill_active(self):
        _write_skill(self.root, "fresh-skill", "active")
        result = curate_skills(
            now=self.now,
            usage={"fresh-skill": self.now - timedelta(days=3)},
        )
        self.assertEqual(result["stale"], [])
        self.assertEqual(self._stage("fresh-skill"), "active")

    def test_curator_archives_stale_skill(self):
        _write_skill(self.root, "stale-skill", "stale")
        result = curate_skills(
            now=self.now,
            usage={"stale-skill": self.now - timedelta(days=400)},
        )
        self.assertEqual(result["archived"], ["stale-skill"])
        self.assertEqual(self._stage("stale-skill"), "archived")

    def test_curator_never_promotes_candidate(self):
        _write_skill(self.root, "candidate-skill", "staging")
        result = curate_skills(
            now=self.now,
            usage={"candidate-skill": self.now - timedelta(days=400)},
        )
        self.assertEqual(result, {"stale": [], "archived": [], "skipped_pinned": []})
        self.assertEqual(self._stage("candidate-skill"), "staging")

    def test_curator_skips_pinned_skill(self):
        _write_skill(self.root, "pinned-old", "active", pinned=True)
        result = curate_skills(
            now=self.now,
            usage={"pinned-old": self.now - timedelta(days=400)},
        )
        self.assertEqual(result["skipped_pinned"], ["pinned-old"])
        self.assertEqual(result["archived"], [])
        self.assertEqual(self._stage("pinned-old"), "active")

    def test_curator_dry_run_writes_nothing(self):
        _write_skill(self.root, "dry-run-skill", "active")
        result = curate_skills(
            dry_run=True,
            now=self.now,
            usage={"dry-run-skill": self.now - timedelta(days=120)},
        )
        self.assertEqual(result["stale"], ["dry-run-skill"])
        self.assertEqual(self._stage("dry-run-skill"), "active")
