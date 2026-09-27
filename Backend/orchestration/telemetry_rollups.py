"""
Nightly rollup of orchestration JSONL telemetry into entity facts and
derived watches (issue #153).

Design rules:
- Pure arithmetic/statistics. The LLM is used only by consumers of these
  facts (digests, proposals), never here.
- Read-only: never executes anything, never mutates the live telemetry
  file (it is rotated, not truncated).
- Incremental: each run consumes only rotated files it has not fully
  processed yet (per-file byte-offset bookmarks), so wall-clock time
  stays flat as the corpus grows.
- Idempotent: a per-window marker makes a second run in the same window
  a no-op.
- Privacy: extraction reads only structured fields (command lines,
  params, transcript inputs, typed command_result fields). Free-text
  user content is never scanned.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Iterable, List, Optional, Tuple

from django.core.cache import cache

logger = logging.getLogger(__name__)

FACTS_CACHE_KEY = "telemetry_rollup:facts"
WATCHES_CACHE_KEY = "telemetry_rollup:watches"
FACTS_TTL_SECONDS = 48 * 60 * 60
WATCHES_TTL_SECONDS = 48 * 60 * 60
BOOKMARK_TTL_SECONDS = 30 * 24 * 60 * 60
WINDOW_TTL_SECONDS = 48 * 60 * 60

MAX_LINES_PER_RUN = 20000
MAX_FACTS = 100
MAX_WATCHES = 200
FACT_MAX_AGE_DAYS = 30
MIN_SAMPLES = 5
DEFAULT_ERROR_RATE_THRESHOLD = 0.10
DEFAULT_CERT_WARN_DAYS = 30
COOCCURRENCE_THRESHOLD = 0.5
MAX_COOCCURRENCE_TYPES = 8
ROTATION_RETRIES = 5
ROTATION_RETRY_DELAY_SECONDS = 0.2
BOOKMARK_FLUSH_EVERY = 500

_IPV4_RE = re.compile(
    r"\b(?:(?:25[0-5]|2[0-4]\d|1?\d?\d)\.){3}(?:25[0-5]|2[0-4]\d|1?\d?\d)\b"
)
_MAC_RE = re.compile(r"\b(?:[0-9A-Fa-f]{2}[:-]){5}[0-9A-Fa-f]{2}\b")
_COMMAND_HOST_RE = re.compile(
    r"\b(?:ssh|ping|curl|nc|telnet|mtr|traceroute)\s+(?:[A-Za-z0-9._-]+@)?"
    r"([A-Za-z0-9][A-Za-z0-9.-]+)\b"
)
_ENTITY_KEYS = {
    "origin",
    "destination",
    "city",
    "location",
    "email",
    "phone_number",
    "amount",
    "currency",
    "departure_date",
    "return_date",
    "check_in_date",
    "check_out_date",
    "itinerary_id",
    "invoice_id",
}


def _window_key(day: str) -> str:
    return f"telemetry_rollup:window:{day}"


def _mark_window_done(day: str) -> None:
    try:
        cache.set(_window_key(day), True, timeout=WINDOW_TTL_SECONDS)
    except Exception:
        logger.warning("Telemetry rollup could not persist window marker", exc_info=True)


def _bookmark_key(path: str) -> str:
    return f"telemetry_rollup:bookmark:{path}"


def _telemetry_path() -> str:
    from orchestration.telemetry import _telemetry_path as _path

    return _path()


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _day_str(now: datetime) -> str:
    return now.strftime("%Y%m%d")


def _rotated_file_pattern() -> str:
    live = _telemetry_path()
    directory = os.path.dirname(live)
    stem, ext = os.path.splitext(os.path.basename(live))
    return os.path.join(directory, f"{stem}-*{ext}")


def rotate_live_telemetry_file(now: Optional[datetime] = None) -> Optional[str]:
    """Rename the live JSONL to a dated rotated file for consumption.

    Writers open the live file per line and recreate it on their next
    write, so the rename never loses events. Returns the rotated path,
    or None when there is nothing to rotate or the file stays locked.
    """
    now = now or _utc_now()
    live = _telemetry_path()
    if not os.path.exists(live):
        return None
    directory = os.path.dirname(live)
    stem, ext = os.path.splitext(os.path.basename(live))
    candidate = os.path.join(directory, f"{stem}-{_day_str(now)}{ext}")
    index = 1
    while os.path.exists(candidate):
        index += 1
        candidate = os.path.join(directory, f"{stem}-{_day_str(now)}-{index}{ext}")
    for _ in range(ROTATION_RETRIES):
        try:
            os.replace(live, candidate)
            return candidate
        except OSError:
            time.sleep(ROTATION_RETRY_DELAY_SECONDS)
    logger.warning("Telemetry rotation failed after %s retries: %s", ROTATION_RETRIES, live)
    return None


def list_rotated_files() -> List[str]:
    import glob

    return sorted(glob.glob(_rotated_file_pattern()))


def reset_bookmarks() -> int:
    cleared = 0
    for path in list_rotated_files():
        if cache.delete(_bookmark_key(path)):
            cleared += 1
    return cleared


def reset_window_marker(day: Optional[str] = None) -> None:
    cache.delete(_window_key(day or _day_str(_utc_now())))


def _parse_line(line: str) -> Optional[Dict[str, Any]]:
    stripped = line.strip()
    if not stripped:
        return None
    try:
        data = json.loads(stripped)
    except (ValueError, TypeError):
        return None
    return data if isinstance(data, dict) else None


def _read_events_from_file(
    path: str,
    bookmark: int,
    max_lines: int,
) -> Tuple[List[Dict[str, Any]], int]:
    """Stream events from ``path`` starting at ``bookmark`` bytes.

    Telemetry lines are ASCII (``ensure_ascii=True``), so byte offsets
    are character offsets. Malformed or partial trailing lines (from
    interleaved concurrent writes) are skipped, never fatal.
    """
    events: List[Dict[str, Any]] = []
    offset = bookmark
    try:
        with open(path, "r", encoding="utf-8") as handle:
            handle.seek(offset)
            while len(events) < max_lines:
                line = handle.readline()
                if not line:
                    break
                offset += len(line.encode("utf-8"))
                event = _parse_line(line)
                if event is not None:
                    events.append(event)
    except OSError as exc:
        logger.warning("Telemetry rollup could not read %s: %s", path, exc)
    return events, offset


# ---------------------------------------------------------------------------
#  Entity facts                                                              #
# ---------------------------------------------------------------------------

def extract_entity_facts(events: Iterable[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    """Deterministic entity extraction over structured event fields.

    Returns a dict keyed by stable collection keys mapping to fact records:
    ``{type, name, value, first_seen, last_seen, occurrences}``. Keys are
    never rebuilt from type+value at the end: two hosts sharing one IP, or
    two distinct values for the same entity field, must stay separate
    records instead of colliding.
    """
    collected: Dict[str, Dict[str, Any]] = {}

    def _add(fact_key: str, fact_type: str, value: str, ts: str, name: Optional[str] = None) -> None:
        value = str(value).strip()[:120]
        if not value or value in {"", "None", "null", "none"}:
            return
        record = collected.get(fact_key)
        if record is None:
            collected[fact_key] = {
                "type": fact_type,
                "name": name,
                "value": value,
                "first_seen": ts,
                "last_seen": ts,
                "occurrences": 1,
            }
        else:
            record["occurrences"] += 1
            if not record.get("name") and name:
                record["name"] = name
            if ts < record["first_seen"]:
                record["first_seen"] = ts
            if ts > record["last_seen"]:
                record["last_seen"] = ts

    for event in events:
        ts = str(event.get("ts") or _utc_now().isoformat())
        event_type = str(event.get("event") or "")

        # Structured host labels from typed command results (shell events).
        host_label = event.get("host") or event.get("hostname")
        if isinstance(host_label, str) and host_label.strip():
            host_label = host_label.strip()
            address = event.get("ip") or event.get("address")
            _add(
                f"host:{host_label}",
                "host",
                str(address) if address else host_label,
                ts,
                name=host_label,
            )

        ip_value = event.get("ip")
        if isinstance(ip_value, str) and _IPV4_RE.fullmatch(ip_value.strip()):
            _add(f"ip:{ip_value.strip()}", "ip", ip_value.strip(), ts)

        mac_value = event.get("mac")
        if isinstance(mac_value, str) and _MAC_RE.fullmatch(mac_value.strip()):
            _add(f"mac:{mac_value.strip()}", "mac", mac_value.strip(), ts)

        # Command lines: "ssh host", "ping host" (structured shell fields).
        command = event.get("command") or event.get("cmdline")
        if isinstance(command, str):
            for match in _COMMAND_HOST_RE.finditer(command):
                target = match.group(1)
                if not _IPV4_RE.fullmatch(target):
                    _add(f"host:{target}", "host", target, ts)
            for match in _IPV4_RE.finditer(command):
                _add(f"ip:{match.group(0)}", "ip", match.group(0), ts)
            for match in _MAC_RE.finditer(command):
                _add(f"mac:{match.group(0)}", "mac", match.group(0), ts)

        # Service usage from receipts.
        if event_type == "action_receipt" and event.get("action"):
            _add(f"service:{event['action']}", "service", str(event["action"]), ts)

        # Named entities from tool params and loop transcripts.
        param_dicts: List[Dict[str, Any]] = []
        if isinstance(event.get("params"), dict):
            param_dicts.append(event["params"])
        if isinstance(event.get("input"), dict):
            param_dicts.append(event["input"])
        transcript = event.get("transcript")
        if isinstance(transcript, list):
            for entry in transcript:
                if isinstance(entry, dict) and isinstance(entry.get("input"), dict):
                    param_dicts.append(entry["input"])
        for params in param_dicts:
            for key in _ENTITY_KEYS:
                value = params.get(key)
                if value not in (None, "", [], {}):
                    if isinstance(value, (list, tuple)):
                        value = ", ".join(str(v) for v in value)
                    value = str(value).strip()
                    # Bounded, value-derived discriminator: distinct values
                    # for the same field stay separate records without
                    # embedding free text in the key.
                    digest = hashlib.sha256(value.encode("utf-8")).hexdigest()[:12]
                    _add(f"entity:{key}:{digest}", "entity", value, ts, name=key)

    ranked = sorted(
        collected.items(),
        key=lambda item: item[1]["occurrences"],
        reverse=True,
    )[:MAX_FACTS]
    return dict(ranked)


# ---------------------------------------------------------------------------
#  Derived watches                                                           #
# ---------------------------------------------------------------------------

def _trend(current: float, previous: Optional[float], epsilon: float = 1e-9) -> str:
    if previous is None:
        return "unknown"
    if current > previous + epsilon:
        return "rising"
    if current < previous - epsilon:
        return "falling"
    return "stable"


def _watch_index(watches: List[Dict[str, Any]]) -> Dict[str, float]:
    index: Dict[str, float] = {}
    for watch in watches:
        labels = watch.get("labels") or {}
        key = f"{watch.get('metric')}:{json.dumps(labels, sort_keys=True)}"
        value = watch.get("current_value")
        if isinstance(value, (int, float)):
            index[key] = float(value)
    return index


def _parse_ts(event: Dict[str, Any]) -> Optional[datetime]:
    raw = event.get("ts")
    if not isinstance(raw, str):
        return None
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def _action_error_rate_watches(
    events: List[Dict[str, Any]], previous: Dict[str, float]
) -> List[Dict[str, Any]]:
    counts: Dict[str, List[int]] = {}
    for event in events:
        if event.get("event") != "action_receipt":
            continue
        action = event.get("action")
        if not action:
            continue
        bucket = counts.setdefault(str(action), [0, 0])
        if event.get("status") in {"error", "failed"}:
            bucket[1] += 1
        else:
            bucket[0] += 1
    watches = []
    for action, (ok, err) in counts.items():
        total = ok + err
        if total < MIN_SAMPLES:
            continue
        rate = round(err / total, 4)
        key = f"action_error_rate:{json.dumps({'action': action}, sort_keys=True)}"
        watches.append({
            "metric": "action_error_rate",
            "labels": {"action": action},
            "threshold": DEFAULT_ERROR_RATE_THRESHOLD,
            "current_value": rate,
            "trend": _trend(rate, previous.get(key)),
            "samples": total,
        })
    return watches


def _disk_growth_watches(events: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    samples: Dict[str, Dict[str, int]] = {}
    for event in events:
        host = event.get("host")
        if not isinstance(host, str) or not host.strip():
            continue
        used = event.get("disk_used_bytes")
        if not isinstance(used, int):
            continue
        parsed = _parse_ts(event)
        if parsed is None:
            continue
        day = parsed.strftime("%Y-%m-%d")
        bucket = samples.setdefault(host.strip(), {})
        bucket[day] = used
    watches = []
    for host, per_day in samples.items():
        days = sorted(per_day)
        if len(days) < 2:
            continue
        first, last = per_day[days[0]], per_day[days[-1]]
        span_days = max(len(days) - 1, 1)
        growth = round((last - first) / span_days, 2)
        trend = "rising" if growth > 0 else ("falling" if growth < 0 else "stable")
        watches.append({
            "metric": "disk_growth_bytes_per_day",
            "labels": {"host": host},
            "threshold": None,
            "current_value": growth,
            "trend": trend,
            "samples": len(days),
        })
    return watches


def _cert_expiry_watches(
    events: List[Dict[str, Any]], previous: Dict[str, float]
) -> List[Dict[str, Any]]:
    per_host: Dict[str, float] = {}
    for event in events:
        host = event.get("host")
        days = event.get("cert_expiry_days")
        if not isinstance(host, str) or not isinstance(days, (int, float)):
            continue
        key = host.strip()
        per_host[key] = min(per_host.get(key, float("inf")), float(days))
    watches = []
    for host, min_days in per_host.items():
        key = f"cert_expiry_days:{json.dumps({'host': host}, sort_keys=True)}"
        current = round(min_days, 2)
        watches.append({
            "metric": "cert_expiry_days",
            "labels": {"host": host},
            "threshold": DEFAULT_CERT_WARN_DAYS,
            "current_value": current,
            "trend": _trend(current, previous.get(key)),
        })
    return watches


def _uptime_heartbeat_watches(events: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    counts: Dict[str, int] = {}
    for event in events:
        if event.get("event") not in {"progress_event", "heartbeat"}:
            continue
        room_id = event.get("room_id")
        parsed = _parse_ts(event)
        if parsed is None:
            continue
        day = parsed.strftime("%Y-%m-%d")
        counts[f"{room_id}:{day}"] = counts.get(f"{room_id}:{day}", 0) + 1
    per_room: Dict[str, Dict[str, int]] = {}
    for key, count in counts.items():
        room_id, day = key.rsplit(":", 1)
        per_room.setdefault(room_id, {})[day] = count
    watches = []
    for room_id, per_day in per_room.items():
        days = sorted(per_day)
        if not days:
            continue
        current = per_day[days[-1]]
        prior = None
        if len(days) >= 2:
            yesterday = (datetime.fromisoformat(days[-1]) - timedelta(days=1)).strftime("%Y-%m-%d")
            if days[-2] == yesterday:
                prior = per_day[days[-2]]
        watches.append({
            "metric": "uptime_heartbeats",
            "labels": {"room_id": room_id},
            "threshold": None,
            "current_value": current,
            "trend": _trend(float(current), float(prior)) if prior is not None else "unknown",
        })
    return watches


def _cooccurrence_watches(events: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    type_frequency: Dict[str, int] = {}
    hourly: Dict[Tuple[str, int], set] = {}
    for event in events:
        event_type = str(event.get("event") or "")
        if not event_type:
            continue
        type_frequency[event_type] = type_frequency.get(event_type, 0) + 1
        parsed = _parse_ts(event)
        if parsed is None:
            continue
        bucket = (parsed.strftime("%Y-%m-%d"), parsed.hour)
        hourly.setdefault(bucket, set()).add(event_type)
    top_types = sorted(type_frequency, key=type_frequency.get, reverse=True)[
        :MAX_COOCCURRENCE_TYPES
    ]
    watches = []
    for i, type_a in enumerate(top_types):
        for type_b in top_types[i + 1:]:
            both = 0
            either = 0
            for types in hourly.values():
                has_a = type_a in types
                has_b = type_b in types
                if has_a and has_b:
                    both += 1
                if has_a or has_b:
                    either += 1
            if either == 0 or both < 2:
                continue
            jaccard = round(both / either, 4)
            if jaccard < COOCCURRENCE_THRESHOLD:
                continue
            watches.append({
                "metric": "event_cooccurrence",
                "labels": {"event_a": type_a, "event_b": type_b},
                "threshold": COOCCURRENCE_THRESHOLD,
                "current_value": jaccard,
                "trend": "correlated" if jaccard >= COOCCURRENCE_THRESHOLD else "stable",
                "samples": both,
            })
    return watches


def compute_watches(
    events: List[Dict[str, Any]],
    previous_watches: Optional[List[Dict[str, Any]]] = None,
) -> List[Dict[str, Any]]:
    previous = _watch_index(previous_watches or [])
    watches = []
    watches.extend(_action_error_rate_watches(events, previous))
    watches.extend(_disk_growth_watches(events))
    watches.extend(_cert_expiry_watches(events, previous))
    watches.extend(_uptime_heartbeat_watches(events))
    watches.extend(_cooccurrence_watches(events))
    return watches[:MAX_WATCHES]


# ---------------------------------------------------------------------------
#  Storage                                                                   #
# ---------------------------------------------------------------------------

def load_facts() -> Dict[str, Any]:
    try:
        return cache.get(FACTS_CACHE_KEY) or {"facts": {}}
    except Exception:
        return {"facts": {}}


def load_watches() -> List[Dict[str, Any]]:
    try:
        return cache.get(WATCHES_CACHE_KEY) or []
    except Exception:
        return []


def _store_facts(facts: Dict[str, Any]) -> bool:
    try:
        cache.set(FACTS_CACHE_KEY, facts, timeout=FACTS_TTL_SECONDS)
        return True
    except Exception:
        logger.warning("Telemetry rollup could not persist facts", exc_info=True)
        return False


def _store_watches(watches: List[Dict[str, Any]]) -> bool:
    try:
        cache.set(WATCHES_CACHE_KEY, watches, timeout=WATCHES_TTL_SECONDS)
        return True
    except Exception:
        logger.warning("Telemetry rollup could not persist watches", exc_info=True)
        return False


def _merge_facts(current: Dict[str, Any], new: Dict[str, Any]) -> Dict[str, Any]:
    merged = dict((current.get("facts") or {}))
    cutoff = (_utc_now() - timedelta(days=FACT_MAX_AGE_DAYS)).isoformat()
    for key, record in (new or {}).items():
        if record.get("last_seen", "") < cutoff:
            continue
        existing = merged.get(key)
        if existing is None:
            merged[key] = record
        else:
            existing["occurrences"] = existing.get("occurrences", 0) + record.get("occurrences", 0)
            if record["first_seen"] < existing["first_seen"]:
                existing["first_seen"] = record["first_seen"]
            if record["last_seen"] > existing["last_seen"]:
                existing["last_seen"] = record["last_seen"]
    pruned = {
        key: record
        for key, record in merged.items()
        if record.get("last_seen", "") >= cutoff
    }
    ranked = sorted(
        pruned.items(),
        key=lambda item: item[1].get("occurrences", 0),
        reverse=True,
    )[:MAX_FACTS]
    return {
        "facts": dict(ranked),
        "updated_at": _utc_now().isoformat(),
    }


# ---------------------------------------------------------------------------
#  Rollup driver                                                             #
# ---------------------------------------------------------------------------

def run_rollup(
    *,
    now: Optional[datetime] = None,
    reset_bookmarks: bool = False,
    max_lines: int = MAX_LINES_PER_RUN,
) -> Dict[str, Any]:
    """Rotate, consume, and compute. Returns a summary dict.

    ``noop`` is True when this window was already processed. The window
    marker is only set after a successful pass, so a crashed run resumes
    from its bookmarks instead of silently skipping.
    """
    now = now or _utc_now()
    day = _day_str(now)
    try:
        window = cache.get(_window_key(day))
    except Exception:
        window = False
    if window and not reset_bookmarks:
        return {"noop": True, "day": day}

    rotated_path = rotate_live_telemetry_file(now)
    files = list_rotated_files()

    events: List[Dict[str, Any]] = []
    lines_read = 0
    pending_bookmarks: List[Tuple[str, int, bool]] = []
    for path in files:
        if lines_read >= max_lines:
            break
        bookmark = 0 if reset_bookmarks else int(cache.get(_bookmark_key(path)) or 0)
        remaining = max_lines - lines_read
        file_events, new_offset = _read_events_from_file(path, bookmark, remaining)
        events.extend(file_events)
        lines_read += len(file_events)
        pending_bookmarks.append((path, new_offset, new_offset > bookmark))

    if not events:
        _mark_window_done(day)
        return {
            "noop": False,
            "day": day,
            "rotated": bool(rotated_path),
            "files_processed": 0,
            "lines_processed": 0,
            "facts": 0,
            "watches": 0,
        }

    new_facts = extract_entity_facts(events)
    stored_facts = _merge_facts(load_facts(), new_facts)
    facts_ok = _store_facts(stored_facts)

    previous_watches = load_watches()
    watches = compute_watches(events, previous_watches)
    watches_ok = _store_watches(watches)

    # Bookmarks and the window marker only advance once the computed facts
    # and watches are durably stored. If storage failed, a later retry
    # re-reads the same offsets instead of silently skipping the window.
    if not (facts_ok and watches_ok):
        return {
            "noop": False,
            "day": day,
            "rotated": bool(rotated_path),
            "error": "storage_unavailable",
            "files_processed": 0,
            "lines_processed": lines_read,
            "facts": 0,
            "watches": 0,
        }

    files_touched = 0
    for path, new_offset, advanced in pending_bookmarks:
        try:
            # Always refresh the TTL, even at EOF where the offset is
            # unchanged: an expired bookmark would force a full re-read.
            cache.set(_bookmark_key(path), new_offset, timeout=BOOKMARK_TTL_SECONDS)
        except Exception:
            logger.warning("Telemetry rollup could not persist bookmark for %s", path)
        if advanced:
            files_touched += 1

    _mark_window_done(day)
    return {
        "noop": False,
        "day": day,
        "rotated": bool(rotated_path),
        "files_processed": files_touched,
        "lines_processed": lines_read,
        "facts": len(stored_facts.get("facts") or {}),
        "watches": len(watches),
    }
