#!/usr/bin/env python3
"""Start, stop, check and explain a local Kazi install.

Run from the repo root:

    python scripts/kazi.py up
    python scripts/kazi.py status
    python scripts/kazi.py down

The launcher never imports Django. Everything Django knows is read through
``manage.py kazi_status --json`` so the process plan comes from the effective
settings, not from one environment variable.
"""
from __future__ import annotations

import argparse
import ctypes
import hashlib
import hmac
import http.client
import http.server
import importlib.metadata
import json
import os
import secrets
import signal
import shutil
import socket
import subprocess  # nosec B404 - list argv only, no shell
import sys
import threading
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Dict, List, Optional, Sequence
from urllib.parse import urlparse

try:
    from dotenv import dotenv_values
except Exception:  # pragma: no cover - dotenv is locked, this is a safety net
    dotenv_values = None

REPO_ROOT = Path(__file__).resolve().parent.parent
BACKEND_DIR = REPO_ROOT / "Backend"
MANAGE_PY = BACKEND_DIR / "manage.py"

CREATE_NEW_PROCESS_GROUP = 0x00000200
CREATE_NO_WINDOW = 0x08000000
CLEAN_WINDOWS_EXIT = 0xC000013A

CRASH_LIMIT = 5
CRASH_WINDOW_SECONDS = 600
RESTART_BASE_SECONDS = 1
RESTART_MAX_SECONDS = 30
STOP_ORDER = ("web", "worker", "beat", "sidecar")
LOG_MAX_BYTES = 10 * 1024 * 1024
LOG_KEEP = 5
LIVE_LOG_CHECK_SECONDS = 2
AUTOSTART_WAIT_SECONDS = 180
WEB_READY_SECONDS = 240
WORKER_READY_SECONDS = 120
WORKER_SHUTDOWN_WAIT_SECONDS = 300
PROGRESS_SECONDS = 5
SUPERVISOR_ID_ENV = "KAZI_SUPERVISOR_ID"

REQUIRED_ENV_KEYS = (
    "DJANGO_SECRET_KEY",
    "DATABASE_URL",
    "REDIS_URL",
    "CELERY_BROKER_URL",
    "CELERY_RESULT_BACKEND",
)
WORKER_COUNT_KEYS = (
    "KAZI_WEB_WORKERS",
    "WEB_CONCURRENCY",
    "GUNICORN_WORKERS",
    "UVICORN_WORKERS",
)
AUTOSTART_MARKER = "managed by kazi"


def system_name(value: Optional[str] = None) -> str:
    return value or os.name


def is_windows(value: Optional[str] = None) -> bool:
    return system_name(value) == "nt"


class RuntimePaths:
    def __init__(self, root: Optional[Path] = None):
        self.root = Path(root) if root else REPO_ROOT / ".kazi"
        self.run = self.root / "run"
        self.logs = self.root / "logs"
        self.control = self.run / "control.json"
        self.state = self.run / "supervisor.json"
        self.lock = self.run / "supervisor.lock"
        self.start_lock = self.run / "up.lock"
        self.plan = self.run / "plan.json"

    def ensure(self) -> None:
        self.run.mkdir(parents=True, exist_ok=True)
        self.logs.mkdir(parents=True, exist_ok=True)

    def log_path(self, name: str) -> Path:
        return self.logs / f"{name}.log"


def read_json(path: Path) -> Optional[dict]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _write_owner_only(path: Path, text: str, mode: int) -> None:
    fd = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, mode)
    with os.fdopen(fd, "w", encoding="utf-8", newline="") as handle:
        handle.write(text)


def write_json(path: Path, payload: dict, mode: int = 0o600) -> bool:
    """Write state without ever taking the supervisor down.

    A denied replace (antivirus, a reader holding the file) is reported and
    returns False; callers keep running.
    """
    tmp = path.with_name(path.name + ".tmp")
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        _write_owner_only(tmp, json.dumps(payload, sort_keys=True), mode)
        os.replace(tmp, path)
        return True
    except OSError as exc:
        try:
            tmp.unlink()
        except OSError:
            pass
        print(f"WARN state write {path.name}: {exc}", file=sys.stderr)
        return False


def remove_file(path: Path) -> None:
    try:
        path.unlink()
    except OSError:
        pass


def load_dotenv_map(root: Path = REPO_ROOT) -> Dict[str, str]:
    if dotenv_values is None:
        return {}
    try:
        raw = dotenv_values(root / ".env")
    except OSError:
        return {}
    return {key: value for key, value in raw.items() if value is not None}


def utf8_env(env: Optional[dict] = None) -> dict:
    out = dict(env or os.environ)
    out["PYTHONIOENCODING"] = "utf-8"
    return out


# --- process identity -------------------------------------------------------

def _windows_kernel32():
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.OpenProcess.restype = ctypes.c_void_p
    kernel32.OpenProcess.argtypes = [ctypes.c_ulong, ctypes.c_int, ctypes.c_ulong]
    kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
    return kernel32


def _windows_creation_time(pid: int) -> Optional[str]:
    from ctypes import wintypes

    kernel32 = _windows_kernel32()
    handle = kernel32.OpenProcess(0x1000, False, int(pid))
    if not handle:
        return None
    try:
        creation = wintypes.FILETIME()
        exited = wintypes.FILETIME()
        kernel = wintypes.FILETIME()
        user = wintypes.FILETIME()
        ok = kernel32.GetProcessTimes(
            handle,
            ctypes.byref(creation),
            ctypes.byref(exited),
            ctypes.byref(kernel),
            ctypes.byref(user),
        )
        if not ok:
            return None
        if exited.dwLowDateTime or exited.dwHighDateTime:
            return None
        return str((creation.dwHighDateTime << 32) | creation.dwLowDateTime)
    finally:
        kernel32.CloseHandle(handle)


def _linux_creation_time(pid: int) -> Optional[str]:
    try:
        text = Path(f"/proc/{int(pid)}/stat").read_text(encoding="utf-8")
    except (OSError, ValueError):
        return None
    try:
        rest = text[text.rindex(")") + 2:].split()
        return rest[19]
    except (ValueError, IndexError):
        return None


def process_creation_time(pid: Optional[int], system: Optional[str] = None) -> Optional[str]:
    if not pid:
        return None
    target = system_name(system)
    try:
        if target == "nt":
            return _windows_creation_time(int(pid))
        if target == "posix":
            return _linux_creation_time(int(pid))
    except (OSError, ValueError):
        return None
    return None


def process_matches(
    pid: Optional[int],
    creation: Optional[str],
    system: Optional[str] = None,
    reader: Optional[Callable[[int, Optional[str]], Optional[str]]] = None,
) -> bool:
    if not pid or not creation:
        return False
    current = reader(pid, system) if reader else process_creation_time(pid, system)
    return current is not None and str(current) == str(creation)


def pid_alive(pid: Optional[int], system: Optional[str] = None) -> bool:
    if not pid:
        return False
    if is_windows(system):
        return process_creation_time(pid, system) is not None
    try:
        os.kill(int(pid), 0)
    except OSError:
        return False
    return True


# --- one supervisor only ----------------------------------------------------

class SupervisorLock:
    def __init__(self, path: Path, system: Optional[str] = None):
        self.path = Path(path)
        self.system = system_name(system)
        self._handle = None
        self._fd: Optional[int] = None

    def acquire(self) -> bool:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(str(self.path), os.O_RDWR | os.O_CREAT, 0o600)
        try:
            if self.system == "nt":
                import msvcrt

                if os.fstat(fd).st_size == 0:
                    os.write(fd, b"0")
                os.lseek(fd, 0, os.SEEK_SET)
                msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.lockf(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            os.close(fd)
            return False
        self._fd = fd
        return True

    def release(self) -> None:
        if self._fd is None:
            return
        try:
            if self.system == "nt":
                import msvcrt

                os.lseek(self._fd, 0, os.SEEK_SET)
                msvcrt.locking(self._fd, msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.lockf(self._fd, fcntl.LOCK_UN)
        except OSError:
            pass
        finally:
            try:
                os.close(self._fd)
            finally:
                self._fd = None


# --- Windows job object -----------------------------------------------------

class WindowsJob:
    def __init__(self):
        self._kernel32, self._handle = self._create()

    @staticmethod
    def _create():
        from ctypes import wintypes

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.CreateJobObjectW.restype = ctypes.c_void_p
        kernel32.CreateJobObjectW.argtypes = [ctypes.c_void_p, ctypes.c_wchar_p]
        kernel32.SetInformationJobObject.argtypes = [
            ctypes.c_void_p, ctypes.c_int, ctypes.c_void_p, ctypes.c_ulong,
        ]
        kernel32.AssignProcessToJobObject.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
        kernel32.TerminateJobObject.argtypes = [ctypes.c_void_p, ctypes.c_uint]
        kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
        kernel32.GetCurrentProcess.restype = ctypes.c_void_p
        kernel32.GetCurrentProcess.argtypes = []

        class BasicLimits(ctypes.Structure):
            _fields_ = [
                ("PerProcessUserTimeLimit", ctypes.c_longlong),
                ("PerJobUserTimeLimit", ctypes.c_longlong),
                ("LimitFlags", wintypes.DWORD),
                ("MinimumWorkingSetSize", ctypes.c_size_t),
                ("MaximumWorkingSetSize", ctypes.c_size_t),
                ("ActiveProcessLimit", wintypes.DWORD),
                ("Affinity", ctypes.c_size_t),
                ("PriorityClass", wintypes.DWORD),
                ("SchedulingClass", wintypes.DWORD),
            ]

        class IoCounters(ctypes.Structure):
            _fields_ = [
                ("ReadOperationCount", ctypes.c_ulonglong),
                ("WriteOperationCount", ctypes.c_ulonglong),
                ("OtherOperationCount", ctypes.c_ulonglong),
                ("ReadTransferCount", ctypes.c_ulonglong),
                ("WriteTransferCount", ctypes.c_ulonglong),
                ("OtherTransferCount", ctypes.c_ulonglong),
            ]

        class ExtendedLimits(ctypes.Structure):
            _fields_ = [
                ("BasicLimitInformation", BasicLimits),
                ("IoInfo", IoCounters),
                ("ProcessMemoryLimit", ctypes.c_size_t),
                ("JobMemoryLimit", ctypes.c_size_t),
                ("PeakProcessMemoryUsed", ctypes.c_size_t),
                ("PeakJobMemoryUsed", ctypes.c_size_t),
            ]

        handle = kernel32.CreateJobObjectW(None, None)
        if not handle:
            raise OSError("CreateJobObject failed")
        info = ExtendedLimits()
        info.BasicLimitInformation.LimitFlags = 0x2000
        if not kernel32.SetInformationJobObject(
            handle, 9, ctypes.byref(info), ctypes.sizeof(info)
        ):
            kernel32.CloseHandle(handle)
            raise OSError("SetInformationJobObject failed")
        return kernel32, handle

    def assign_current_process(self) -> None:
        process = self._kernel32.GetCurrentProcess()
        if not self._kernel32.AssignProcessToJobObject(self._handle, process):
            raise OSError("AssignProcessToJobObject failed")

    def terminate(self) -> None:
        if self._handle:
            self._kernel32.TerminateJobObject(self._handle, 1)

    def close(self) -> None:
        if self._handle:
            self._kernel32.CloseHandle(self._handle)
            self._handle = None


# --- control channel --------------------------------------------------------

def token_matches(expected: Optional[str], supplied: Optional[str]) -> bool:
    if not expected or not supplied:
        return False
    return hmac.compare_digest(str(expected), str(supplied))


class ControlServer:
    def __init__(
        self,
        status_provider: Callable[[], dict],
        stop_callback: Callable[[], None],
        token: Optional[str] = None,
        host: str = "127.0.0.1",
        port: int = 0,
    ):
        if host != "127.0.0.1":
            raise ValueError("The control channel binds to 127.0.0.1 only.")
        self.status_provider = status_provider
        self.stop_callback = stop_callback
        self.token = token or secrets.token_urlsafe(32)
        self.host = host
        self.port = port
        self._httpd: Optional[http.server.ThreadingHTTPServer] = None
        self._thread: Optional[threading.Thread] = None

    def _authorized(self, handler: http.server.BaseHTTPRequestHandler) -> bool:
        return token_matches(self.token, handler.headers.get("X-Kazi-Token"))

    def _reply(self, handler, status: int, payload: dict) -> None:
        body = json.dumps(payload).encode("utf-8")
        handler.send_response(status)
        handler.send_header("Content-Type", "application/json")
        handler.send_header("Content-Length", str(len(body)))
        handler.end_headers()
        handler.wfile.write(body)

    def _handler_class(self):
        outer = self

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_GET(self):  # noqa: N802
                if not outer._authorized(self):
                    outer._reply(self, 403, {"error": "forbidden"})
                    return
                if self.path == "/status":
                    outer._reply(self, 200, outer.status_provider())
                    return
                if self.path == "/ping":
                    outer._reply(self, 200, {"ok": True})
                    return
                outer._reply(self, 404, {"error": "not_found"})

            def do_POST(self):  # noqa: N802
                if not outer._authorized(self):
                    outer._reply(self, 403, {"error": "forbidden"})
                    return
                if self.path == "/stop":
                    outer.stop_callback()
                    outer._reply(self, 202, {"stopping": True})
                    return
                outer._reply(self, 404, {"error": "not_found"})

            def log_message(self, *args, **kwargs):
                return

        return Handler

    def start(self) -> int:
        self._httpd = http.server.ThreadingHTTPServer(
            (self.host, self.port), self._handler_class()
        )
        self._httpd.daemon_threads = True
        self.port = self._httpd.server_address[1]
        self._thread = threading.Thread(target=self._httpd.serve_forever, daemon=True)
        self._thread.start()
        return self.port

    def stop(self) -> None:
        if self._httpd is not None:
            self._httpd.shutdown()
            self._httpd.server_close()
            self._httpd = None
        if self._thread is not None:
            self._thread.join(timeout=5)
            self._thread = None


def control_request(
    paths: RuntimePaths,
    route: str,
    method: str = "GET",
    timeout: float = 3.0,
) -> Optional[dict]:
    control = read_json(paths.control)
    if not control or not control.get("port") or not control.get("token"):
        return None
    connection = http.client.HTTPConnection(
        "127.0.0.1", int(control["port"]), timeout=timeout
    )
    try:
        connection.request(method, route, headers={"X-Kazi-Token": control["token"]})
        response = connection.getresponse()
        raw = response.read()
        body = json.loads(raw.decode("utf-8")) if raw else None
        return {"status": response.status, "body": body}
    except (OSError, ValueError):
        return None
    finally:
        connection.close()


def live_control(paths: RuntimePaths, system: Optional[str] = None) -> Optional[dict]:
    control = read_json(paths.control)
    if not control:
        return None
    if not process_matches(control.get("pid"), control.get("creation"), system):
        return None
    return control


# --- supervisor -------------------------------------------------------------

@dataclass
class ChildSpec:
    name: str
    argv: List[str]
    cwd: Optional[str] = None


@dataclass
class ChildSlot:
    spec: ChildSpec
    proc: Optional[subprocess.Popen] = None
    creation: Optional[str] = None
    restarts: int = 0
    crashes: List[float] = field(default_factory=list)
    next_start: float = 0.0
    given_up: bool = False
    last_exit: Optional[int] = None

    def as_state(self) -> dict:
        if self.proc is not None:
            state = "running"
        elif self.given_up:
            state = "given-up"
        else:
            state = "restarting"
        return {
            "name": self.spec.name,
            "pid": self.proc.pid if self.proc is not None else None,
            "creation": self.creation if self.proc is not None else None,
            "state": state,
            "restarts": self.restarts,
            "given_up": self.given_up,
            "last_exit": self.last_exit,
        }


def normalize_exit_code(code: Optional[int], system: Optional[str] = None) -> Optional[int]:
    if code is None or not is_windows(system):
        return code
    return code & 0xFFFFFFFF


def is_clean_exit(code: Optional[int], system: Optional[str] = None) -> bool:
    normalized = normalize_exit_code(code, system)
    if normalized in (0, CLEAN_WINDOWS_EXIT):
        return True
    if normalized is not None and normalized < 0:
        return -normalized in (signal.SIGTERM, signal.SIGINT)
    return False


def restart_delay(attempt: int, base: int = RESTART_BASE_SECONDS, cap: int = RESTART_MAX_SECONDS) -> int:
    if attempt < 1:
        attempt = 1
    return min(cap, base * (2 ** (attempt - 1)))


def should_restart(
    stop_requested: bool,
    exit_code: Optional[int],
    crashes: Sequence[float],
    now: float,
    system: Optional[str] = None,
    window: int = CRASH_WINDOW_SECONDS,
    limit: int = CRASH_LIMIT,
) -> tuple[bool, List[float]]:
    if stop_requested:
        return False, list(crashes)
    if is_clean_exit(exit_code, system):
        return True, list(crashes)
    recent = [stamp for stamp in crashes if now - stamp <= window]
    recent.append(now)
    if len(recent) >= limit:
        return False, recent
    return True, recent


def child_popen_kwargs(system: Optional[str] = None) -> dict:
    if is_windows(system):
        return {"creationflags": CREATE_NEW_PROCESS_GROUP, "shell": False}
    if system_name(system) == "posix":
        return {"start_new_session": True, "shell": False}
    return {"shell": False}


def supervisor_popen_kwargs(system: Optional[str] = None) -> dict:
    if is_windows(system):
        return {"creationflags": CREATE_NO_WINDOW, "shell": False}
    if system_name(system) == "posix":
        return {"start_new_session": True, "shell": False}
    return {"shell": False}


def _replace_log_file(source: Path, target: Path) -> None:
    try:
        os.replace(source, target)
    except OSError:
        pass


def rotate_log(path: Path, max_bytes: int = LOG_MAX_BYTES, keep: int = LOG_KEEP) -> bool:
    """Rotate a closed log. Never raise: a failed rotation is not fatal."""
    try:
        if path.stat().st_size < max_bytes:
            return False
    except OSError:
        return False
    for index in range(keep - 1, 0, -1):
        older = path.with_name(f"{path.name}.{index}")
        newer = path.with_name(f"{path.name}.{index + 1}")
        if older.exists():
            _replace_log_file(older, newer)
    _replace_log_file(path, path.with_name(f"{path.name}.1"))
    return True


def rotate_live_log(path: Path, max_bytes: int = LOG_MAX_BYTES, keep: int = LOG_KEEP) -> bool:
    """Bound a running child's log without closing its handle.

    The child's append handle keeps writing at the end of the truncated file.
    A copy is kept as .1; failures are ignored because a busy reader or an
    antivirus hold must never stop the stack.
    """
    try:
        if path.stat().st_size < max_bytes:
            return False
    except OSError:
        return False
    for index in range(keep - 1, 0, -1):
        older = path.with_name(f"{path.name}.{index}")
        newer = path.with_name(f"{path.name}.{index + 1}")
        if older.exists():
            _replace_log_file(older, newer)
    try:
        shutil.copyfile(path, path.with_name(f"{path.name}.1"))
        with open(path, "r+b") as handle:
            handle.truncate(0)
        return True
    except OSError:
        return False


def cleanup_posix_survivors(paths: RuntimePaths, system: Optional[str] = None, wait_seconds: float = 5.0) -> int:
    """Stop proven POSIX survivors from a previous supervisor before doctoring."""
    if system_name(system) != "posix":
        return 0
    previous = read_json(paths.state)
    if not previous or not previous.get("supervisor_id"):
        return 0
    marker = f"{SUPERVISOR_ID_ENV}={previous.get('supervisor_id')}".encode("utf-8")
    targets = []
    for child in previous.get("children") or []:
        pid = child.get("pid")
        creation = child.get("creation")
        if not process_matches(pid, creation, "posix"):
            continue
        try:
            raw = Path(f"/proc/{int(pid)}/environ").read_bytes()
        except (OSError, TypeError, ValueError):
            continue
        if marker in raw.split(b"\0"):
            targets.append((int(pid), creation))
    for pid, _creation in targets:
        try:
            os.kill(pid, signal.SIGTERM)
        except OSError:
            pass
    deadline = time.monotonic() + max(0.0, wait_seconds)
    pending = list(targets)
    while pending and time.monotonic() < deadline:
        pending = [
            (pid, creation) for pid, creation in pending
            if process_matches(pid, creation, "posix")
        ]
        if pending:
            time.sleep(0.1)
    for pid, _creation in pending:
        try:
            os.kill(pid, signal.SIGKILL)
        except OSError:
            pass
    remove_file(paths.state)
    return len(targets)


def web_force_kill_seconds(grace: float) -> float:
    """Uvicorn gets one grace for tasks, then the lifespan gets a second."""
    return max(10.0, float(grace) * 2 + 10)


def worker_node_name() -> str:
    install = hashlib.sha256(str(REPO_ROOT).encode("utf-8")).hexdigest()[:10]
    return f"kazi-{install}@{socket.gethostname()}"


def worker_shutdown_wait_seconds() -> int:
    raw = (os.environ.get("KAZI_WORKER_SHUTDOWN_SECONDS") or "").strip()
    try:
        return max(10, int(raw)) if raw else WORKER_SHUTDOWN_WAIT_SECONDS
    except ValueError:
        return WORKER_SHUTDOWN_WAIT_SECONDS


def worker_shutdown_argv(system: Optional[str] = None, timeout: Optional[int] = None) -> List[str]:
    return [
        sys.executable, "-m", "celery", "-A", "Backend",
        "control", "shutdown", "-d", worker_node_name(),
        "--timeout", str(timeout or worker_shutdown_wait_seconds()),
    ]


class Supervisor:
    def __init__(
        self,
        specs: Sequence[ChildSpec],
        paths: RuntimePaths,
        system: Optional[str] = None,
        child_env: Optional[dict] = None,
        grace_seconds: float = 20.0,
        job_factory: Optional[Callable[[], "WindowsJob"]] = None,
    ):
        self.paths = paths
        self.system = system_name(system)
        self.slots = [ChildSlot(spec=spec) for spec in specs]
        self.child_env = dict(child_env or os.environ)
        self.child_env["PYTHONIOENCODING"] = "utf-8"
        self.supervisor_id = secrets.token_hex(16)
        self.child_env[SUPERVISOR_ID_ENV] = self.supervisor_id
        self.grace_seconds = grace_seconds
        self.job_factory = job_factory
        self.stop_requested = threading.Event()
        self.given_up: Dict[str, str] = {}
        self.creation: Optional[str] = None
        self.started_at = time.time()
        self.job: Optional[WindowsJob] = None
        self.last_log_check = 0.0

    def stop_names(self) -> List[str]:
        present = {slot.spec.name for slot in self.slots}
        return [name for name in STOP_ORDER if name in present]

    def state(self) -> dict:
        return {
            "pid": os.getpid(),
            "creation": self.creation,
            "started_at": self.started_at,
            "stop_requested": self.stop_requested.is_set(),
            "children": [slot.as_state() for slot in self.slots],
            "given_up": dict(self.given_up),
        }

    def _request_stop(self) -> None:
        self.stop_requested.set()

    def _install_signals(self) -> None:
        def handler(*_args):
            self._request_stop()

        for name in ("SIGTERM", "SIGINT"):
            sig = getattr(signal, name, None)
            if sig is not None:
                try:
                    signal.signal(sig, handler)
                except (OSError, ValueError):
                    pass

    def _setup_job(self) -> bool:
        if not is_windows(self.system):
            return True
        factory = self.job_factory or WindowsJob
        try:
            self.job = factory()
            self.job.assign_current_process()
        except OSError as exc:
            self.job = None
            print(
                f"FAIL job object: {exc}; refusing to start without survivor cleanup.",
                file=sys.stderr,
                flush=True,
            )
            return False
        return True

    def _spawn(self, slot: ChildSlot) -> None:
        path = self.paths.log_path(slot.spec.name)
        rotate_log(path)
        log = open(path, "ab", buffering=0)
        try:
            proc = subprocess.Popen(  # nosec B603 - list argv, no shell
                slot.spec.argv,
                cwd=slot.spec.cwd or str(REPO_ROOT),
                env=self.child_env,
                stdin=subprocess.DEVNULL,
                stdout=log,
                stderr=subprocess.STDOUT,
                **child_popen_kwargs(self.system),
            )
        finally:
            log.close()
        slot.proc = proc
        slot.creation = process_creation_time(proc.pid, self.system)

    def _posix_owns(self, slot: ChildSlot) -> bool:
        if slot.proc is None or not process_matches(slot.proc.pid, slot.creation, "posix"):
            return False
        try:
            raw = Path(f"/proc/{slot.proc.pid}/environ").read_bytes()
        except OSError:
            return False
        marker = f"{SUPERVISOR_ID_ENV}={self.supervisor_id}".encode("utf-8")
        return marker in raw.split(b"\0")

    def _clean_previous_posix(self) -> None:
        cleanup_posix_survivors(self.paths, self.system)

    def _signal_break(self, slot: ChildSlot) -> None:
        if slot.proc is None or slot.proc.poll() is not None:
            return
        if not process_matches(slot.proc.pid, slot.creation, self.system):
            return
        if is_windows(self.system):
            ctrl_break = getattr(signal, "CTRL_BREAK_EVENT", None)
            if ctrl_break is not None:
                try:
                    slot.proc.send_signal(ctrl_break)
                except OSError:
                    pass
        else:
            try:
                slot.proc.send_signal(signal.SIGTERM)
            except OSError:
                pass

    def _terminate(self, slot: ChildSlot, wait_seconds: float = 5.0) -> None:
        if slot.proc is None or slot.proc.poll() is not None:
            return
        try:
            slot.proc.terminate()
            slot.proc.wait(timeout=max(0.1, wait_seconds))
        except (OSError, subprocess.TimeoutExpired):
            try:
                slot.proc.kill()
                slot.proc.wait(timeout=5)
            except (OSError, subprocess.TimeoutExpired):
                pass

    def _warm_worker_shutdown(self, timeout: int = 5) -> None:
        try:
            subprocess.run(  # nosec B603 - list argv, no shell
                worker_shutdown_argv(self.system, timeout),
                cwd=str(REPO_ROOT / "Backend"),
                env=self.child_env,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=timeout + 10,
                check=False,
                **child_popen_kwargs(self.system),
            )
        except (OSError, subprocess.TimeoutExpired):
            pass

    def stop_all(self) -> None:
        self.stop_requested.set()
        slots = {slot.spec.name: slot for slot in self.slots}
        for name in self.stop_names():
            slot = slots[name]
            if name == "web":
                self._signal_break(slot)
                try:
                    if slot.proc is not None:
                        slot.proc.wait(timeout=web_force_kill_seconds(self.grace_seconds))
                except (OSError, subprocess.TimeoutExpired):
                    self._terminate(slot)
            elif name == "worker":
                self._warm_worker_shutdown()
                try:
                    if slot.proc is not None:
                        slot.proc.wait(timeout=worker_shutdown_wait_seconds())
                except (OSError, subprocess.TimeoutExpired):
                    self._terminate(slot)
            else:
                self._terminate(slot)
        for slot in self.slots:
            self._terminate(slot, wait_seconds=1)

    def _record_spawn_failure(self, slot: ChildSlot, exc: OSError, now: float) -> None:
        slot.last_exit = f"spawn failed: {exc}"
        restart, crashes = should_restart(
            self.stop_requested.is_set(), 1, slot.crashes, now, self.system
        )
        slot.crashes = crashes
        if restart:
            slot.restarts += 1
            slot.next_start = now + restart_delay(slot.restarts)
        elif not self.stop_requested.is_set():
            slot.given_up = True
            self.given_up[slot.spec.name] = (
                f"could not start after {len(crashes)} attempts: {exc}"
            )

    def _try_spawn(self, slot: ChildSlot, now: float) -> None:
        try:
            self._spawn(slot)
        except OSError as exc:
            self._record_spawn_failure(slot, exc, now)

    def _reap_children(self, now: float) -> None:
        for slot in self.slots:
            if slot.proc is not None:
                code = slot.proc.poll()
                if code is None:
                    continue
                slot.last_exit = code
                slot.proc = None
                slot.creation = None
                restart, crashes = should_restart(
                    self.stop_requested.is_set(), code, slot.crashes, now, self.system
                )
                slot.crashes = crashes
                if restart:
                    slot.restarts += 1
                    slot.next_start = now + restart_delay(slot.restarts)
                elif not self.stop_requested.is_set():
                    slot.given_up = True
                    self.given_up[slot.spec.name] = (
                        f"exited {code!r} after {len(crashes)} crashes in "
                        f"{CRASH_WINDOW_SECONDS // 60} minutes"
                    )
            elif not slot.given_up and not self.stop_requested.is_set():
                if now >= slot.next_start:
                    self._try_spawn(slot, now)

    def _rotate_running_logs(self, now: float) -> None:
        if now - self.last_log_check < LIVE_LOG_CHECK_SECONDS:
            return
        self.last_log_check = now
        for slot in self.slots:
            if slot.proc is not None and slot.proc.poll() is None:
                rotate_live_log(self.paths.log_path(slot.spec.name))

    def run(self) -> int:
        self.paths.ensure()
        lock = SupervisorLock(self.paths.lock, self.system)
        if not lock.acquire():
            print("A Kazi supervisor is already running.")
            return 0
        try:
            cleanup_posix_survivors(self.paths, self.system)
            self.creation = process_creation_time(os.getpid(), self.system)
            self._install_signals()
            if not self._setup_job():
                return 1
            now = time.monotonic()
            for slot in self.slots:
                self._try_spawn(slot, now)
            server = ControlServer(self.state, self._request_stop)
            port = server.start()
            write_json(self.paths.control, {
                "port": port,
                "token": server.token,
                "pid": os.getpid(),
                "creation": self.creation,
            })
            write_json(self.paths.state, {**self.state(), "supervisor_id": self.supervisor_id})
            last_state = time.monotonic()
            try:
                while not self.stop_requested.is_set():
                    now = time.monotonic()
                    self._reap_children(now)
                    self._rotate_running_logs(now)
                    if now - last_state >= 1.0:
                        write_json(
                            self.paths.state,
                            {**self.state(), "supervisor_id": self.supervisor_id},
                        )
                        last_state = now
                    time.sleep(0.2)
            finally:
                self.stop_all()
                server.stop()
                remove_file(self.paths.control)
                remove_file(self.paths.state)
        finally:
            lock.release()
        return 0


def build_child_specs(settings: dict, system: Optional[str] = None) -> List[ChildSpec]:
    plan = process_plan(settings, system)
    backend = str(BACKEND_DIR)
    specs: List[ChildSpec] = []
    for name in plan:
        if name == "web":
            host = settings.get("web", {}).get("host", "127.0.0.1")
            port = _as_int(settings.get("web", {}).get("port"), 8000)
            grace = _as_int(
                settings.get("profile", {}).get("turn_shutdown_grace_seconds"), 20
            )
            specs.append(ChildSpec("web", [
                sys.executable, "-m", "uvicorn", "Backend.asgi:application",
                "--host", host, "--port", str(port), "--workers", "1",
                "--timeout-graceful-shutdown", str(grace),
            ], cwd=backend))
        elif name == "sidecar":
            specs.append(ChildSpec(
                "sidecar", [sys.executable, "-m", "orchestration.shell_exec"],
                cwd=backend,
            ))
        elif name == "worker":
            pool = "solo" if is_windows(system) else "prefork"
            specs.append(ChildSpec("worker", [
                sys.executable, "-m", "celery", "-A", "Backend", "worker",
                "--loglevel=info", f"--pool={pool}", f"--hostname={worker_node_name()}",
            ], cwd=backend))
        elif name == "beat":
            specs.append(ChildSpec("beat", [
                sys.executable, "-m", "celery", "-A", "Backend", "beat",
                "--loglevel=info",
            ], cwd=backend))
    return specs


def process_plan(settings: dict, system: Optional[str] = None) -> List[str]:
    plan = ["web", "sidecar"]
    if not settings.get("profile", {}).get("lite", False):
        plan.append("worker")
    if not settings.get("profile", {}).get("in_process_scheduler", False):
        plan.append("beat")
    return plan


# --- settings bridge --------------------------------------------------------

def parse_settings_json(text: str) -> Optional[dict]:
    try:
        return json.loads(text)
    except ValueError:
        start = text.find("{")
        end = text.rfind("}")
        if start == -1 or end <= start:
            return None
        try:
            return json.loads(text[start:end + 1])
        except ValueError:
            return None


def load_settings(paths: RuntimePaths, timeout: float = 120.0) -> tuple[Optional[dict], Optional[str]]:
    try:
        result = subprocess.run(  # nosec B603 - list argv, no shell
            [sys.executable, str(MANAGE_PY), "kazi_status", "--json"],
            cwd=str(REPO_ROOT),
            env=utf8_env(),
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None, "could not run manage.py kazi_status"
    if result.returncode != 0:
        return None, "manage.py kazi_status failed"
    payload = parse_settings_json(result.stdout)
    if payload is None:
        return None, "kazi_status returned unreadable JSON"
    return payload, None


def run_manage(args: Sequence[str], timeout: Optional[float] = None) -> int:
    try:
        result = subprocess.run(  # nosec B603 - list argv, no shell
            [sys.executable, str(MANAGE_PY), *args],
            cwd=str(REPO_ROOT),
            env=utf8_env(),
            check=False,
            timeout=timeout,
        )
    except (OSError, subprocess.TimeoutExpired):
        return 1
    return result.returncode


# --- doctor -----------------------------------------------------------------

@dataclass
class Check:
    name: str
    level: str
    message: str


def check_python(version_info: Sequence[int]) -> Check:
    pair = tuple(version_info[:2])
    if pair in ((3, 11), (3, 12)):
        return Check("Python", "OK", f"Python {pair[0]}.{pair[1]} is supported.")
    return Check(
        "Python", "FAIL",
        f"Python {pair[0]}.{pair[1]} is not supported; use 3.11 or 3.12.",
    )


def check_venv(prefix: str, base_prefix: str) -> Check:
    if prefix != base_prefix:
        return Check("Virtual environment", "OK", "Running inside a virtual environment.")
    return Check(
        "Virtual environment", "FAIL",
        "Not running inside a virtual environment; use the checkout .venv.",
    )


def parse_lock_requirement(line: str):
    """Return a packaging Requirement for one lock line, or None.

    Environment markers stay attached to the requirement so the caller can
    skip a package that does not apply on this OS or Python.
    """
    text = line.strip()
    if not text or text.startswith("#"):
        return None
    try:
        from packaging.requirements import Requirement

        return Requirement(text)
    except Exception:
        return None


def lock_line_applies(requirement) -> bool:
    if requirement is None or requirement.marker is None:
        return True
    try:
        return bool(requirement.marker.evaluate())
    except Exception:
        return True


def check_lockfile(
    root: Path = REPO_ROOT,
    version_reader: Callable[[str], str] = importlib.metadata.version,
) -> Check:
    lock = root / "requirements.lock"
    try:
        lines = lock.read_text(encoding="utf-8").splitlines()
    except OSError:
        return Check("Lock file", "WARN", "requirements.lock is missing; install it before starting.")
    missing = 0
    checked = 0
    for line in lines:
        requirement = parse_lock_requirement(line)
        if requirement is None or not lock_line_applies(requirement):
            continue
        checked += 1
        try:
            installed = version_reader(requirement.name)
        except importlib.metadata.PackageNotFoundError:
            missing += 1
            continue
        if not requirement.specifier.contains(installed, prereleases=True):
            missing += 1
    if missing:
        return Check(
            "Lock file", "WARN",
            f"{missing} of {checked} applicable locked packages are missing or differ; "
            "run pip install -r requirements.lock.",
        )
    return Check("Lock file", "OK", f"All {checked} applicable locked packages are installed.")


def check_required_keys(dotenv: dict, env: dict, required: Sequence[str] = REQUIRED_ENV_KEYS) -> Check:
    missing = [name for name in required if not (dotenv.get(name) or env.get(name))]
    if missing:
        return Check(
            "Required settings", "FAIL",
            "Missing required settings: " + ", ".join(missing) + ".",
        )
    return Check("Required settings", "OK", "Every required setting is present.")


def check_env_file(path: Path, values: Optional[dict] = None) -> Check:
    if not path.is_file():
        return Check("Environment file", "WARN", f"{path.name} is missing; defaults and the process environment are used.")
    if values is None:
        return Check("Environment file", "OK", f"{path.name} is present.")
    return Check("Environment file", "OK", f"{path.name} is present ({len(values)} keys).")


def check_process_overrides(env: dict, dotenv: dict) -> Check:
    overridden = [
        name for name, value in dotenv.items()
        if name != "SHELL_EXEC_TOKEN" and name in env and env[name] != value
    ]
    if overridden:
        return Check(
            "Process environment", "WARN",
            ".env overrides these process variables: " + ", ".join(sorted(overridden)) + ".",
        )
    return Check("Process environment", "OK", ".env does not silently override process variables.")


def _host_port(url: Optional[str], default_port: int) -> tuple[Optional[str], int]:
    if not url:
        return None, default_port
    parsed = urlparse(url)
    return parsed.hostname, int(parsed.port or default_port)


def check_tcp(
    name: str,
    host: Optional[str],
    port: int,
    connector: Callable[[str, int], bool],
    level: str = "FAIL",
) -> Check:
    if not host:
        return Check(name, "OK", f"{name} uses a local file.")
    try:
        reachable = connector(host, port)
    except (OSError, TypeError, ValueError):
        reachable = False
    if reachable:
        return Check(name, "OK", f"{name} is reachable at its configured host.")
    return Check(name, level, f"{name} is not reachable at its configured host.")


def check_temporal(disabled: bool, host: Optional[str], port: int, connector) -> Check:
    if disabled:
        return Check("Temporal", "OK", "Temporal is disabled.")
    try:
        reachable = bool(host and connector(host, port))
    except (OSError, TypeError, ValueError):
        reachable = False
    if reachable:
        return Check("Temporal", "OK", "Temporal is enabled and reachable.")
    return Check(
        "Temporal", "WARN",
        "Temporal is enabled but not reachable at TEMPORAL_HOST; set TEMPORAL_DISABLED=1 or start it.",
    )


def check_migrations(pending: Optional[list]) -> Check:
    if pending is None:
        return Check("Migrations", "WARN", "Pending migrations could not be read; run manage.py migrate.")
    if pending:
        return Check("Migrations", "WARN", f"{len(pending)} migration(s) pending; kazi up applies them.")
    return Check("Migrations", "OK", "No migrations are pending.")


def check_beat_drift(drift: Optional[dict]) -> Check:
    if drift is None:
        return Check("Beat schedule", "WARN", "Beat drift could not be read; run sync_beat_schedule.")
    total = sum(len(drift.get(key) or []) for key in ("missing", "extra", "changed"))
    if total:
        return Check(
            "Beat schedule", "WARN",
            f"{total} beat entries drift from settings; kazi up syncs them.",
        )
    return Check("Beat schedule", "OK", "Beat schedule matches settings.")


def check_shell_token(token_set: bool) -> Check:
    if token_set:
        return Check("Shell token", "OK", "SHELL_EXEC_TOKEN is set.")
    return Check(
        "Shell token", "WARN",
        "SHELL_EXEC_TOKEN is empty; run_command is disabled (fail closed).",
    )


def check_shell_profile(profile: str) -> Check:
    if profile == "open":
        return Check(
            "Shell profile", "WARN",
            "The open shell profile is unsandboxed: with great power comes great responsibility.",
        )
    return Check("Shell profile", "OK", f"Shell profile is {profile or 'standard'}.")


def _default_binder(host: str, port: int) -> bool:
    family = socket.AF_INET6 if ":" in host else socket.AF_INET
    with socket.socket(family, socket.SOCK_STREAM) as sock:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            sock.bind((host, port))
        except OSError:
            return False
    return True


def check_ports(addresses: Sequence[tuple[str, str, int]], binder=_default_binder) -> List[Check]:
    checks = []
    for label, host, port in addresses:
        try:
            free = binder(host, port)
        except OSError:
            free = False
        if free:
            checks.append(Check(f"{label} port", "OK", f"{label} port is free."))
        else:
            checks.append(Check(f"{label} port", "FAIL", f"{label} port is already in use."))
    return checks


def check_static(debug: bool, collected: bool) -> Check:
    if debug:
        return Check("Static files", "OK", "DEBUG is on; static files are served from source.")
    if collected:
        return Check("Static files", "OK", "Collected static files are present.")
    return Check(
        "Static files", "FAIL",
        "DEBUG is off and static files are not collected; run collectstatic.",
    )


def check_worker_counts(env: dict, lite: bool) -> Check:
    selected = next((name for name in WORKER_COUNT_KEYS if env.get(name)), None)
    if selected is None:
        return Check("Web workers", "OK", "No web worker count is set; the ASGI guard assumes one.")
    try:
        count = int(env[selected])
    except ValueError:
        return Check("Web workers", "FAIL", f"{selected} is not a whole number.")
    if lite and count > 1:
        return Check(
            "Web workers", "FAIL",
            f"{selected} is {count}; the lite profile requires exactly one web worker.",
        )
    return Check("Web workers", "OK", f"{selected} is set to {count}.")


def _as_int(value, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def check_pool(pool_size: int, processes: int, server_limit: int = 15) -> Check:
    if pool_size <= 0:
        return Check("Connection pool", "OK", "No per-process connection pool is configured.")
    total = pool_size * max(1, processes)
    if total > server_limit:
        return Check(
            "Connection pool", "WARN",
            f"DB_POOL_MAX_SIZE x processes is about {total}, above a typical {server_limit}-client limit.",
        )
    return Check("Connection pool", "OK", f"DB_POOL_MAX_SIZE x processes is about {total}.")


def _tcp_connect(host: str, port: int, timeout: float = 1.0) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def collect_checks(
    settings: Optional[dict],
    env: dict,
    dotenv: dict,
    root: Path = REPO_ROOT,
    system: Optional[str] = None,
    connector: Optional[Callable[[str, int], bool]] = None,
    binder=_default_binder,
) -> List[Check]:
    connector = connector or _tcp_connect
    checks = [
        check_python(sys.version_info),
        check_venv(sys.prefix, sys.base_prefix),
        check_lockfile(root),
    ]
    checks.append(check_env_file(root / ".env", dotenv))
    checks.append(check_required_keys(dotenv, env))
    checks.append(check_process_overrides(env, dotenv))
    if settings is None:
        checks.append(Check(
            "Effective settings", "FAIL",
            "Could not read effective Django settings; run python Backend/manage.py kazi_status --json.",
        ))
        return checks
    checks.extend(settings_checks(settings, env, connector, binder))
    return checks


def settings_checks(settings: dict, env: dict, connector, binder) -> List[Check]:
    profile = settings.get("profile", {})
    database = settings.get("database", {})
    redis = settings.get("redis", {})
    temporal = settings.get("temporal", {})
    checks = [
        check_migrations((settings.get("pending_migrations") or {}).get("items")),
        check_beat_drift(settings.get("beat_drift")),
        check_shell_token(bool(settings.get("shell", {}).get("token_set"))),
        check_shell_profile(str(profile.get("shell_profile") or "standard")),
        check_temporal(
            bool(profile.get("temporal_disabled")),
            temporal.get("host"),
            _as_int(temporal.get("port"), 7233),
            connector,
        ),
        check_tcp(
            "Database", database.get("host"), _as_int(database.get("port"), 5432),
            connector, level="FAIL",
        ),
    ]
    if not profile.get("lite"):
        checks.append(check_tcp(
            "Redis", redis.get("host"), _as_int(redis.get("port"), 6379),
            connector, level="FAIL",
        ))
    addresses = [
        ("Web", settings.get("web", {}).get("host", "127.0.0.1"),
         _as_int(settings.get("web", {}).get("port"), 8000)),
        ("Sidecar", settings.get("shell", {}).get("host", "127.0.0.1"),
         _as_int(settings.get("shell", {}).get("port"), 8765)),
    ]
    checks.extend(check_ports(addresses, binder))
    checks.append(check_static(
        bool(profile.get("debug")), bool(settings.get("static", {}).get("collected"))
    ))
    checks.append(check_worker_counts(env, bool(profile.get("lite"))))
    checks.append(check_pool(
        _as_int(settings.get("pool", {}).get("size"), 0),
        _as_int(settings.get("pool", {}).get("processes"), len(process_plan(settings))),
    ))
    return checks


def print_check(check: Check) -> None:
    print(f"{check.level:4} {check.name}: {check.message}")


def run_doctor(settings: Optional[dict], paths: RuntimePaths, env: dict, dotenv: dict) -> int:
    checks = collect_checks(settings, env, dotenv)
    for check in checks:
        print_check(check)
    return 1 if any(check.level == "FAIL" for check in checks) else 0


# --- process helpers --------------------------------------------------------

def _report_progress(label: str, started: float, next_report: float, progress) -> float:
    now = time.monotonic()
    if progress is not None and now >= next_report:
        progress(now - started)
        return now + PROGRESS_SECONDS
    return next_report


def wait_for_web(settings: dict, timeout: float = WEB_READY_SECONDS, progress=None) -> bool:
    web = settings.get("web", {})
    host = web.get("host", "127.0.0.1")
    port = _as_int(web.get("port"), 8000)
    started = time.monotonic()
    deadline = started + timeout
    next_report = started + PROGRESS_SECONDS
    while time.monotonic() < deadline:
        try:
            urllib.request.urlopen(  # nosec B310 - fixed loopback HTTP URL
                f"http://{host}:{port}/", timeout=2
            )
            return True
        except urllib.error.HTTPError:
            return True
        except (OSError, ValueError):
            next_report = _report_progress("web", started, next_report, progress)
            time.sleep(0.5)
    return False


def wait_for_control(paths: RuntimePaths, timeout: float = 60.0, progress=None,
                     process=None) -> bool:
    started = time.monotonic()
    deadline = started + timeout
    next_report = started + PROGRESS_SECONDS
    while time.monotonic() < deadline:
        if process is not None and process.poll() is not None:
            return False
        response = control_request(paths, "/ping", timeout=1)
        if response is not None and response.get("status") == 200:
            return True
        next_report = _report_progress("supervisor", started, next_report, progress)
        time.sleep(0.25)
    return False


def wait_for_worker(node: Optional[str] = None, timeout: float = WORKER_READY_SECONDS,
                    progress=None) -> bool:
    node = node or worker_node_name()
    started = time.monotonic()
    deadline = started + timeout
    next_report = started + PROGRESS_SECONDS
    command = [
        sys.executable, "-m", "celery", "-A", "Backend", "inspect", "ping",
        "-d", node, "--timeout", "2",
    ]
    while time.monotonic() < deadline:
        try:
            result = subprocess.run(  # nosec B603 - list argv, no shell
                command, cwd=str(BACKEND_DIR), env=utf8_env(),
                stdin=subprocess.DEVNULL, capture_output=True,
                text=True, encoding="utf-8", errors="replace",
                timeout=10, check=False,
            )
        except (OSError, subprocess.TimeoutExpired):
            return False
        if result.returncode == 0 and "pong" in (result.stdout or "").lower():
            return True
        next_report = _report_progress("worker", started, next_report, progress)
        time.sleep(1)
    return False


def preflight_failures(checks: Sequence[Check], ignore_static: bool = False) -> List[Check]:
    return [
        check for check in checks
        if check.level == "FAIL" and not (ignore_static and check.name == "Static files")
    ]


def run_preflight(settings: Optional[dict], paths: RuntimePaths, env: dict, dotenv: dict,
                  deadline: Optional[float] = None, ignore_static: bool = False) -> int:
    while True:
        checks = collect_checks(settings, env, dotenv)
        failures = preflight_failures(checks, ignore_static)
        if not failures or deadline is None or time.monotonic() >= deadline:
            break
        print(
            f"Waiting for services to come up: {len(failures)} check(s) still failing...",
            flush=True,
        )
        time.sleep(5)
    for check in checks:
        print_check(check)
    return 1 if failures else 0


def readiness_errors(web_ready: bool, worker_ready: bool, plan: Sequence[str],
                     paths: RuntimePaths) -> List[str]:
    errors = []
    if not web_ready:
        errors.append(f"web did not answer HTTP; see {paths.log_path('web')}")
    if "worker" in plan and not worker_ready:
        errors.append(f"worker did not answer a broker ping; see {paths.log_path('worker')}")
    return errors


def print_database_host(settings: dict) -> None:
    host = settings.get("database", {}).get("host") or "(local SQLite file)"
    print(f"Database host: {host}", flush=True)


def print_status_text(paths: RuntimePaths, settings: Optional[dict], system: Optional[str]) -> None:
    response = control_request(paths, "/status")
    if response is None or response.get("status") != 200:
        print("Kazi is not running.")
    else:
        body = response.get("body") or {}
        print(f"Kazi is running (supervisor pid {body.get('pid')}).")
        for child in body.get("children") or []:
            state = child.get("state") or ("given-up" if child.get("given_up") else "running")
            pid = child.get("pid")
            where = f"pid {pid} " if pid is not None else ""
            print(
                f"  {child.get('name')}: {where}{state}, "
                f"{child.get('restarts', 0)} restart(s)"
            )
        for name, reason in (body.get("given_up") or {}).items():
            print(f"  gave up on {name}: {reason}")
    if settings is None:
        return
    pending = (settings.get("pending_migrations") or {}).get("items")
    print_database_host(settings)
    if pending is not None:
        print(f"Pending migrations: {len(pending)}")
    drift = settings.get("beat_drift") or {}
    drifted = sum(len(drift.get(key) or []) for key in ("missing", "extra", "changed"))
    print(f"Beat schedule drift: {drifted} entries")
    turns = settings.get("turns") or {}
    print(f"Turns: {turns.get('open', 0)} open, {turns.get('paused', 0)} paused")


# --- autostart --------------------------------------------------------------

def startup_directory(env: dict) -> Path:
    appdata = env.get("APPDATA") or str(Path.home() / "AppData" / "Roaming")
    return Path(appdata) / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Startup"


def autostart_file(system: Optional[str] = None, env: Optional[dict] = None) -> Path:
    env = env or os.environ
    target = system_name(system)
    if target == "nt":
        return startup_directory(env) / "kazi.cmd"
    if target == "darwin":
        return Path.home() / "Library" / "LaunchAgents" / "com.kazi.core.plist"
    return Path(env.get("HOME") or Path.home()) / ".config" / "systemd" / "user" / "kazi.service"


def windows_autostart_text(root: Path = REPO_ROOT) -> str:
    python = Path(sys.executable)
    script = root / "scripts" / "kazi.py"
    log = root / ".kazi" / "logs" / "autostart.log"
    return (
        f"REM {AUTOSTART_MARKER}\r\n"
        "@echo off\r\n"
        f'cd /d "{root}"\r\n'
        f'"{python}" "{script}" up --autostart >> "{log}" 2>&1\r\n'
    )


def systemd_unit_text(root: Path = REPO_ROOT) -> str:
    python = Path(sys.executable)
    script = root / "scripts" / "kazi.py"
    return (
        f"# {AUTOSTART_MARKER}\n"
        "[Unit]\n"
        "Description=Kazi Core\n"
        "After=network-online.target\n"
        "Wants=network-online.target\n"
        "\n"
        "[Service]\n"
        "Type=simple\n"
        f"WorkingDirectory={root}\n"
        f'ExecStart="{python}" "{script}" run\n'
        "Restart=on-failure\n"
        "RestartSec=5\n"
        "\n"
        "[Install]\n"
        "WantedBy=default.target\n"
    )


def autostart_text(system: Optional[str] = None, root: Path = REPO_ROOT) -> str:
    if is_windows(system):
        return windows_autostart_text(root)
    return systemd_unit_text(root)


def autostart_enable(system: Optional[str] = None, env: Optional[dict] = None,
                     root: Path = REPO_ROOT) -> int:
    target = system_name(system)
    if target == "darwin":
        print("macOS autostart is documented only; see docs/kazi-command.md.")
        return 1
    path = autostart_file(system, env)
    path.parent.mkdir(parents=True, exist_ok=True)
    (root / ".kazi" / "logs").mkdir(parents=True, exist_ok=True)
    path.write_text(autostart_text(system, root), encoding="utf-8")
    print(f"Autostart enabled: {path}")
    return 0


def autostart_disable(system: Optional[str] = None, env: Optional[dict] = None) -> int:
    path = autostart_file(system, env)
    if not path.exists():
        print("Autostart is not enabled.")
        return 0
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        print("Autostart file is unreadable; leaving it alone.")
        return 1
    if AUTOSTART_MARKER not in text:
        print("Autostart file was not created by kazi; leaving it alone.")
        return 1
    path.unlink()
    print(f"Autostart disabled: {path}")
    return 0


def autostart_status(system: Optional[str] = None, env: Optional[dict] = None) -> int:
    path = autostart_file(system, env)
    if path.exists():
        print(f"Autostart is enabled: {path}")
        return 0
    print("Autostart is not enabled.")
    return 0


# --- commands ---------------------------------------------------------------

def write_plan(specs: Sequence[ChildSpec], paths: RuntimePaths, grace_seconds: float = 20.0) -> None:
    write_json(paths.plan, {
        "grace_seconds": float(grace_seconds),
        "children": [
            {"name": spec.name, "argv": list(spec.argv), "cwd": spec.cwd} for spec in specs
        ],
    })


def specs_from_plan(payload: dict) -> List[ChildSpec]:
    return [
        ChildSpec(str(item["name"]), list(item["argv"]), item.get("cwd"))
        for item in payload.get("children") or []
    ]


def start_supervisor_log(paths: RuntimePaths):
    paths.ensure()
    log_path = paths.log_path("supervisor")
    rotate_log(log_path)
    return open(log_path, "ab", buffering=0)


def _can_retry(deadline: Optional[float]) -> bool:
    return deadline is not None and time.monotonic() < deadline


def _bootstrap(settings: Optional[dict], error: Optional[str], paths: RuntimePaths,
               env: dict, dotenv: dict, deadline: Optional[float]) -> Optional[dict]:
    while True:
        if settings is None:
            if _can_retry(deadline):
                print(f"Waiting for Django settings ({error})...", flush=True)
                time.sleep(5)
                settings, error = load_settings(paths)
                continue
            print(f"FAIL settings: {error}.", flush=True)
            return None
        cleanup_posix_survivors(paths)
        debug = bool(settings.get("profile", {}).get("debug"))
        if run_preflight(
            settings, paths, env, dotenv, deadline=deadline, ignore_static=not debug,
        ):
            return None
        print_database_host(settings)
        if run_manage(["migrate", "--noinput"]) == 0:
            return settings
        if _can_retry(deadline):
            print("Waiting for the database and migrations...", flush=True)
            time.sleep(5)
            settings, error = load_settings(paths)
            continue
        print("FAIL migrate: the database migration failed; nothing was started.", flush=True)
        return None


def cmd_up(paths: RuntimePaths, system: Optional[str], autostart: bool, foreground: bool) -> int:
    deadline = time.monotonic() + AUTOSTART_WAIT_SECONDS if autostart else None
    settings, error = load_settings(paths)
    env = os.environ.copy()
    dotenv = load_dotenv_map()
    if settings is not None and not foreground and live_control(paths, system) is not None:
        print_status_text(paths, settings, system)
        return 0
    start_lock = SupervisorLock(paths.start_lock, system)
    if not start_lock.acquire():
        print(
            "Another kazi up is already starting or running; wait for it to finish "
            "or run `kazi status` in a moment.",
            flush=True,
        )
        return 0
    try:
        probe = SupervisorLock(paths.lock, system)
        if not probe.acquire():
            print("A Kazi supervisor is already running.", flush=True)
            print_status_text(paths, settings, system)
            return 0
        probe.release()
        settings = _bootstrap(settings, error, paths, env, dotenv, deadline)
        if settings is None:
            return 1
        plan = process_plan(settings, system)
        if "beat" in plan and run_manage(["sync_beat_schedule"]) != 0:
            print("FAIL beat schedule: sync_beat_schedule failed; nothing was started.", flush=True)
            return 1
        if not settings.get("profile", {}).get("debug") and run_manage(["collectstatic", "--noinput"]) != 0:
            print("FAIL static files: collectstatic failed; nothing was started.", flush=True)
            return 1
        specs = build_child_specs(settings, system)
        grace = _grace(settings)
        if foreground:
            return Supervisor(specs, paths, system, grace_seconds=grace).run()
        write_plan(specs, paths, grace)
        argv = [
            sys.executable, str(Path(__file__).resolve()), "__supervise",
            "--plan", str(paths.plan),
        ]
        log = start_supervisor_log(paths)
        try:
            supervisor_process = subprocess.Popen(  # nosec B603 - list argv, no shell
                argv,
                cwd=str(REPO_ROOT),
                stdin=subprocess.DEVNULL,
                stdout=log,
                stderr=subprocess.STDOUT,
                **supervisor_popen_kwargs(system),
            )
        except OSError:
            print("FAIL supervisor: could not start the supervisor.", flush=True)
            return 1
        finally:
            log.close()
        wait = AUTOSTART_WAIT_SECONDS if autostart else 60
        print("Waiting for the supervisor...", flush=True)
        if not wait_for_control(
            paths, timeout=wait, process=supervisor_process,
            progress=lambda seconds: print(f"  supervisor still starting ({seconds:.0f}s)", flush=True),
        ):
            if supervisor_process.poll() is not None:
                print(
                    f"FAIL supervisor: exited before the control channel came up; "
                    f"see {paths.log_path('supervisor')}",
                    flush=True,
                )
                return 1
            print("FAIL supervisor: the control channel did not come up.", flush=True)
            return 1
        print("Waiting for web...", flush=True)
        web_ready = wait_for_web(
            settings, timeout=WEB_READY_SECONDS,
            progress=lambda seconds: print(f"  web still starting ({seconds:.0f}s)", flush=True),
        )
        worker_ready = True
        if "worker" in plan:
            print("Waiting for the worker...", flush=True)
            worker_ready = wait_for_worker(
                timeout=WORKER_READY_SECONDS,
                progress=lambda seconds: print(f"  worker still starting ({seconds:.0f}s)", flush=True),
            )
        errors = readiness_errors(web_ready, worker_ready, plan, paths)
        if errors:
            for message in errors:
                print(f"FAIL {message}", flush=True)
            print("The supervisor is left running for inspection; run `kazi down` to stop it.", flush=True)
            return 1
        host = settings.get("web", {}).get("host", "127.0.0.1")
        port = _as_int(settings.get("web", {}).get("port"), 8000)
        print(f"Kazi is up: http://{host}:{port}/", flush=True)
        return 0
    finally:
        start_lock.release()


def _grace(settings: dict) -> float:
    return float(settings.get("profile", {}).get("turn_shutdown_grace_seconds") or 20)


def cmd_down(paths: RuntimePaths, system: Optional[str]) -> int:
    control = live_control(paths, system)
    if control is None:
        print("Kazi is not running.")
        remove_file(paths.control)
        return 0
    response = control_request(paths, "/stop", method="POST")
    if response is None or response.get("status") != 202:
        print("The supervisor refused the stop request; nothing was signalled.")
        return 1
    deadline = time.monotonic() + max(120, worker_shutdown_wait_seconds() + 60)
    while time.monotonic() < deadline:
        if not process_matches(control.get("pid"), control.get("creation"), system):
            print("Kazi is down.")
            return 0
        time.sleep(0.25)
    print("The supervisor is still running; it will stop its children itself.")
    return 1


def cmd_logs(paths: RuntimePaths, name: Optional[str], follow: bool) -> int:
    print("Logs can contain user content and task arguments. Treat them as sensitive.")
    if name:
        allowed = {"web", "worker", "beat", "sidecar", "supervisor", "autostart"}
        if name not in allowed:
            print(f"Unknown log {name!r}; choose one of: {', '.join(sorted(allowed))}.")
            return 1
        log = paths.log_path(name)
    else:
        log = paths.log_path("supervisor")
    if not log.exists():
        print(f"No log at {log}.")
        return 0
    with open(log, "r", encoding="utf-8", errors="replace") as handle:
        if follow:
            handle.seek(0, os.SEEK_END)
            try:
                while True:
                    line = handle.readline()
                    if line:
                        print(line, end="")
                    else:
                        time.sleep(0.5)
            except KeyboardInterrupt:
                return 0
        print(handle.read(), end="")
    return 0


def cmd_autostart(action: str, system: Optional[str]) -> int:
    if action == "enable":
        return autostart_enable(system)
    if action == "disable":
        return autostart_disable(system)
    return autostart_status(system)


def cmd_status(paths: RuntimePaths, system: Optional[str]) -> int:
    settings, _error = load_settings(paths)
    print_status_text(paths, settings, system)
    return 0


def cmd_doctor(paths: RuntimePaths) -> int:
    settings, error = load_settings(paths)
    if settings is None:
        print(f"WARN settings: {error}; only local checks are shown.")
    return run_doctor(settings, paths, os.environ, load_dotenv_map())


# --- entry point ------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="kazi", description="Start, stop, check and explain Kazi.")
    sub = parser.add_subparsers(dest="command", required=True)
    up = sub.add_parser("up", help="Start Kazi in the background.")
    up.add_argument("--autostart", action="store_true", help="Wait longer for services at logon.")
    sub.add_parser("run", help="Run the supervisor in the foreground.")
    sub.add_parser("down", help="Stop Kazi gracefully.")
    sub.add_parser("status", help="Show what is running and what needs attention.")
    logs = sub.add_parser("logs", help="Print or follow a process log.")
    logs.add_argument("name", nargs="?", help="web, worker, beat, sidecar, supervisor, autostart")
    logs.add_argument("-f", "--follow", action="store_true", help="Follow the log.")
    auto = sub.add_parser("autostart", help="Enable, disable or check logon autostart.")
    auto.add_argument("action", choices=("enable", "disable", "status"))
    sub.add_parser("doctor", help="Explain startup problems before they bite.")
    supervise = sub.add_parser("__supervise")
    supervise.add_argument("--plan", required=True)
    supervise.add_argument("--state-dir")
    supervise.add_argument("--log-dir")
    holder = sub.add_parser("__lock-holder")
    holder.add_argument("--path", required=True)
    holder.add_argument("--ready")
    holder.add_argument("--seconds", type=float, default=30.0)
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    system = system_name()
    if args.command == "__supervise":
        payload = read_json(Path(args.plan))
        if payload is None:
            print("Cannot read the supervisor plan.")
            return 1
        paths = RuntimePaths(args.state_dir)
        if args.log_dir:
            paths.logs = Path(args.log_dir)
        grace = float(payload.get("grace_seconds") or 20.0)
        return Supervisor(
            specs_from_plan(payload), paths, system, grace_seconds=grace,
        ).run()
    if args.command == "__lock-holder":
        lock = SupervisorLock(Path(args.path), system)
        if not lock.acquire():
            print("lock-busy")
            return 1
        if args.ready:
            Path(args.ready).write_text("held", encoding="utf-8")
        print("lock-held", flush=True)
        try:
            time.sleep(max(0.0, args.seconds))
        finally:
            lock.release()
        return 0
    paths = RuntimePaths()
    if args.command == "up":
        return cmd_up(paths, system, args.autostart, foreground=False)
    if args.command == "run":
        return cmd_up(paths, system, autostart=False, foreground=True)
    if args.command == "down":
        return cmd_down(paths, system)
    if args.command == "status":
        return cmd_status(paths, system)
    if args.command == "logs":
        return cmd_logs(paths, args.name, args.follow)
    if args.command == "autostart":
        return cmd_autostart(args.action, system)
    if args.command == "doctor":
        return cmd_doctor(paths)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
