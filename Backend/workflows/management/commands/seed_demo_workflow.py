"""Seed the canonical demo workflow into the database.

Reads `examples/workflows/follow_up_email/workflow.json` and creates a
`UserWorkflow` row owned by the named user. Idempotent — re-running
updates the existing row rather than creating a duplicate.

Used by `scripts/demo.sh` and by anyone running through
`examples/workflows/follow_up_email/README.md` by hand.
"""
from __future__ import annotations

import json
from pathlib import Path

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError

from workflows.models import UserWorkflow


DEMO_NAME = "Follow-up email demo"
DEMO_PATH_FROM_REPO_ROOT = Path("examples") / "workflows" / "follow_up_email" / "workflow.json"


class Command(BaseCommand):
    help = "Seed the canonical follow-up-email demo workflow for v0.4 KAZI_DEMO_MODE."

    def add_arguments(self, parser):
        parser.add_argument(
            "--user",
            required=True,
            help="Username that will own the demo workflow.",
        )
        parser.add_argument(
            "--name",
            default=DEMO_NAME,
            help=f"Workflow name (default: {DEMO_NAME!r}).",
        )

    def handle(self, *args, **options):
        username = options["user"]
        display_name = options["name"]

        User = get_user_model()
        try:
            user = User.objects.get(username=username)
        except User.DoesNotExist:
            raise CommandError(
                f"User {username!r} does not exist. "
                "Create one with `python manage.py createsuperuser` first."
            )

        # Repo root is two levels above Backend/manage.py.
        repo_root = Path(__file__).resolve().parents[4]
        workflow_path = repo_root / DEMO_PATH_FROM_REPO_ROOT
        if not workflow_path.is_file():
            raise CommandError(
                f"Demo workflow definition not found at {workflow_path}. "
                "Are you running from a clean clone of the repo?"
            )

        try:
            definition = json.loads(workflow_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise CommandError(f"Failed to parse {workflow_path}: {exc}")

        description = definition.get("description") or "Kazi v0.4 human-gated runtime demo."

        workflow, created = UserWorkflow.objects.update_or_create(
            user=user,
            name=display_name,
            defaults={
                "description": description,
                "definition": definition,
                "status": "active",
            },
        )

        verb = "Created" if created else "Updated"
        self.stdout.write(self.style.SUCCESS(
            f"{verb} demo workflow id={workflow.id} for user={username}."
        ))
        self.stdout.write("")
        self.stdout.write("Next:")
        self.stdout.write(
            f"  curl -X POST http://localhost:8000/api/workflows/{workflow.id}/run/ "
            "-H 'Content-Type: application/json' -d '{\"trigger_data\": {}}'"
        )
        self.stdout.write(
            "Then watch the execution detail and approve. Full walkthrough: "
            "examples/workflows/follow_up_email/README.md"
        )
