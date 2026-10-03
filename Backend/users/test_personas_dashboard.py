"""Tests for the persona dashboard pages and identity injection (Workstream B)."""
from __future__ import annotations

import os
import tempfile

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse

from chatbot.models import Chatroom, Member
from orchestration.personas import confirm_persona, create_persona, persona_identity_prompt

User = get_user_model()


class PersonaIdentityPromptTests(TestCase):
    def test_identity_prompt_includes_name_scope_and_rules(self):
        user = User.objects.create_user(
            username="identity-owner", email="example@example.com",
            password="fake-token",  # nosec B106 — test fixture — fake credential
        )
        persona = create_persona(
            user, name="Night Hawk",
            description="Overnight custodian.",
            tool_scope=["run_command"],
            risk_ceiling="low",
            approval_boundary=["send_email"],
        )
        prompt = persona_identity_prompt(persona)

        self.assertIn('operating as the named persona "Night Hawk"', prompt)
        self.assertIn("Overnight custodian.", prompt)
        self.assertIn("run_command", prompt)
        self.assertIn("send_email", prompt)

    def test_identity_prompt_loads_assigned_skills(self):
        with tempfile.TemporaryDirectory() as tmp:
            skill_dir = os.path.join(tmp, "identity-skill")
            os.makedirs(skill_dir)
            with open(os.path.join(skill_dir, "SKILL.md"), "w", encoding="utf-8") as handle:
                handle.write(
                    "---\nname: identity-skill\ndescription: d\ntools: []\nstage: active\n---\n"
                    "Step one: be careful.\n"
                )
            user = User.objects.create_user(
                username="skill-owner", email="example@example.com",
                password="fake-token",  # nosec B106 — test fixture — fake credential
            )
            persona = create_persona(user, name="Skillful", skills=["identity-skill"])
            with override_settings(SKILLS_DIR=tmp):
                prompt = persona_identity_prompt(persona)
            self.assertIn("Step one: be careful.", prompt)


class PersonaDashboardTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="dash-owner", email="example@example.com",
            password="fake-token",  # nosec B106 — test fixture — fake credential
            is_staff=True,
        )
        self.client.force_login(self.user)
        self.room = Chatroom.objects.create()
        member, _ = Member.objects.get_or_create(User=self.user)
        self.room.participants.add(member)

    def test_non_staff_cannot_manage_skills(self):
        regular = User.objects.create_user(
            username="regular-user", email="regular@example.com",
            password="fake-token",  # nosec B106 — test fixture — fake credential
        )
        self.client.force_login(regular)
        response = self.client.get(reverse('users:skills'))
        self.assertEqual(response.status_code, 403)

    def test_pages_load(self):
        self.assertEqual(self.client.get(reverse('users:personas')).status_code, 200)
        self.assertEqual(self.client.get(reverse('users:skills')).status_code, 200)
        persona = create_persona(self.user, name="Editable")
        self.assertEqual(
            self.client.get(reverse('users:persona_edit', args=[persona.id])).status_code,
            200,
        )

    def test_edit_persona_updates_fields(self):
        persona = create_persona(self.user, name="Editable")
        response = self.client.post(
            reverse('users:persona_edit', args=[persona.id]),
            {
                'name': "Renamed Hawk",
                'description': "Now with rules.",
                'tool_scope': "run_command, get_weather",
                'risk_ceiling': "low",
                'approval_boundary': "send_email",
                'skills': [],
            },
        )
        self.assertEqual(response.status_code, 302)
        persona.refresh_from_db()
        self.assertEqual(persona.name, "Renamed Hawk")
        self.assertEqual(persona.tool_scope, ["run_command", "get_weather"])
        self.assertEqual(persona.risk_ceiling, "low")
        self.assertEqual(persona.approval_boundary, ["send_email"])

    def test_bind_persona_to_room(self):
        persona = create_persona(self.user, name="Binder")
        response = self.client.post(
            reverse('users:personas'),
            {'action': 'bind_room', 'persona_id': persona.id, 'room_id': self.room.id},
        )
        self.assertEqual(response.status_code, 302)
        persona.refresh_from_db()
        self.assertEqual(persona.room_id, self.room.id)

        # Rebinding another persona to the same room releases the first.
        other = create_persona(self.user, name="Otro")
        self.client.post(
            reverse('users:personas'),
            {'action': 'bind_room', 'persona_id': other.id, 'room_id': self.room.id},
        )
        persona.refresh_from_db()
        other.refresh_from_db()
        self.assertIsNone(persona.room_id)
        self.assertEqual(other.room_id, self.room.id)

    def test_room_list_shows_persona_name_and_avatar(self):
        persona = confirm_persona(create_persona(
            self.user, name="Night Hawk", room=self.room,
        ))
        response = self.client.get(
            reverse('chatbot:bot-home', kwargs={'room_name': self.room.id})
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Night Hawk")
        self.assertIsNotNone(persona.id)

    @override_settings(SKILLS_DIR=None)
    def test_create_and_promote_skill_from_dashboard(self):
        with tempfile.TemporaryDirectory() as tmp:
            with override_settings(SKILLS_DIR=tmp):
                response = self.client.post(
                    reverse('users:skills'),
                    {
                        'action': 'create',
                        'name': "Dash Skill",
                        'description': "Made in the dashboard.",
                        'tools': "run_command",
                        'when_to_use': "At night",
                        'inputs': "shell",
                        'sequence': "1. do it",
                        'validation': "echo done",
                        'returns': "summary",
                        'approval': "deletes",
                    },
                )
                self.assertEqual(response.status_code, 302)
                skill_md = os.path.join(tmp, "dash-skill", "SKILL.md")
                self.assertTrue(os.path.isfile(skill_md))
                with open(skill_md, encoding="utf-8") as handle:
                    body = handle.read()
                self.assertIn("stage: staging", body)

                self.client.post(
                    reverse('users:skills'),
                    {'action': 'transition', 'skill': "dash-skill", 'target': "review"},
                )
                self.client.post(
                    reverse('users:skills'),
                    {'action': 'transition', 'skill': "dash-skill", 'target': "active"},
                )
                with open(skill_md, encoding="utf-8") as handle:
                    self.assertIn("stage: active", handle.read())

                self.client.post(
                    reverse('users:skills'),
                    {'action': 'pin', 'skill': "dash-skill", 'pinned': "1"},
                )
                with open(skill_md, encoding="utf-8") as handle:
                    self.assertIn("pinned: true", handle.read())
