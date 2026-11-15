"""Loading the Celery app at start-up must not bring back the manage.py hang."""
import os
import subprocess  # nosec B404 — test infra spawns manage.py with fixed argv
import sys
from pathlib import Path
from unittest import TestCase

BACKEND_DIR = Path(__file__).resolve().parents[1]

# One manage.py start-up covers both checks: the `check` command still has to
# return, and a plain Django process still has to bind celery tasks to the
# project app. The two probe the same start-up under the same env.
_PROBE = (
    "from django.core.management import call_command\n"
    "call_command('check')\n"
    "print('CHECK_OK')\n"
    "from chatbot.tasks import refresh_room_context_summary as t\n"
    "print('APP=' + t.app.main)\n"
)


class ManagePyStartsWithCeleryLoadedTests(TestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.proc = subprocess.run(  # nosec B603 — test infra spawns manage.py with fixed argv
            [sys.executable, str(BACKEND_DIR / "manage.py"), "shell", "-c", _PROBE],
            env=dict(os.environ), capture_output=True, text=True,
            cwd=str(BACKEND_DIR), timeout=180,
        )

    def test_manage_py_check_returns(self):
        self.assertEqual(self.proc.returncode, 0, self.proc.stderr[-2000:])
        self.assertIn("CHECK_OK", self.proc.stdout)

    def test_a_plain_django_process_binds_tasks_to_the_project_app(self):
        self.assertEqual(self.proc.returncode, 0, self.proc.stderr[-2000:])
        self.assertIn("APP=Backend", self.proc.stdout)
