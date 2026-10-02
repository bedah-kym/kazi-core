"""Tests for agent personas (v0.7 W-G, issue #203)."""
from __future__ import annotations

from unittest.mock import MagicMock

from asgiref.sync import async_to_sync
from django.contrib.auth import get_user_model
from django.db import IntegrityError
from django.test import SimpleTestCase, TestCase

from chatbot.models import Chatroom
from orchestration.agent_loop import _bucket_tool_calls
from orchestration.personas import (
    apply_persona_bounds,
    confirm_persona,
    create_persona,
    duplicate_persona,
    persona_bounds,
    resolve_room_persona,
)
from workflows.models import WorkflowDraft

User = get_user_model()


class PersonaBoundsUnitTests(SimpleTestCase):
    def test_out_of_scope_action_is_denied_not_prompted(self):
        persona = MagicMock(tool_scope=["get_weather"], approval_boundary=[], risk_ceiling="medium")
        risk, reason = apply_persona_bounds(
            {"requires_confirmation": False, "risk_level": "low"},
            persona_bounds(persona),
            "send_email",
        )
        self.assertTrue(risk["persona_denied"])
        self.assertIn("outside the persona's tool scope", reason)

    def test_boundary_action_forces_confirmation(self):
        persona = MagicMock(tool_scope=["send_email"], approval_boundary=["send_email"], risk_ceiling="high")
        risk, reason = apply_persona_bounds(
            {"requires_confirmation": False, "risk_level": "low"},
            persona_bounds(persona),
            "send_email",
        )
        self.assertTrue(risk["requires_confirmation"])
        self.assertEqual(reason, "")

    def test_above_ceiling_escalates(self):
        persona = MagicMock(tool_scope=["withdraw"], approval_boundary=[], risk_ceiling="low")
        risk, reason = apply_persona_bounds(
            {"requires_confirmation": False, "risk_level": "high"},
            persona_bounds(persona),
            "withdraw",
        )
        self.assertTrue(risk["requires_confirmation"])
        self.assertEqual(risk["risk_level"], "high")


class PersonaBucketTests(SimpleTestCase):
    def test_out_of_scope_call_is_denied_in_bucket(self):
        persona = MagicMock(tool_scope=["get_weather"], approval_boundary=[], risk_ceiling="high")
        auto, pause, denied = _bucket_tool_calls(
            [{"id": "1", "name": "send_email", "input": {"to": "a@b.c"}}], None, persona=persona,
        )
        self.assertEqual(auto, [])
        self.assertEqual(pause, [])
        self.assertEqual(len(denied), 1)
        self.assertIn("persona", denied[0][1])

    def test_scoped_call_flows_normally(self):
        persona = MagicMock(tool_scope=["get_weather", "send_email"], approval_boundary=[], risk_ceiling="high")
        auto, pause, denied = _bucket_tool_calls(
            [{"id": "1", "name": "get_weather", "input": {"city": "Nairobi"}}], None, persona=persona,
        )
        self.assertEqual(len(auto), 1)
        self.assertEqual(pause, [])
        self.assertEqual(denied, [])


class PersonaModelTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="persona-owner", email="example@example.com",
            password="fake-token",  # nosec B106 — test fixture — fake credential
        )
        self.other = User.objects.create_user(
            username="persona-other", email="other@example.com",
            password="fake-token",  # nosec B106 — test fixture — fake credential
        )
        self.room = Chatroom.objects.create()

    def test_lifecycle_draft_to_active(self):
        persona = create_persona(
            self.user, name="Ops bot", tool_scope=["get_weather"], risk_ceiling="low",
        )
        self.assertEqual(persona.status, "draft")
        confirm_persona(persona)
        self.assertEqual(persona.status, "active")

    def test_duplicate_copies_scope_but_not_history(self):
        persona = create_persona(
            self.user, name="Ops bot", tool_scope=["get_weather"], risk_ceiling="low",
            approval_boundary=["send_email"],
        )
        confirm_persona(persona)
        copy = duplicate_persona(persona, new_name="Ops bot copy")
        self.assertEqual(copy.status, "draft")
        self.assertEqual(copy.created_from_id, persona.id)
        self.assertEqual(copy.tool_scope, persona.tool_scope)
        self.assertEqual(copy.approval_boundary, persona.approval_boundary)

    def test_unique_name_per_user(self):
        create_persona(self.user, name="Ops bot")
        with self.assertRaises(IntegrityError):
            create_persona(self.user, name="Ops bot")

    def test_resolution_requires_owner_and_active(self):
        persona = create_persona(self.user, name="Ops bot", room=self.room)
        confirm_persona(persona)

        self.assertEqual(resolve_room_persona(self.room.id, self.user.id).id, persona.id)
        self.assertIsNone(resolve_room_persona(self.room.id, self.other.id))
        self.assertIsNone(resolve_room_persona(None, self.user.id))

    def test_inactive_persona_is_invisible(self):
        create_persona(self.user, name="Draft bot", room=self.room)
        self.assertIsNone(resolve_room_persona(self.room.id, self.user.id))

    def test_draft_stamps_owner_persona(self):
        from workflows import workflow_agent

        persona = create_persona(self.user, name="Ops bot", room=self.room)
        confirm_persona(persona)
        definition = {
            "workflow_name": "Persona draft",
            "workflow_description": "Draft.",
            "triggers": [{"trigger_type": "manual"}],
            "steps": [],
        }
        async_to_sync(workflow_agent._save_draft)(self.user.id, self.room.id, definition)

        draft = WorkflowDraft.objects.get(user=self.user)
        self.assertEqual(draft.owner_persona_id, persona.id)
