"""Report the effective local install state for ``scripts/kazi.py``.

The launcher never imports Django, so this command is the bridge: it answers
"which processes should run, and what needs attention" from the settings Django
actually loaded. It prints the database host only, never a credential.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from urllib.parse import urlparse

from django.conf import settings
from django.core.management.base import BaseCommand


def _host_port(value: str, default_port: int) -> tuple[str, int]:
    text = str(value or "").strip()
    if not text:
        return "", default_port
    try:
        if "://" in text:
            parsed = urlparse(text)
            host = parsed.hostname or ""
            port = parsed.port
        else:
            host, separator, port_text = text.rpartition(":")
            if not separator:
                return text.strip("[]"), default_port
            try:
                port = int(port_text)
            except ValueError:
                return host.strip("[]") or text, default_port
        return host.strip("[]"), int(port or default_port)
    except (ValueError, TypeError):
        return "", default_port


DEFAULT_DB_PORTS = {
    "postgresql": 5432,
    "postgis": 5432,
    "mysql": 3306,
    "oracle": 1521,
    "mssql": 1433,
}


def _db_port(engine: str, value) -> int:
    port = _as_int(value, 0)
    if port:
        return port
    key = str(engine or "").rsplit(".", 1)[-1].lower()
    for name, default in DEFAULT_DB_PORTS.items():
        if name in key:
            return default
    return 0


def _database() -> dict:
    try:
        from django.db import connection

        config = connection.settings_dict
        engine = str(config.get("ENGINE") or "")
        return {
            "engine": engine.rsplit(".", 1)[-1],
            "host": str(config.get("HOST") or ""),
            "port": _db_port(engine, config.get("PORT")),
        }
    except Exception:
        return {"engine": "", "host": "", "port": 0}


def _pending_migrations() -> list | None:
    try:
        from django.db import connection
        from django.db.migrations.executor import MigrationExecutor

        executor = MigrationExecutor(connection)
        plan = executor.migration_plan(executor.loader.graph.leaf_nodes())
        return [f"{migration.app_label}.{migration.name}" for migration, _backwards in plan]
    except Exception:
        return None


def _beat_drift() -> dict | None:
    try:
        from workflows.management.commands.sync_beat_schedule import beat_schedule_drift

        return beat_schedule_drift()
    except Exception:
        return None


def _turn_counts() -> dict:
    try:
        from orchestration.models import Turn

        return {
            "open": Turn.objects.filter(state__in=("queued", "running", "committing")).count(),
            "paused": Turn.objects.filter(state="paused").count(),
        }
    except Exception:
        return {}


def _last_reaper() -> dict:
    out: dict = {"result": None, "last_run_at": None, "run_count": None}
    try:
        from django.core.cache import cache
        from django_celery_beat.models import PeriodicTask

        from orchestration.tasks import LAST_REAPER_CACHE_KEY

        out["result"] = cache.get(LAST_REAPER_CACHE_KEY)
        row = PeriodicTask.objects.filter(name="reap-lapsed-turns").values(
            "last_run_at", "total_run_count"
        ).first()
        if row:
            last = row.get("last_run_at")
            out["last_run_at"] = last.isoformat() if last else None
            out["run_count"] = row.get("total_run_count")
    except Exception:
        pass
    return out


def _static() -> dict:
    try:
        root = Path(settings.STATIC_ROOT)
        return {
            "root": str(root),
            "collected": root.is_dir() and any(root.iterdir()),
        }
    except Exception:
        return {"root": "", "collected": False}


def _profile() -> dict:
    return {
        "lite": bool(getattr(settings, "KAZI_LITE_PROFILE", False)),
        "in_process_scheduler": bool(getattr(settings, "IN_PROCESS_SCHEDULER", False)),
        "temporal_disabled": bool(getattr(settings, "TEMPORAL_DISABLED", False)),
        "shell_profile": str(getattr(settings, "SHELL_EXEC_PROFILE", "standard")),
        "debug": bool(getattr(settings, "DEBUG", False)),
        "turn_shutdown_grace_seconds": int(
            getattr(settings, "TURN_SHUTDOWN_GRACE_SECONDS", 20)
        ),
    }


def _redis() -> dict:
    host, port = _host_port(str(getattr(settings, "REDIS_URL", "")), 6379)
    return {"host": host, "port": port}


def _temporal() -> dict:
    host, port = _host_port(
        str(getattr(settings, "TEMPORAL_HOST", "localhost:7233")), 7233
    )
    return {"host": host, "port": port}


def _pool() -> dict:
    raw = (os.environ.get("DB_POOL_MAX_SIZE") or "").strip()
    try:
        size = int(raw) if raw else 0
    except ValueError:
        size = 0
    return {"size": size}


def _as_int(value, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _web() -> dict:
    return {
        "host": os.environ.get("KAZI_WEB_HOST", "127.0.0.1"),
        "port": _as_int(os.environ.get("KAZI_WEB_PORT"), 8000),
    }


def _shell() -> dict:
    return {
        "host": str(getattr(settings, "SHELL_EXEC_HOST", "127.0.0.1")),
        "port": _as_int(getattr(settings, "SHELL_EXEC_PORT", 8765), 8765),
        "token_set": bool(getattr(settings, "SHELL_EXEC_TOKEN", "")),
    }


def collect() -> dict:
    return {
        "profile": _profile(),
        "database": _database(),
        "redis": _redis(),
        "temporal": _temporal(),
        "shell": _shell(),
        "web": _web(),
        "static": _static(),
        "pool": _pool(),
        "pending_migrations": {"items": _pending_migrations()},
        "beat_drift": _beat_drift(),
        "turns": _turn_counts(),
        "last_reaper": _last_reaper(),
    }


class Command(BaseCommand):
    help = "Report effective local install state for scripts/kazi.py."

    def add_arguments(self, parser):
        parser.add_argument("--json", action="store_true", help="Print JSON only.")

    def handle(self, *args, **options):
        payload = collect()
        if options.get("json"):
            self.stdout.write(json.dumps(payload))
            return
        profile = payload["profile"]
        self.stdout.write(f"Profile: lite={profile['lite']} in_process_scheduler={profile['in_process_scheduler']}")
        self.stdout.write(f"Temporal disabled: {profile['temporal_disabled']}")
        self.stdout.write(f"Shell profile: {profile['shell_profile']}")
        self.stdout.write(f"Database host: {payload['database']['host'] or '(local SQLite file)'}")
        pending = payload["pending_migrations"]["items"]
        self.stdout.write(f"Pending migrations: {'unknown' if pending is None else len(pending)}")
        drift = payload["beat_drift"]
        if drift is None:
            self.stdout.write("Beat schedule drift: unknown")
        else:
            total = sum(len(drift.get(key) or []) for key in ("missing", "extra", "changed"))
            self.stdout.write(f"Beat schedule drift: {total} entries")
        turns = payload["turns"]
        self.stdout.write(f"Turns: {turns.get('open', 0)} open, {turns.get('paused', 0)} paused")
        reaper = payload["last_reaper"]
        self.stdout.write(f"Last reaper run: {reaper.get('last_run_at') or 'unknown'}")
