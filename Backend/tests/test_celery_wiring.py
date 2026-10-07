"""Loading the Celery app at start-up must not bring back the manage.py hang."""
import os
import subprocess  # nosec B404 — test infra spawns manage.py with fixed argv
import sys
from pathlib import Path
from unittest import TestCase

BACKEND_DIR = Path(__file__).resolve().parents[1]


class ManagePyStartsWithCeleryLoadedTests(TestCase):
    def test_manage_py_check_returns(self):
        env = dict(os.environ)
        proc = subprocess.run(  # nosec B603 — test infra spawns manage.py with fixed argv
            [sys.executable, str(BACKEND_DIR / "manage.py"), "check"],
            env=env, capture_output=True, text=True, cwd=str(BACKEND_DIR), timeout=180,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr[-2000:])

    def test_a_plain_django_process_binds_tasks_to_the_project_app(self):
        env = dict(os.environ)
        proc = subprocess.run(  # nosec B603 — test infra spawns manage.py with fixed argv
            [sys.executable, str(BACKEND_DIR / "manage.py"), "shell", "-c",
             "from chatbot.tasks import refresh_room_context_summary as t; print('APP=' + t.app.main)"],
            env=env, capture_output=True, text=True, cwd=str(BACKEND_DIR), timeout=180,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr[-2000:])
        self.assertIn("APP=Backend", proc.stdout)
