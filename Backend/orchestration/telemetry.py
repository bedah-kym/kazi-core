"""Lightweight telemetry logging for orchestration events."""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from threading import Lock
from typing import Any, Dict, Optional

from django.conf import settings
import sys

_LOCK = Lock()


def _telemetry_enabled() -> bool:
    return getattr(settings, "ORCHESTRATION_TELEMETRY_ENABLED", True)


def _telemetry_path() -> str:
    configured = getattr(settings, "ORCHESTRATION_TELEMETRY_PATH", None)
    if configured:
        return str(configured)
    base_dir = getattr(settings, "BASE_DIR", os.getcwd())
    return os.path.join(base_dir, "telemetry", "orchestration.jsonl")


def _ensure_dir(path: str) -> None:
    directory = os.path.dirname(path)
    if directory:
        os.makedirs(directory, exist_ok=True)


def record_event(event_type: str, payload: Optional[Dict[str, Any]] = None) -> None:
    if not _telemetry_enabled() or not event_type:
        return
    data = {
        "event": event_type,
        "ts": datetime.now(timezone.utc).isoformat(),
    }
    if isinstance(payload, dict):
        data.update(payload)
    line = json.dumps(data, ensure_ascii=True)
    path = _telemetry_path()
    try:
        _ensure_dir(path)
        with _LOCK:
            with open(path, "a", encoding="utf-8") as handle:
                handle.write(line + "\n")
        print(line, file=sys.stdout, flush=True)
    except Exception:
        # Telemetry should never block core flows.
        return
