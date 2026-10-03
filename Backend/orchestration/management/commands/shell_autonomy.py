"""Manage chat shell autonomy: autopilot window + exact-command grants."""
from __future__ import annotations

from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = "Arm/disarm shell autopilot and manage exact-command grants."

    def add_arguments(self, parser):
        sub = parser.add_subparsers(dest="action", required=True)
        for name in ("arm", "disarm", "status"):
            p = sub.add_parser(name)
            p.add_argument("user_id", type=int)
            p.add_argument("room_id", type=int)
            if name == "arm":
                p.add_argument("--minutes", type=int, default=None)
        p = sub.add_parser("grant")
        p.add_argument("user_id", type=int)
        p.add_argument("room_id", type=int)
        p.add_argument("command")
        p.add_argument("--days", type=int, default=None)
        p = sub.add_parser("revoke")
        p.add_argument("user_id", type=int)
        p.add_argument("room_id", type=int)
        p.add_argument("command")
        p = sub.add_parser("list")
        p.add_argument("user_id", type=int)
        p.add_argument("room_id", type=int)

    def handle(self, *args, **options):
        from orchestration.shell import autopilot, grants

        action = options["action"]
        user_id = options["user_id"]
        room_id = options["room_id"]

        if action == "arm":
            ok = autopilot.arm(user_id, room_id, minutes=options.get("minutes"))
            if ok:
                self.stdout.write(self.style.SUCCESS(f"autopilot armed for user {user_id} room {room_id}"))
            else:
                self.stdout.write(self.style.ERROR("no profile for that user"))
        elif action == "disarm":
            self.stdout.write(f"autopilot disarmed={autopilot.disarm(user_id, room_id)}")
        elif action == "status":
            self.stdout.write(str({
                "armed": autopilot.is_armed(user_id, room_id),
                "status": autopilot.status(user_id, room_id),
            }))
        elif action == "grant":
            fp = grants.create_grant(user_id, room_id, options["command"], days=options.get("days"))
            if fp:
                self.stdout.write(self.style.SUCCESS(f"granted: {fp}"))
            else:
                self.stdout.write(self.style.WARNING("command is not grantable (destructive/opaque/missing)"))
        elif action == "revoke":
            self.stdout.write(f"revoked={grants.revoke_grant(user_id, room_id, options['command'])}")
        elif action == "list":
            self.stdout.write(str(grants.list_grants(user_id, room_id)))
