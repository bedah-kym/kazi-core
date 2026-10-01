"""Tests for the v0.7 promotion pipeline (W-B, issue #156)."""
from __future__ import annotations

import json
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, patch

from asgiref.sync import async_to_sync
from django.contrib.auth import get_user_model
from django.test import SimpleTestCase, TestCase, override_settings

from workflows.models import UserWorkflow, WorkflowCandidate, WorkflowDraft
from workflows.promotion import (
    PromotionError,
    draft_workflow_from_candidate,
    is_save_skill_request,
    mine_candidates,
    queue_candidates,
    save_session_as_skill,
    write_staged_skill,
)
from workflows.tasks import mine_workflow_candidates

User = get_user_model()


def _event(user_id, room_id, tools, statuses=None, ts="2026-10-01T00:00:00+00:00"):
    statuses = statuses or ["success"] * len(tools)
    return {
        "event": "agent_loop_done",
        "ts": ts,
        "user_id": user_id,
        "room_id": room_id,
        "transcript": [
            {"tool": tool, "status": statuses[index]}
            for index, tool in enumerate(tools)
        ],
    }


VALID_DEFINITION = {
    "workflow_name": "Weekly backup check",
    "workflow_description": "Check the backup and email a summary.",
    "triggers": [{"trigger_type": "manual"}],
    "steps": [
        {
            "id": "step_1",
            "service": "gmail",
            "action": "send_email",
            "params": {"to": "example@example.com", "subject": "Backup", "text": "All good"},
            "requires_approval": True,
        }
    ],
}


class _FakeLLM:
    def __init__(self, definition):
        self._definition = definition

    async def generate_text(self, **kwargs):
        return json.dumps({"workflow_definition": self._definition})

    def extract_json(self, text):
        return json.loads(text)


class SaveSkillDetectionTests(SimpleTestCase):
    def test_detects_save_phrases(self):
        self.assertTrue(is_save_skill_request("Save this as a skill"))
        self.assertTrue(is_save_skill_request("ok save what we just did"))
        self.assertFalse(is_save_skill_request("send an email tomorrow"))


class CandidateMiningTests(SimpleTestCase):
    def test_finds_repeated_successful_sequence(self):
        events = [
            _event(1, 2, ["search_info", "send_email"]),
            _event(1, 2, ["search_info", "send_email"]),
            _event(1, 2, ["search_info", "send_email"]),
        ]
        candidates = mine_candidates(events)
        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0]["occurrences"], 3)
        self.assertEqual(candidates[0]["success_rate"], 1.0)
        self.assertEqual(candidates[0]["tool_sequence"], ["search_info", "send_email"])

    def test_ignores_low_success_sequences(self):
        events = [
            _event(1, 2, ["search_info", "send_email"], ["success", "error"]),
            _event(1, 2, ["search_info", "send_email"], ["success", "error"]),
            _event(1, 2, ["search_info", "send_email"]),
        ]
        self.assertEqual(mine_candidates(events), [])

    def test_ignores_single_occurrence_and_single_step(self):
        events = [
            _event(1, 2, ["search_info", "send_email"]),
            _event(1, 2, ["search_info"]),
            _event(1, 2, ["search_info"]),
            _event(1, 2, ["search_info"]),
        ]
        self.assertEqual(mine_candidates(events), [])

    def test_no_events_no_candidates(self):
        self.assertEqual(mine_candidates([]), [])


class StagedSkillTests(SimpleTestCase):
    def test_write_staged_skill_creates_staging_folder(self):
        with tempfile.TemporaryDirectory() as tmp:
            contract = {
                "when_to_use": "Weekly",
                "inputs": ["to"],
                "sequence": ["step_1: gmail send_email"],
                "validation": "Re-run the test run.",
                "returns": "Email summary.",
                "approval": "step_1",
            }
            path = write_staged_skill(
                "Weekly Backup", "Check backups", ["send_email"], contract,
                skills_root_override=Path(tmp),
            )
            raw = path.read_text(encoding="utf-8")
            self.assertIn("stage: staging", raw)
            self.assertIn("## When to use it", raw)
            self.assertIn("## What requires approval", raw)

    def test_refuses_to_clobber_promoted_skill(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp) / "weekly-backup"
            folder.mkdir()
            (folder / "SKILL.md").write_text(
                "---\nname: weekly-backup\nstage: active\n---\nbody", encoding="utf-8"
            )
            with self.assertRaises(PromotionError):
                write_staged_skill(
                    "Weekly Backup", "desc", [], {},
                    skills_root_override=Path(tmp),
                )


class PromotionDraftTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="promoter", email="example@example.com",
            password="fake-token",  # nosec B106 — test fixture — fake credential
        )

    def test_explicit_save_stages_skill_and_awaiting_draft(self):
        with tempfile.TemporaryDirectory() as tmp:
            with override_settings(ORCHESTRATION_TELEMETRY_PATH=str(Path(tmp) / "events.jsonl")):
                Path(tmp, "events.jsonl").write_text(
                    json.dumps(_event(self.user.id, 5, ["search_info", "send_email"])) + "\n",
                    encoding="utf-8",
                )
                draft = async_to_sync(save_session_as_skill)(
                    self.user.id, None,
                    llm=_FakeLLM(VALID_DEFINITION),
                    skills_root_override=Path(tmp) / "skills",
                )

            self.assertEqual(draft.status, "awaiting_confirmation")
            self.assertEqual(draft.source, "explicit_save")
            self.assertTrue(draft.skill_name)
            skill_md = Path(tmp) / "skills" / draft.skill_name / "SKILL.md"
            self.assertTrue(skill_md.is_file())
            self.assertIn("stage: staging", skill_md.read_text(encoding="utf-8"))
            self.assertEqual(UserWorkflow.objects.count(), 0)

    def test_explicit_save_without_recent_session_raises(self):
        with tempfile.TemporaryDirectory() as tmp:
            with override_settings(ORCHESTRATION_TELEMETRY_PATH=str(Path(tmp) / "missing.jsonl")):
                with self.assertRaises(PromotionError):
                    async_to_sync(save_session_as_skill)(self.user.id, 5, llm=_FakeLLM(VALID_DEFINITION))

    def test_statistical_candidate_drafts_but_never_activates(self):
        events = [_event(self.user.id, 5, ["search_info", "send_email"])] * 3
        candidates = mine_candidates(events)
        self.assertEqual(len(candidates), 1)

        queue_result = queue_candidates(candidates)
        self.assertEqual(queue_result["created"], 1)
        candidate = WorkflowCandidate.objects.get(user=self.user)
        self.assertEqual(candidate.status, "candidate")

        with tempfile.TemporaryDirectory() as tmp:
            draft = async_to_sync(draft_workflow_from_candidate)(
                {
                    "user_id": self.user.id,
                    "room_id": None,
                    "tool_sequence": candidate.pattern["tool_sequence"],
                    "occurrences": candidate.occurrences,
                },
                llm=_FakeLLM(VALID_DEFINITION),
                skills_root_override=Path(tmp),
            )

        self.assertEqual(draft.status, "draft")
        self.assertEqual(draft.source, "statistical")
        self.assertEqual(UserWorkflow.objects.count(), 0)

    def test_invalid_drafted_definition_is_rejected(self):
        bad_definition = dict(VALID_DEFINITION)
        bad_definition["steps"] = [{"id": "s", "service": "weather", "action": "make_rain"}]
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(PromotionError):
                async_to_sync(draft_workflow_from_candidate)(
                    {"user_id": self.user.id, "tool_sequence": ["get_weather"]},
                    llm=_FakeLLM(bad_definition),
                    skills_root_override=Path(tmp),
                )
            self.assertEqual(WorkflowDraft.objects.count(), 0)

    def test_mine_task_queues_candidates(self):
        events = [_event(self.user.id, 5, ["search_info", "send_email"])] * 3
        with patch("workflows.promotion.read_telemetry_events", return_value=events):
            result = mine_workflow_candidates()
        self.assertEqual(result["mined"], 1)
        self.assertEqual(result["created"], 1)
        self.assertEqual(WorkflowCandidate.objects.filter(user=self.user).count(), 1)


class WorkflowAgentSaveSkillTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="chatpromoter", email="example@example.com",
            password="fake-token",  # nosec B106 — test fixture — fake credential
        )

    def test_save_skill_message_returns_staged_summary(self):
        from workflows import workflow_agent

        draft = WorkflowDraft.objects.create(
            user=self.user,
            definition=VALID_DEFINITION,
            status="awaiting_confirmation",
            source="explicit_save",
            skill_name="weekly-backup-check",
        )
        with patch.object(
            workflow_agent, "save_session_as_skill", new=AsyncMock(return_value=draft)
        ):
            response = async_to_sync(workflow_agent.handle_workflow_message)(
                self.user.id, None, "save this as a skill"
            )
        self.assertIn("weekly-backup-check", response)
        self.assertIn("approve", response)
