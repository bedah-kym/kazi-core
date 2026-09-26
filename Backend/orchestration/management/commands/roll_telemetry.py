"""Manual driver for the nightly telemetry rollup (issue #153).

Run ``python Backend/manage.py roll_telemetry`` to roll immediately, or
``--reset-bookmarks`` to reprocess every rotated file from scratch
(backfill). The Celery beat task ``orchestration.tasks.roll_telemetry``
runs the same code on schedule.
"""
from django.core.management.base import BaseCommand

from orchestration.telemetry_rollups import reset_bookmarks, reset_window_marker, run_rollup


class Command(BaseCommand):
    help = "Roll orchestration JSONL telemetry into entity facts and derived watches."

    def add_arguments(self, parser):
        parser.add_argument(
            "--reset-bookmarks",
            action="store_true",
            help="Clear per-file offset bookmarks and reprocess all rotated files.",
        )

    def handle(self, *args, **options):
        if options.get("reset_bookmarks"):
            cleared = reset_bookmarks()
            reset_window_marker()
            self.stdout.write(f"Cleared {cleared} bookmark(s); window marker reset.")
        summary = run_rollup(reset_bookmarks=bool(options.get("reset_bookmarks")))
        if summary.get("noop"):
            self.stdout.write(
                f"[noop] Window {summary.get('day')} already processed. "
                "Use --reset-bookmarks to backfill."
            )
            return
        self.stdout.write(
            "Rollup complete: day={day} rotated={rotated} files={files_processed} "
            "lines={lines_processed} facts={facts} watches={watches}".format(
                day=summary.get("day"),
                rotated=summary.get("rotated"),
                files_processed=summary.get("files_processed"),
                lines_processed=summary.get("lines_processed"),
                facts=summary.get("facts"),
                watches=summary.get("watches"),
            )
        )
