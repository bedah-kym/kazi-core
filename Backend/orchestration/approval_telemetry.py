"""Approval telemetry per rule (v0.7, issue #166).

Nightly rollup of approve / reject / expire counts and median latency per
(connector, action, risk_level, rule) from ``WorkflowApprovalRecord`` rows.
Aggregates only — no user content. High-approval rules become "promote to a
scoped rule" candidates for the standing-grant path (#159).
"""
from __future__ import annotations

import logging
from statistics import median
from typing import Any, Dict, Iterable, List, Optional

from django.conf import settings
from django.utils import timezone

logger = logging.getLogger(__name__)

METRICS_CACHE_KEY = "approval_telemetry:metrics:{user_id}"
CANDIDATES_CACHE_KEY = "approval_telemetry:candidates:{user_id}"
_CACHE_TTL_SECONDS = 48 * 3600

_EXPIRE_STATUSES = {"timed_out", "cancelled"}
_DECIDED_STATUSES = {"approved", "rejected"}


def _window_days() -> int:
    try:
        return max(1, int(getattr(settings, "APPROVAL_TELEMETRY_WINDOW_DAYS", 1) or 1))
    except (TypeError, ValueError):
        return 1


def _promote_rate() -> float:
    try:
        return float(getattr(settings, "APPROVAL_PROMOTE_THRESHOLD_RATE", 0.95) or 0.95)
    except (TypeError, ValueError):
        return 0.95


def _promote_count() -> int:
    try:
        return int(getattr(settings, "APPROVAL_PROMOTE_THRESHOLD_COUNT", 50) or 50)
    except (TypeError, ValueError):
        return 50


def _risk_level(action: str) -> str:
    try:
        from orchestration.action_catalog import get_action_definition

        return str((get_action_definition(action) or {}).get("risk_level") or "low").lower()
    except Exception:
        return "unknown"


def compute_approval_metrics(records: Iterable[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Pure aggregation over synthetic/DB record dicts.

    Each record: ``service``, ``action``, ``status``, ``rule`` (optional),
    ``risk_level`` (optional), ``created_at``/``reviewed_at`` (datetimes).
    Returns one metric row per (service, action, risk_level, rule).
    """
    groups: Dict[Any, Dict[str, Any]] = {}
    for record in records or []:
        if not isinstance(record, dict):
            continue
        status = str(record.get("status") or "")
        if status not in _DECIDED_STATUSES and status not in _EXPIRE_STATUSES:
            continue
        key = (
            str(record.get("service") or ""),
            str(record.get("action") or ""),
            str(record.get("risk_level") or _risk_level(str(record.get("action") or ""))),
            str(record.get("rule") or "none"),
        )
        group = groups.setdefault(key, {
            "service": key[0],
            "action": key[1],
            "risk_level": key[2],
            "rule": key[3],
            "approved": 0,
            "rejected": 0,
            "expired": 0,
            "latencies": [],
        })
        if status == "approved":
            group["approved"] += 1
        elif status == "rejected":
            group["rejected"] += 1
        else:
            group["expired"] += 1

        created = record.get("created_at")
        reviewed = record.get("reviewed_at")
        if status in _DECIDED_STATUSES and created is not None and reviewed is not None:
            try:
                group["latencies"].append(max(0.0, (reviewed - created).total_seconds()))
            except TypeError:
                continue

    metrics: List[Dict[str, Any]] = []
    for _, group in groups.items():
        decided = group["approved"] + group["rejected"]
        metrics.append({
            "service": group["service"],
            "action": group["action"],
            "risk_level": group["risk_level"],
            "rule": group["rule"],
            "approved": group["approved"],
            "rejected": group["rejected"],
            "expired": group["expired"],
            "total": decided + group["expired"],
            "approval_rate": round(group["approved"] / decided, 4) if decided else 0.0,
            "median_latency_seconds": round(median(group["latencies"]), 1) if group["latencies"] else None,
        })
    metrics.sort(key=lambda m: (m["service"], m["action"], m["risk_level"], m["rule"]))
    return metrics


def _rule_for_record(metadata: Any) -> str:
    if not isinstance(metadata, dict):
        return "none"
    grant_id = metadata.get("standing_grant_id")
    if grant_id is not None:
        return f"grant:{grant_id}"
    return "none"


def rollup_approval_telemetry(window_days: Optional[int] = None, now=None) -> Dict[str, Any]:
    """Aggregate approval rows into per-user cached metrics + rule candidates."""
    from django.core.cache import cache

    from workflows.models import WorkflowApprovalRecord

    now = now or timezone.now()
    window = int(window_days or _window_days())
    since = now - timezone.timedelta(days=window)

    rows = (
        WorkflowApprovalRecord.objects.filter(created_at__gte=since)
        .exclude(status="pending")
        .values(
            "requested_by_id", "service", "action", "status",
            "created_at", "reviewed_at", "metadata",
        )
    )

    by_user: Dict[int, List[Dict[str, Any]]] = {}
    for row in rows:
        by_user.setdefault(row["requested_by_id"], []).append({
            "service": row["service"],
            "action": row["action"],
            "status": row["status"],
            "rule": _rule_for_record(row["metadata"]),
            "created_at": row["created_at"],
            "reviewed_at": row["reviewed_at"],
        })

    threshold_rate = _promote_rate()
    threshold_count = _promote_count()
    users = 0
    candidates = 0
    for user_id, records in by_user.items():
        metrics = compute_approval_metrics(records)
        cache.set(METRICS_CACHE_KEY.format(user_id=user_id), metrics, _CACHE_TTL_SECONDS)

        user_candidates = [
            {
                "action": metric["action"],
                "service": metric["service"],
                "risk_level": metric["risk_level"],
                "rule": metric["rule"],
                "approval_rate": metric["approval_rate"],
                "total_decided": metric["approved"] + metric["rejected"],
            }
            for metric in metrics
            if metric["rule"] == "none"
            and metric["approved"] + metric["rejected"] >= threshold_count
            and metric["approval_rate"] >= threshold_rate
        ]
        cache.set(CANDIDATES_CACHE_KEY.format(user_id=user_id), user_candidates, _CACHE_TTL_SECONDS)
        users += 1
        candidates += len(user_candidates)

    return {
        "window_days": window,
        "users": users,
        "rows": len(rows),
        "candidates": candidates,
    }


def user_metrics(user_id: Optional[int]) -> List[Dict[str, Any]]:
    if not user_id:
        return []
    try:
        from django.core.cache import cache

        return list(cache.get(METRICS_CACHE_KEY.format(user_id=user_id)) or [])
    except Exception:
        return []


def user_rule_candidates(user_id: Optional[int]) -> List[Dict[str, Any]]:
    if not user_id:
        return []
    try:
        from django.core.cache import cache

        return list(cache.get(CANDIDATES_CACHE_KEY.format(user_id=user_id)) or [])
    except Exception:
        return []


def approval_digest_lines(user_id: Optional[int]) -> List[str]:
    """Compact digest lines from cached approval metrics and candidates."""
    lines: List[str] = []
    for metric in user_metrics(user_id)[:5]:
        lines.append(
            f"- {metric['service']}.{metric['action']} ({metric['risk_level']}, "
            f"{metric['rule']}): {metric['approved']}/{metric['approved'] + metric['rejected']} "
            f"approved"
            + (f", median {metric['median_latency_seconds']}s" if metric["median_latency_seconds"] is not None else "")
        )
    for candidate in user_rule_candidates(user_id)[:3]:
        lines.append(
            f"- Consider a standing rule for {candidate['service']}.{candidate['action']}: "
            f"{int(candidate['approval_rate'] * 100)}% approved over {candidate['total_decided']} requests."
        )
    return lines
