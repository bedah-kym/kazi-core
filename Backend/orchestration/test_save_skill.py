"""Tests for the save_skill chat meta-tool (Workstream D)."""
from __future__ import annotations

import os
import tempfile

from asgiref.sync import async_to_sync
from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings

from chatbot.models import Chatroom
from orchestration.agent_loop import _execute_meta_tool
from orchestration.skill_registry import get_skill
from workflows.models import WorkflowDraft

User = get_user_model()


class SaveSkillMetaToolTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="skill-saver", email="example@example.com",
            password="fake-token",  # nosec B106 — test fixture — fake credential
        )
        self.room = Chatroom.objects.create()

    def test_save_skill_writes_staged_folder_and_draft(self):
        with tempfile.TemporaryDirectory() as tmp:
            with override_settings(SKILLS_DIR=tmp):
                result = async_to_sync(_execute_meta_tool)(
                    "save_skill",
                    {
                        "name": "Weekly Check",
                        "description": "Checks the thing weekly.",
                        "tools": "run_command",
                        "when_to_use": "Every Friday",
                        "inputs": "shell",
                        "sequence": "1. run the check\n2. report",
                        "validation": "exit 0",
                        "returns": "summary",
                        "approval": "deletes",
                    },
                    {"user_id": self.user.id, "room_id": self.room.id},
                    None,
                    "",
                    [],
                )
                self.assertEqual(result["status"], "success")
                skill_md = os.path.join(tmp, "weekly-check", "SKILL.md")
                self.assertTrue(os.path.isfile(skill_md))
                with open(skill_md, encoding="utf-8") as handle:
                    body = handle.read()
                self.assertIn("stage: staging", body)
                self.assertIn("1. run the check", body)

                # Staged, never active — and a reviewable draft exists.
                self.assertIsNone(get_skill("weekly-check"))
                draft = WorkflowDraft.objects.get(user=self.user, skill_name="weekly-check")
                self.assertEqual(draft.source, "explicit_save")
                self.assertEqual(draft.status, "draft")

    def test_save_skill_requires_a_name(self):
        result = async_to_sync(_execute_meta_tool)(
            "save_skill", {"description": "no name"}, {"user_id": self.user.id}, None, "", [],
        )
        self.assertEqual(result["status"], "error")
