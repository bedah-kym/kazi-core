"""Regression tests for settings env handling (Phase 4 e1/e2).

- CELERY_RESULT_BACKEND was hardcoded to 'django-db', silently ignoring the
  env var CI and deploy platforms set.
- DEBUG fallback generated a fresh SECRET_KEY per process and printed it to
  stdout; now it persists to a gitignored file and is never logged.
"""
import io
import os
import subprocess
import sys
import tempfile
from contextlib import redirect_stdout
from pathlib import Path
from unittest import TestCase, skipIf

from django.conf import settings

BACKEND_DIR = Path(__file__).resolve().parents[1]
REPO_ROOT = BACKEND_DIR.parent
HAS_DOTENV = (REPO_ROOT / ".env").exists()


class ResultBackendEnvTests(TestCase):
    def test_result_backend_follows_env_precedence(self):
        expected = os.environ.get("CELERY_RESULT_BACKEND", "django-db")
        self.assertEqual(settings.CELERY_RESULT_BACKEND, expected)

    def test_result_backend_honors_explicit_env(self):
        env = {k: v for k, v in os.environ.items() if not k.startswith("CELERY_")}
        env["CELERY_RESULT_BACKEND"] = "redis://fake-host:6379/2"
        proc = subprocess.run(
            [sys.executable, str(BACKEND_DIR / "manage.py"), "shell", "-c",
             "from django.conf import settings; print('RB=' + settings.CELERY_RESULT_BACKEND)"],
            env=env, capture_output=True, text=True, cwd=str(BACKEND_DIR), timeout=300,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("RB=redis://fake-host:6379/2", proc.stdout)


@skipIf(HAS_DOTENV, "repo .env provides DJANGO_SECRET_KEY; no-key scenarios need a bare env")
class ProductionSecretRequiredTests(TestCase):
    def test_prod_boot_without_secret_fails_loudly(self):
        env = {
            k: v for k, v in os.environ.items()
            if not k.startswith(("DJANGO_", "CELERY_"))
        }
        env.pop("DJANGO_SECRET_KEY", None)
        proc = subprocess.run(
            [sys.executable, str(BACKEND_DIR / "manage.py"), "check"],
            env=env, capture_output=True, text=True, cwd=str(BACKEND_DIR), timeout=300,
        )
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("CRITICAL SECURITY ERROR", proc.stdout + proc.stderr)


class DevSecretKeyFallbackTests(TestCase):
    def _call(self, key_file):
        from Backend.settings import _load_or_create_dev_secret_key
        return _load_or_create_dev_secret_key(key_file)

    def test_generates_persists_and_reuses_stable_key(self):
        with tempfile.TemporaryDirectory() as tmp:
            key_file = Path(tmp) / "k"
            key1, persisted1 = self._call(key_file)
            self.assertTrue(key1)
            self.assertTrue(persisted1)
            self.assertTrue(key_file.exists())
            self.assertEqual(key_file.read_text().strip(), key1)
            key2, persisted2 = self._call(key_file)
            self.assertEqual(key1, key2)
            self.assertTrue(persisted2)

    def test_unwritable_path_yields_key_with_warning_note(self):
        with tempfile.TemporaryDirectory() as tmp:
            missing_dir = Path(tmp) / "nope" / "k"
            key, persisted = self._call(missing_dir)
            self.assertTrue(key)
            self.assertFalse(persisted)

    def test_function_never_writes_key_to_stdout(self):
        with tempfile.TemporaryDirectory() as tmp:
            buffer = io.StringIO()
            with redirect_stdout(buffer):
                key, _note = self._call(Path(tmp) / "k")
            self.assertNotIn(key, buffer.getvalue())
