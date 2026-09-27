"""Restore a room workspace from a shell snapshot."""
from __future__ import annotations

from django.core.management.base import BaseCommand, CommandError


class Command(BaseCommand):
    help = "Restore a room workspace from a shell snapshot (see run_shell_exec)."

    def add_arguments(self, parser):
        parser.add_argument("--room", required=True, help="Room id whose workspace to restore.")
        parser.add_argument("--snapshot", required=True, help="Snapshot id (filename) to restore.")

    def handle(self, *args, **options):
        from orchestration.shell_exec.backends import ShellExecConfig, restore_snapshot

        config = ShellExecConfig.from_settings()
        try:
            restore_snapshot(config, str(options["room"]), str(options["snapshot"]))
        except ValueError as exc:
            raise CommandError(str(exc))
        self.stdout.write(
            f"Restored snapshot {options['snapshot']} into room {options['room']}."
        )
