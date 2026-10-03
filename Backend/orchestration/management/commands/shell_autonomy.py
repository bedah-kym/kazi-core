"""Manage the shell autopilot window for an unsandboxed (`open`) room."""
from __future__ import annotations

from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = "Arm/disarm/inspect the shell autopilot window for a user and room."

    def add_arguments(self, parser):
        sub = parser.add_subparsers(dest="action", required=True)
        for name in ("arm", "disarm", "status"):
            p = sub.add_parser(name)
            p.add_argument("user_id", type=int)
            p.add_argument("room_id", type=int)
            if name == "arm":
                p.add_argument("--minutes", type=int, default=None)

    def handle(self, *args, **options):
        from orchestration.shell import autopilot

        action = options["action"]
        user_id = options["user_id"]
        room_id = options["room_id"]

        if action == "arm":
            ok = autopilot.arm(user_id, room_id, minutes=options.get("minutes"), armed_by=user_id)
            if ok:
                self.stdout.write(self.style.SUCCESS(f"autopilot armed for user {user_id} room {room_id}"))
            else:
                self.stdout.write(self.style.ERROR(
                    "not armed: the user must be a member of the room and the room must use the open profile"
                ))
        elif action == "disarm":
            self.stdout.write(f"autopilot disarmed={autopilot.disarm(user_id, room_id, reason='cli')}")
        elif action == "status":
            self.stdout.write(str({
                "armed": autopilot.is_armed(user_id, room_id),
                "status": autopilot.status(user_id, room_id),
            }))
