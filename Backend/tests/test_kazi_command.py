"""Tests for the `kazi` launcher (scripts/kazi.py).

The pure tests pin the process plan, command lines and doctor checks. The
platform tests use real throwaway Python children so the Windows half of the
feature (job objects, console groups, msvcrt locks, process identity) is
exercised for real, not mocked above the layer where it can fail.
"""
from __future__ import annotations

import http.client
import importlib.util
import json
import os
import shutil
import signal
import socket
import subprocess  # nosec B404 - real throwaway children in platform tests
import sys
import tempfile
import threading
import time
import unittest
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
from unittest.mock import patch

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = REPO_ROOT / "scripts"
TMP_ROOT = REPO_ROOT / ".kazi" / "test-tmp"
WINDOWS = sys.platform == "win32"


def _load_kazi():
    spec = importlib.util.spec_from_file_location("kazi_under_test", SCRIPTS / "kazi.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


K = _load_kazi()


def _settings(**profile):
    base = {"lite": False, "in_process_scheduler": False, "debug": True,
            "shell_profile": "standard", "temporal_disabled": True}
    base.update(profile)
    return {
        "profile": base,
        "web": {"host": "127.0.0.1", "port": 8000},
        "shell": {"host": "127.0.0.1", "port": 8765, "token_set": True},  # nosec B105 - boolean flag
        "database": {"host": "", "port": 0},
        "redis": {"host": "127.0.0.1", "port": 6379},
        "temporal": {"host": "localhost", "port": 7233},
        "static": {"collected": True},
        "pool": {"size": 0},
        "pending_migrations": {"items": []},
        "beat_drift": {"missing": [], "extra": [], "changed": []},
    }


def _tmpdir():
    TMP_ROOT.mkdir(parents=True, exist_ok=True)
    return Path(tempfile.mkdtemp(prefix="kazi-test-", dir=str(TMP_ROOT)))


def _wait_until(predicate, timeout=15.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.1)
    return False


def _stop(proc):
    if proc is None:
        return
    if proc.poll() is None:
        proc.kill()
    try:
        proc.wait(timeout=10)
    except subprocess.TimeoutExpired:
        pass


def _port_open(port):
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=0.5):
            return True
    except OSError:
        return False


def _free_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def _kill_leftovers(paths):
    state = K.read_json(paths.state) or {}
    for child in state.get("children") or []:
        pid = child.get("pid")
        if pid and K.pid_alive(pid, "nt" if WINDOWS else "posix"):
            try:
                os.kill(int(pid), signal.SIGTERM)
            except OSError:
                pass


def _supervise(tmp: Path, children):
    plan = tmp / "plan.json"
    state = tmp / "state"
    logs = tmp / "logs"
    K.write_json(plan, {"children": children})
    argv = [
        sys.executable, str(SCRIPTS / "kazi.py"), "__supervise",
        "--plan", str(plan), "--state-dir", str(state), "--log-dir", str(logs),
    ]
    proc = subprocess.Popen(  # nosec B603 - list argv, throwaway children
        argv, cwd=str(REPO_ROOT), stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        **K.supervisor_popen_kwargs("nt" if WINDOWS else "posix"),
    )
    paths = K.RuntimePaths(state)
    paths.logs = logs
    if not _wait_until(lambda: K.read_json(paths.control) is not None, timeout=20):
        _stop(proc)
        raise AssertionError("supervisor did not publish its control file")
    return proc, paths


class ProcessPlanTests(unittest.TestCase):
    def test_full_profile_runs_web_sidecar_worker_and_beat(self):
        self.assertEqual(
            K.process_plan(_settings(), "posix"),
            ["web", "sidecar", "worker", "beat"],
        )

    def test_lite_profile_runs_web_and_sidecar_only(self):
        self.assertEqual(
            K.process_plan(_settings(lite=True, in_process_scheduler=True), "posix"),
            ["web", "sidecar"],
        )

    def test_in_process_scheduler_suppresses_beat_without_dropping_the_worker(self):
        self.assertEqual(
            K.process_plan(_settings(in_process_scheduler=True), "posix"),
            ["web", "sidecar", "worker"],
        )

    def test_child_arguments_are_list_form_and_use_the_right_pool(self):
        windows = {spec.name: spec for spec in K.build_child_specs(_settings(), "nt")}
        posix = {spec.name: spec for spec in K.build_child_specs(_settings(), "posix")}
        for specs in (windows.values(), posix.values()):
            for spec in specs:
                self.assertIsInstance(spec.argv, list)
                self.assertTrue(all(isinstance(part, str) for part in spec.argv))
                self.assertEqual(spec.cwd, str(REPO_ROOT / "Backend"))
        self.assertIn("--pool=solo", windows["worker"].argv)
        self.assertIn("--pool=prefork", posix["worker"].argv)
        self.assertTrue(any(part.startswith("--hostname=kazi-") for part in windows["worker"].argv))
        self.assertIn("--timeout-graceful-shutdown", windows["web"].argv)
        self.assertIn("20", windows["web"].argv)


class CreationFlagTests(unittest.TestCase):
    def test_windows_children_get_a_new_process_group_without_shell(self):
        kwargs = K.child_popen_kwargs("nt")
        self.assertEqual(kwargs["creationflags"], K.CREATE_NEW_PROCESS_GROUP)
        self.assertFalse(kwargs["shell"])

    def test_windows_supervisor_is_hidden_but_not_detached(self):
        kwargs = K.supervisor_popen_kwargs("nt")
        self.assertEqual(kwargs["creationflags"], K.CREATE_NO_WINDOW)
        self.assertFalse(kwargs["creationflags"] & 0x00000008)
        self.assertFalse(kwargs["shell"])

    def test_posix_starts_new_sessions_without_shell(self):
        self.assertTrue(K.child_popen_kwargs("posix")["start_new_session"])
        self.assertTrue(K.supervisor_popen_kwargs("posix")["start_new_session"])
        self.assertFalse(K.supervisor_popen_kwargs("posix")["shell"])


class RestartPolicyTests(unittest.TestCase):
    def test_stop_requested_wins_over_every_exit_code(self):
        for code in (0, 1, K.CLEAN_WINDOWS_EXIT, -signal.SIGTERM):
            restart, _crashes = K.should_restart(True, code, [], 100.0, "nt")
            self.assertFalse(restart)

    def test_clean_windows_control_break_exit_is_not_a_crash(self):
        restart, crashes = K.should_restart(False, K.CLEAN_WINDOWS_EXIT, [], 100.0, "nt")
        self.assertTrue(restart)
        self.assertEqual(crashes, [])

    def test_a_crash_is_counted_and_gives_up_after_five_in_ten_minutes(self):
        crashes = []
        for index in range(4):
            restart, crashes = K.should_restart(False, 1, crashes, 100.0 + index, "nt")
            self.assertTrue(restart)
        restart, crashes = K.should_restart(False, 1, crashes, 105.0, "nt")
        self.assertFalse(restart)
        self.assertEqual(len(crashes), 5)

    def test_backoff_doubles_and_caps(self):
        self.assertEqual([K.restart_delay(n) for n in (1, 2, 3)], [1, 2, 4])
        self.assertEqual(K.restart_delay(20), K.RESTART_MAX_SECONDS)


class IdentityTests(unittest.TestCase):
    def test_dead_pid_has_no_identity(self):
        self.assertIsNone(K.process_creation_time(99999999, "nt" if WINDOWS else "posix"))

    def test_a_wrong_creation_time_never_matches(self):
        def reader(_pid, _system):
            return "111"

        self.assertTrue(K.process_matches(10, "111", "posix", reader=reader))
        self.assertFalse(K.process_matches(10, "222", "posix", reader=reader))
        self.assertFalse(K.process_matches(10, None, "posix", reader=reader))

    def test_a_missing_pid_never_matches(self):
        self.assertFalse(K.process_matches(None, "111", "posix", reader=lambda p, s: "111"))


class DoctorTests(unittest.TestCase):
    def test_python_version(self):
        self.assertEqual(K.check_python((3, 12)).level, "OK")
        self.assertEqual(K.check_python((3, 13)).level, "FAIL")

    def test_virtual_environment(self):
        self.assertEqual(K.check_venv("/repo/.venv", "/usr").level, "OK")
        self.assertEqual(K.check_venv("/usr", "/usr").level, "FAIL")

    def test_required_settings_report_names_not_values(self):
        check = K.check_required_keys({}, {})
        self.assertEqual(check.level, "FAIL")
        self.assertIn("DJANGO_SECRET_KEY", check.message)
        self.assertNotIn("secret-value", check.message)

    def test_env_overrides_are_named_not_printed(self):
        check = K.check_process_overrides(
            {"ANTHROPIC_API_KEY": "process-secret"},
            {"ANTHROPIC_API_KEY": "dotenv-secret"},
        )
        self.assertEqual(check.level, "WARN")
        self.assertIn("ANTHROPIC_API_KEY", check.message)
        self.assertNotIn("secret", check.message)

    def test_temporal_disabled_and_reachable(self):
        self.assertEqual(K.check_temporal(True, "localhost", 7233, lambda h, p: False).level, "OK")
        self.assertEqual(K.check_temporal(False, "localhost", 7233, lambda h, p: True).level, "OK")
        self.assertEqual(K.check_temporal(False, "localhost", 7233, lambda h, p: False).level, "WARN")

    def test_service_reachability(self):
        self.assertEqual(K.check_tcp("Redis", "localhost", 6379, lambda h, p: True).level, "OK")
        self.assertEqual(K.check_tcp("Redis", "localhost", 6379, lambda h, p: False).level, "FAIL")
        self.assertEqual(K.check_tcp("Database", "", 0, lambda h, p: False).level, "OK")

    def test_migrations_and_beat_drift(self):
        self.assertEqual(K.check_migrations([]).level, "OK")
        self.assertEqual(K.check_migrations(["a.b"]).level, "WARN")
        self.assertEqual(K.check_beat_drift({"missing": [], "extra": [], "changed": []}).level, "OK")
        self.assertEqual(K.check_beat_drift({"missing": ["x"], "extra": [], "changed": []}).level, "WARN")

    def test_shell_token_and_profile(self):
        self.assertEqual(K.check_shell_token(True).level, "OK")
        self.assertEqual(K.check_shell_token(False).level, "WARN")
        open_check = K.check_shell_profile("open")
        self.assertEqual(open_check.level, "WARN")
        self.assertIn("with great power comes great responsibility", open_check.message)

    def test_ports(self):
        def binder(host, port):
            return port != 8000

        checks = K.check_ports([("Web", "127.0.0.1", 8000), ("Sidecar", "127.0.0.1", 8765)], binder)
        self.assertEqual([check.level for check in checks], ["FAIL", "OK"])

    def test_static_files_and_worker_counts(self):
        self.assertEqual(K.check_static(True, False).level, "OK")
        self.assertEqual(K.check_static(False, False).level, "FAIL")
        self.assertEqual(K.check_worker_counts({}, False).level, "OK")
        self.assertEqual(K.check_worker_counts({"WEB_CONCURRENCY": "2"}, True).level, "FAIL")
        self.assertEqual(K.check_worker_counts({"WEB_CONCURRENCY": "x"}, False).level, "FAIL")

    def test_connection_pool_against_a_server_limit(self):
        self.assertEqual(K.check_pool(0, 4).level, "OK")
        self.assertEqual(K.check_pool(10, 4).level, "WARN")

    def test_lockfile_check_uses_the_reader_and_never_prints_versions(self):
        tmp = _tmpdir()
        try:
            (tmp / "requirements.lock").write_text("Django==5.2.17\n", encoding="utf-8")
            check = K.check_lockfile(tmp, lambda name: "5.2.17")
            self.assertEqual(check.level, "OK")
            self.assertNotIn("5.2.17", check.message)
            check = K.check_lockfile(tmp, lambda name: "0.0.1")
            self.assertEqual(check.level, "WARN")
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_env_file_presence(self):
        tmp = _tmpdir()
        try:
            check = K.check_env_file(tmp / ".env", {})
            self.assertEqual(check.level, "WARN")
            (tmp / ".env").write_text("A=1\n", encoding="utf-8")
            self.assertEqual(K.check_env_file(tmp / ".env", {"A": "1"}).level, "OK")
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


class TextTests(unittest.TestCase):
    def test_windows_autostart_runs_up_autostart_from_the_repo(self):
        text = K.windows_autostart_text(Path(r"C:\repo with spaces"))
        self.assertIn(K.AUTOSTART_MARKER, text)
        self.assertIn('cd /d "C:\\repo with spaces"', text)
        self.assertIn("up --autostart", text)
        self.assertNotIn("shell=True", text)

    def test_systemd_unit_runs_the_supervisor_in_the_foreground(self):
        root = Path("/srv/kazi core")
        text = K.systemd_unit_text(root)
        self.assertIn("Type=simple", text)
        self.assertIn("Restart=on-failure", text)
        self.assertIn(f"WorkingDirectory={root}", text)
        self.assertIn(f'"{root / "scripts" / "kazi.py"}"', text)
        self.assertIn(" run", text)
        self.assertNotIn(" up", text)

    def test_disable_removes_only_a_file_kazi_created(self):
        tmp = _tmpdir()
        try:
            target = K.autostart_file("posix", {"HOME": str(tmp)})
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text("someone else's unit\n", encoding="utf-8")
            self.assertEqual(K.autostart_disable("posix", {"HOME": str(tmp)}), 1)
            self.assertTrue(target.exists())
            target.write_text(K.AUTOSTART_MARKER + "\n", encoding="utf-8")
            self.assertEqual(K.autostart_disable("posix", {"HOME": str(tmp)}), 0)
            self.assertFalse(target.exists())
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_log_rotation_keeps_five_files(self):
        tmp = _tmpdir()
        try:
            log = tmp / "web.log"
            for index in range(7):
                log.write_bytes(b"x" * 10)
                K.rotate_log(log, max_bytes=5, keep=5)
            names = sorted(path.name for path in tmp.iterdir())
            self.assertIn("web.log.5", names)
            self.assertNotIn("web.log.6", names)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


class PlanAndSupervisorTests(unittest.TestCase):
    def test_web_force_kill_is_later_than_both_graces(self):
        self.assertEqual(K.web_force_kill_seconds(20), 50)
        self.assertEqual(K.web_force_kill_seconds(0), 10)

    def test_stop_order_is_web_worker_beat_sidecar(self):
        specs = [
            K.ChildSpec("sidecar", ["x"]),
            K.ChildSpec("beat", ["x"]),
            K.ChildSpec("web", ["x"]),
            K.ChildSpec("worker", ["x"]),
        ]
        supervisor = K.Supervisor(specs, K.RuntimePaths(_tmpdir()), "posix")
        self.assertEqual(supervisor.stop_names(), ["web", "worker", "beat", "sidecar"])

    def test_the_plan_carries_the_shutdown_grace_and_working_folders(self):
        tmp = _tmpdir()
        try:
            paths = K.RuntimePaths(tmp)
            specs = K.build_child_specs(_settings(), "posix")
            K.write_plan(specs, paths, grace_seconds=42)
            payload = K.read_json(paths.plan)
            self.assertEqual(payload["grace_seconds"], 42)
            self.assertTrue(all(child["cwd"] == str(REPO_ROOT / "Backend") for child in payload["children"]))
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_a_failed_spawn_counts_as_a_crash_for_that_child(self):
        tmp = _tmpdir()
        try:
            supervisor = K.Supervisor(
                [K.ChildSpec("web", ["missing"])], K.RuntimePaths(tmp), "posix"
            )
            slot = supervisor.slots[0]
            for index in range(5):
                supervisor._record_spawn_failure(slot, OSError("boom"), 100.0 + index)
            self.assertTrue(slot.given_up)
            self.assertIn("web", supervisor.given_up)
            self.assertIsNone(slot.proc)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_status_names_a_child_waiting_to_restart(self):
        slot = K.ChildSlot(K.ChildSpec("web", ["missing"]))
        state = slot.as_state()
        self.assertEqual(state["state"], "restarting")
        self.assertIsNone(state["pid"])
        slot.given_up = True
        self.assertEqual(slot.as_state()["state"], "given-up")

    def test_a_failed_state_write_does_not_take_the_stack_down(self):
        tmp = _tmpdir()
        try:
            target = tmp / "state.json"
            target.mkdir()
            self.assertFalse(K.write_json(target, {"ok": False}))
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    @unittest.skipIf(WINDOWS, "POSIX file modes")
    def test_state_file_is_owner_only_from_creation(self):
        tmp = _tmpdir()
        try:
            path = tmp / "state.json"
            self.assertTrue(K.write_json(path, {"ok": True}))
            self.assertEqual(os.stat(path).st_mode & 0o777, 0o600)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_readiness_errors_name_the_failed_process_and_log(self):
        tmp = _tmpdir()
        try:
            paths = K.RuntimePaths(tmp)
            errors = K.readiness_errors(False, False, ["web", "worker"], paths)
            self.assertEqual(len(errors), 2)
            self.assertIn(str(paths.log_path("web")), errors[0])
            self.assertIn(str(paths.log_path("worker")), errors[1])
            self.assertEqual(K.readiness_errors(True, True, ["web"], paths), [])
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


class UpExitTests(unittest.TestCase):
    class FakeProcess:
        def poll(self):
            return None

    def _run_up(self, web_ready):
        tmp = _tmpdir()
        try:
            settings = _settings()
            paths = K.RuntimePaths(tmp)
            out = StringIO()
            with patch.object(K, "load_settings", return_value=(settings, None)), \
                    patch.object(K, "_bootstrap", return_value=settings), \
                    patch.object(K, "run_manage", return_value=0), \
                    patch.object(K, "wait_for_control", return_value=True), \
                    patch.object(K, "wait_for_web", return_value=web_ready), \
                    patch.object(K, "wait_for_worker", return_value=True), \
                    patch.object(K.subprocess, "Popen", return_value=self.FakeProcess()), \
                    redirect_stdout(out):
                code = K.cmd_up(paths, os.name, autostart=False, foreground=False)
            return code, out.getvalue()
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_up_exits_zero_and_says_up_only_when_web_and_worker_answer(self):
        code, output = self._run_up(True)
        self.assertEqual(code, 0)
        self.assertIn("Kazi is up", output)

    def test_up_exits_non_zero_and_names_the_failed_log(self):
        code, output = self._run_up(False)
        self.assertEqual(code, 1)
        self.assertNotIn("Kazi is up", output)
        self.assertIn("FAIL web did not answer HTTP", output)
        self.assertIn("web.log", output)

    def test_a_second_up_says_it_is_starting_without_claiming_not_running(self):
        tmp = _tmpdir()
        try:
            settings = _settings()
            paths = K.RuntimePaths(tmp)
            out = StringIO()

            class BusyLock:
                def __init__(self, *args, **kwargs):
                    pass

                def acquire(self):
                    return False

            with patch.object(K, "load_settings", return_value=(settings, None)), \
                    patch.object(K, "SupervisorLock", BusyLock), \
                    redirect_stdout(out):
                code = K.cmd_up(paths, os.name, autostart=False, foreground=False)
            self.assertEqual(code, 0)
            self.assertIn("already starting or running", out.getvalue())
            self.assertNotIn("Kazi is not running.", out.getvalue())
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


class JobFailureTests(unittest.TestCase):
    class FailingJob:
        def assign_current_process(self):
            raise OSError("nested job refused")

    def test_a_job_object_setup_failure_is_fatal_and_explains_why(self):
        tmp = _tmpdir()
        try:
            supervisor = K.Supervisor(
                [], K.RuntimePaths(tmp), "nt", job_factory=self.FailingJob,
            )
            self.assertFalse(supervisor._setup_job())
            self.assertIsNone(supervisor.job)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


class LiveLogTests(unittest.TestCase):
    def test_a_live_child_log_is_rotated_and_can_still_be_written(self):
        tmp = _tmpdir()
        try:
            log = tmp / "web.log"
            handle = open(log, "ab", buffering=0)
            try:
                handle.write(b"x" * 20)
                handle.flush()
                self.assertTrue(K.rotate_live_log(log, max_bytes=5, keep=3))
                self.assertEqual(log.stat().st_size, 0)
                self.assertEqual((tmp / "web.log.1").read_bytes(), b"x" * 20)
                handle.write(b"still-writing")
            finally:
                handle.close()
            self.assertIn(b"still-writing", log.read_bytes())
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


class LockfileMarkerTests(unittest.TestCase):
    def test_environment_markers_that_do_not_apply_are_skipped(self):
        tmp = _tmpdir()
        try:
            (tmp / "requirements.lock").write_text(
                "colorama==0.4.6 ; sys_platform == 'never'\nDjango==5.2.17\n",
                encoding="utf-8",
            )
            seen = []

            def reader(name):
                seen.append(name)
                return "5.2.17"

            check = K.check_lockfile(tmp, reader)
            self.assertEqual(check.level, "OK")
            self.assertEqual(seen, ["Django"])
            self.assertFalse(K.lock_line_applies(K.parse_lock_requirement(
                "colorama==0.4.6 ; sys_platform == 'never'"
            )))
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


class RealSpecImportTests(unittest.TestCase):
    def test_every_real_spec_imports_from_its_real_working_folder(self):
        specs = {spec.name: spec for spec in K.build_child_specs(_settings(), os.name)}
        probes = {
            "web": ("import Backend.asgi; print('ASGI_OK')", "ASGI_OK"),
            "worker": ("import Backend.celery; print('CELERY_OK')", "CELERY_OK"),
            "beat": ("import Backend.celery; print('CELERY_OK')", "CELERY_OK"),
            "sidecar": ("import orchestration.shell_exec; print('SIDECAR_OK')", "SIDECAR_OK"),
        }
        for name, spec in specs.items():
            code, marker = probes[name]
            result = subprocess.run(  # nosec B603 - real import probe
                [sys.executable, "-c", code],
                cwd=spec.cwd,
                env=K.utf8_env(os.environ),
                stdin=subprocess.DEVNULL,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=120,
                check=False,
            )
            self.assertEqual(
                result.returncode, 0,
                f"{name} import failed from {spec.cwd}: {result.stdout}{result.stderr}",
            )
            self.assertIn(marker, result.stdout)


class WebShutdownTests(unittest.TestCase):
    def test_uvicorn_lifespan_runs_before_the_force_kill(self):
        tmp = _tmpdir()
        marker = tmp / "lifespan"
        started = tmp / "request-started"
        port = _free_port()
        app = tmp / "app.py"
        app.write_text(
            """
import asyncio
import os
from pathlib import Path

MARKER = Path(os.environ["KAZI_TEST_MARKER"])
STARTED = Path(os.environ["KAZI_TEST_STARTED"])


async def application(scope, receive, send):
    if scope["type"] == "lifespan":
        while True:
            message = await receive()
            if message["type"] == "lifespan.startup":
                await send({"type": "lifespan.startup.complete"})
            elif message["type"] == "lifespan.shutdown":
                MARKER.write_text("lifespan")
                await send({"type": "lifespan.shutdown.complete"})
                return
    if scope["type"] == "http":
        STARTED.write_text("started")
        await asyncio.sleep(60)
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"ok"})
""",
            encoding="utf-8",
        )
        settings = _settings(turn_shutdown_grace_seconds=2)
        settings["web"]["port"] = port
        web = {spec.name: spec for spec in K.build_child_specs(settings, os.name)}["web"]
        argv = list(web.argv)
        argv[argv.index("Backend.asgi:application")] = "app:application"
        self.assertIn("--timeout-graceful-shutdown", argv)
        proc = None
        paths = None
        thread = None
        old_marker = os.environ.get("KAZI_TEST_MARKER")
        old_started = os.environ.get("KAZI_TEST_STARTED")
        os.environ["KAZI_TEST_MARKER"] = str(marker)
        os.environ["KAZI_TEST_STARTED"] = str(started)
        try:
            proc, paths = _supervise(tmp, [{
                "name": "web", "argv": argv, "cwd": str(tmp),
            }])
            self.assertTrue(_wait_until(lambda: _port_open(port), timeout=30))

            def slow_request():
                connection = http.client.HTTPConnection("127.0.0.1", port, timeout=30)
                try:
                    connection.request("GET", "/slow")
                    connection.getresponse().read()
                except OSError:
                    pass
                finally:
                    connection.close()

            thread = threading.Thread(target=slow_request, daemon=True)
            thread.start()
            self.assertTrue(_wait_until(started.exists, timeout=20))
            response = K.control_request(paths, "/stop", method="POST")
            self.assertEqual(response["status"], 202)
            self.assertTrue(
                _wait_until(marker.exists, timeout=20),
                "lifespan shutdown did not run before the force-kill",
            )
            proc.wait(timeout=20)
            self.assertIsNotNone(proc.poll())
        finally:
            _stop(proc)
            if proc is not None and paths is not None:
                _kill_leftovers(paths)
            if thread is not None:
                thread.join(timeout=2)
            if old_marker is None:
                os.environ.pop("KAZI_TEST_MARKER", None)
            else:
                os.environ["KAZI_TEST_MARKER"] = old_marker
            if old_started is None:
                os.environ.pop("KAZI_TEST_STARTED", None)
            else:
                os.environ["KAZI_TEST_STARTED"] = old_started
            shutil.rmtree(tmp, ignore_errors=True)


@unittest.skipUnless(WINDOWS, "real Windows process semantics")
class WindowsProcessTests(unittest.TestCase):
    def test_a_real_job_object_kills_children_with_the_supervisor(self):
        tmp = _tmpdir()
        pid_file = tmp / "child.pid"
        code = (
            "import os,sys,time; "
            "open(sys.argv[1],'w').write(str(os.getpid())); "
            "time.sleep(300)"
        )
        proc = None
        paths = None
        try:
            proc, paths = _supervise(tmp, [{
                "name": "child", "argv": [sys.executable, "-c", code, str(pid_file)],
                "cwd": str(tmp),
            }])
            self.assertTrue(_wait_until(lambda: pid_file.exists() and pid_file.stat().st_size > 0))
            child_pid = int(pid_file.read_text(encoding="utf-8"))
            self.assertTrue(K.pid_alive(child_pid, "nt"))
            proc.kill()
            proc.wait(timeout=10)
            self.assertTrue(_wait_until(lambda: not K.pid_alive(child_pid, "nt"), timeout=10))
        finally:
            _stop(proc)
            if proc is not None and paths is not None:
                _kill_leftovers(paths)
            shutil.rmtree(tmp, ignore_errors=True)

    def test_a_real_ctrl_break_reaches_a_child_and_it_exits_gracefully(self):
        tmp = _tmpdir()
        ready = tmp / "ready"
        done = tmp / "done"
        code = (
            "import os,signal,sys,time\n"
            "def stop(signum, frame):\n"
            "    open(sys.argv[2],'w').write('graceful')\n"
            "    sys.exit(0)\n"
            "signal.signal(signal.SIGBREAK, stop)\n"
            "open(sys.argv[1],'w').write(str(os.getpid()))\n"
            "while True:\n"
            "    time.sleep(0.02)\n"
        )
        proc = None
        paths = None
        try:
            proc, paths = _supervise(tmp, [{
                "name": "web", "argv": [sys.executable, "-c", code, str(ready), str(done)],
                "cwd": str(tmp),
            }])
            self.assertTrue(_wait_until(lambda: ready.exists() and ready.stat().st_size > 0))
            response = K.control_request(paths, "/stop", method="POST")
            self.assertEqual(response["status"], 202)
            self.assertTrue(_wait_until(lambda: done.exists() and done.stat().st_size > 0, timeout=10))
            self.assertEqual(done.read_text(encoding="utf-8"), "graceful")
            proc.wait(timeout=20)
        finally:
            _stop(proc)
            if proc is not None and paths is not None:
                _kill_leftovers(paths)
            shutil.rmtree(tmp, ignore_errors=True)

    def test_a_real_msvcrt_lock_is_exclusive_and_released_on_death(self):
        tmp = _tmpdir()
        lock_path = tmp / "supervisor.lock"
        ready = tmp / "ready"
        holder = None
        try:
            argv = [
                sys.executable, str(SCRIPTS / "kazi.py"), "__lock-holder",
                "--path", str(lock_path), "--ready", str(ready), "--seconds", "60",
            ]
            holder = subprocess.Popen(  # nosec B603 - throwaway lock holder
                argv, cwd=str(REPO_ROOT), stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            )
            self.assertTrue(_wait_until(lambda: ready.exists() and ready.stat().st_size > 0, timeout=10))
            self.assertFalse(self._try_lock(lock_path))
            holder.kill()
            holder.wait(timeout=10)
            self.assertTrue(_wait_until(lambda: self._try_lock(lock_path)))
        finally:
            _stop(holder)
            shutil.rmtree(tmp, ignore_errors=True)

    @staticmethod
    def _try_lock(path):
        lock = K.SupervisorLock(path, "nt")
        acquired = lock.acquire()
        lock.release()
        return acquired

    def test_real_get_process_times_identity_rejects_dead_and_reused_pids(self):
        child = subprocess.Popen(  # nosec B603 - throwaway identity child
            [sys.executable, "-c", "import time; time.sleep(30)"],
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        try:
            self.assertTrue(_wait_until(lambda: K.process_creation_time(child.pid, "nt") is not None))
            creation = K.process_creation_time(child.pid, "nt")
            self.assertTrue(K.process_matches(child.pid, creation, "nt"))
            self.assertFalse(K.process_matches(child.pid, "0", "nt"))
            self.assertIsNone(K.process_creation_time(99999999, "nt"))
        finally:
            _stop(child)
            self.assertTrue(_wait_until(lambda: K.process_creation_time(child.pid, "nt") is None))

    def test_the_real_control_channel_refuses_a_wrong_token(self):
        server = K.ControlServer(lambda: {"ok": True}, lambda: None, token="right-token")  # nosec B106 - fake token
        port = server.start()
        try:
            self.assertEqual(self._request(port, None), 403)
            self.assertEqual(self._request(port, "wrong-token"), 403)
            self.assertEqual(self._request(port, "right-token"), 200)
        finally:
            server.stop()

    @staticmethod
    def _request(port, token):
        connection = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
        try:
            headers = {"X-Kazi-Token": token} if token is not None else {}
            connection.request("GET", "/status", headers=headers)
            return connection.getresponse().status
        finally:
            connection.close()

    def test_list_form_spawn_keeps_spaces_and_quotes_whole(self):
        tmp = _tmpdir()
        out = tmp / "argv.json"
        code = "import json,sys; open(sys.argv[1],'w').write(json.dumps(sys.argv[2:]))"
        arguments = ["a b", 'he said "hi"', "trailing space ", ""]
        proc = None
        paths = None
        try:
            proc, paths = _supervise(tmp, [{
                "name": "child",
                "argv": [sys.executable, "-c", code, str(out), *arguments],
                "cwd": str(tmp),
            }])
            self.assertTrue(_wait_until(lambda: out.exists() and out.stat().st_size > 0))
            self.assertEqual(json.loads(out.read_text(encoding="utf-8")), arguments)
        finally:
            _stop(proc)
            if proc is not None and paths is not None:
                _kill_leftovers(paths)
            shutil.rmtree(tmp, ignore_errors=True)


@unittest.skipUnless(sys.platform != "win32", "POSIX process groups")
class PosixProcessTests(unittest.TestCase):
    def test_a_fake_supervisor_starts_children_and_stops_them_through_control(self):
        tmp = _tmpdir()
        pid_file = tmp / "child.pid"
        code = (
            "import os,sys,time; "
            "open(sys.argv[1],'w').write(str(os.getpid())); "
            "time.sleep(300)"
        )
        proc = None
        paths = None
        try:
            proc, paths = _supervise(tmp, [{
                "name": "child", "argv": [sys.executable, "-c", code, str(pid_file)],
                "cwd": str(tmp),
            }])
            self.assertTrue(_wait_until(lambda: pid_file.exists() and pid_file.stat().st_size > 0))
            child_pid = int(pid_file.read_text(encoding="utf-8"))
            self.assertTrue(K.pid_alive(child_pid, "posix"))
            response = K.control_request(paths, "/stop", method="POST")
            self.assertEqual(response["status"], 202)
            proc.wait(timeout=20)
            self.assertTrue(_wait_until(lambda: not K.pid_alive(child_pid, "posix"), timeout=10))
        finally:
            _stop(proc)
            if proc is not None and paths is not None:
                _kill_leftovers(paths)
            shutil.rmtree(tmp, ignore_errors=True)
