"""Skill registry — discover and load instruction packs from the skills/ directory.

A skill is a folder containing a SKILL.md file with a minimal frontmatter block:

    ---
    name: web-scraper
    description: Extract structured data from a public webpage
    tools: search_info
    stage: active        # staging | review | active | stale | archived
    pinned: false        # pinned skills are never archived by the curator
    ---
    ...instructions...

Only ``active`` skills are exposed to the agent. The v0.7 lifecycle (#138)
adds ``stale``/``archived`` and a curator that may only demote — promotion to
``active`` always requires a human calling ``transition_skill``.

Filesystem-first (mirrors the ``examples/connectors/`` philosophy) — no DB, no
new dependencies.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone as datetime_timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from django.conf import settings
from django.utils import timezone

logger = logging.getLogger(__name__)

_VALID_STAGES = {"staging", "review", "active", "stale", "archived"}

# Human-controlled transitions. Promotion runs staging -> review -> active;
# demotion is reversible (active -> stale -> active); archived skills come back
# to stale for re-review, never straight to active.
_TRANSITIONS = {
    "staging": {"review", "archived"},
    "review": {"active", "staging", "archived"},
    "active": {"stale", "archived"},
    "stale": {"active", "archived"},
    "archived": {"stale"},
}


def skills_root() -> Optional[Path]:
    """Locate the skills directory: SKILLS_DIR setting, else <repo_root>/skills."""
    configured = getattr(settings, "SKILLS_DIR", None)
    if configured:
        return Path(configured)
    candidate = Path(__file__).resolve().parents[2] / "skills"
    return candidate if candidate.is_dir() else None


def _parse_frontmatter(raw: str) -> Dict[str, str]:
    """Parse a minimal 'key: value' frontmatter block between --- markers."""
    if not raw.startswith("---"):
        return {}
    parts = raw.split("---", 2)
    if len(parts) < 2:
        return {}
    meta: Dict[str, str] = {}
    for line in parts[1].splitlines():
        if ":" not in line:
            continue
        key, _, value = line.partition(":")
        key = key.strip().lower()
        value = value.strip().strip('"').strip("'")
        if key:
            meta[key] = value
    return meta


def _body(raw: str) -> str:
    if raw.startswith("---"):
        parts = raw.split("---", 2)
        if len(parts) >= 3:
            return parts[2].strip()
    return raw.strip()


def _read_skill(path: Path) -> Optional[Dict[str, Any]]:
    skill_md = path / "SKILL.md"
    if not skill_md.is_file():
        return None
    try:
        raw = skill_md.read_text(encoding="utf-8")
    except Exception as exc:
        logger.warning("Skill %s unreadable: %s", path.name, exc)
        return None

    meta = _parse_frontmatter(raw)
    name = (meta.get("name") or path.name).strip().lower()
    stage = (meta.get("stage") or "staging").strip().lower()
    if stage not in _VALID_STAGES:
        stage = "staging"
    raw_tools = (meta.get("tools") or "").strip()
    if raw_tools and raw_tools not in ("[]",):
        tools = [t.strip() for t in raw_tools.split(",") if t.strip()]
    else:
        tools = []
    pinned = (meta.get("pinned") or "").strip().lower() in ("true", "yes", "1")
    return {
        "name": name,
        "description": (meta.get("description") or "").strip(),
        "tools": tools,
        "stage": stage,
        "pinned": pinned,
        "body": _body(raw),
        "path": str(path),
    }


def discover_skills(include_inactive: bool = False) -> List[Dict[str, Any]]:
    """Scan the skills directory. Active-only unless include_inactive is set."""
    root = skills_root()
    if root is None:
        return []
    skills: List[Dict[str, Any]] = []
    for sub in sorted(p for p in root.iterdir() if p.is_dir()):
        if sub.name.startswith(".") or sub.name.startswith("_"):
            continue
        skill = _read_skill(sub)
        if skill is None:
            continue
        if include_inactive or skill["stage"] == "active":
            skills.append(skill)
    return skills


def list_skills() -> List[Dict[str, Any]]:
    """Metadata for active skills (exposed via the list_skills meta-tool)."""
    return [
        {
            "name": s["name"],
            "description": s["description"],
            "tools": s["tools"],
        }
        for s in discover_skills()
    ]


def get_skill(name: str) -> Optional[Dict[str, Any]]:
    """Full skill record for an active skill, or None."""
    target = (name or "").strip().lower()
    for skill in discover_skills():
        if skill["name"] == target:
            return skill
    return None


def load_skill_for_agent(name: str) -> Dict[str, Any]:
    """Return an instruction payload safe to inject into the agent's context."""
    skill = get_skill(name)
    if skill is None:
        return {"status": "error", "message": f"Unknown or inactive skill: {name}"}

    max_chars = int(getattr(settings, "SKILL_MAX_CHARS", 8000))
    body = skill["body"]
    if len(body) > max_chars:
        body = body[:max_chars] + "\n...[skill truncated]"

    return {
        "status": "success",
        "skill": skill["name"],
        "description": skill["description"],
        "tools": skill["tools"],
        "instructions": body,
    }


def _find_skill(name: str) -> Optional[Dict[str, Any]]:
    target = (name or "").strip().lower()
    for skill in discover_skills(include_inactive=True):
        if skill["name"] == target:
            return skill
    return None


def _set_frontmatter_value(raw: str, key: str, value: str) -> str:
    lines = raw.splitlines()
    if not lines or lines[0].strip() != "---":
        return raw
    end = next((index for index in range(1, len(lines)) if lines[index].strip() == "---"), None)
    if end is None:
        return raw
    for index in range(1, end):
        if lines[index].split(":", 1)[0].strip().lower() == key:
            lines[index] = f"{key}: {value}"
            break
    else:
        lines.insert(end, f"{key}: {value}")
    return "\n".join(lines) + ("\n" if raw.endswith("\n") else "")


def transition_skill(name: str, to_stage: str, *, reason: str = "") -> Dict[str, Any]:
    """Move a skill through the lifecycle. Human-only; the curator never promotes."""
    to_stage = (to_stage or "").strip().lower()
    if to_stage not in _VALID_STAGES:
        return {"status": "error", "message": f"Unknown stage: {to_stage}"}

    skill = _find_skill(name)
    if skill is None:
        return {"status": "error", "message": f"Unknown skill: {name}"}
    if skill["stage"] == to_stage:
        return {"status": "success", "skill": skill["name"], "stage": to_stage, "changed": False}
    if to_stage not in _TRANSITIONS.get(skill["stage"], set()):
        return {
            "status": "error",
            "message": f"Illegal transition {skill['stage']} -> {to_stage}",
        }
    if to_stage == "archived" and skill.get("pinned"):
        return {"status": "error", "message": f"Skill '{skill['name']}' is pinned and cannot be archived."}

    skill_md = Path(skill["path"]) / "SKILL.md"
    try:
        raw = skill_md.read_text(encoding="utf-8")
    except OSError as exc:
        return {"status": "error", "message": f"Could not read skill: {exc}"}
    skill_md.write_text(_set_frontmatter_value(raw, "stage", to_stage), encoding="utf-8")
    logger.info("Skill %s: %s -> %s (%s)", skill["name"], skill["stage"], to_stage, reason or "manual")
    return {"status": "success", "skill": skill["name"], "stage": to_stage, "changed": True}


def set_skill_pinned(name: str, pinned: bool) -> Dict[str, Any]:
    """Pin/unpin a skill. Pinned skills are never archived by the curator."""
    skill = _find_skill(name)
    if skill is None:
        return {"status": "error", "message": f"Unknown skill: {name}"}
    skill_md = Path(skill["path"]) / "SKILL.md"
    try:
        raw = skill_md.read_text(encoding="utf-8")
    except OSError as exc:
        return {"status": "error", "message": f"Could not read skill: {exc}"}
    skill_md.write_text(
        _set_frontmatter_value(raw, "pinned", "true" if pinned else "false"),
        encoding="utf-8",
    )
    return {"status": "success", "skill": skill["name"], "pinned": bool(pinned)}


def _skill_last_loaded() -> Dict[str, datetime]:
    from orchestration.telemetry import telemetry_path

    usage: Dict[str, datetime] = {}
    try:
        with open(telemetry_path(), "r", encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                try:
                    import json

                    event = json.loads(line)
                except (ValueError, TypeError):
                    continue
                if not isinstance(event, dict) or event.get("event") != "skill_loaded":
                    continue
                name = str(event.get("skill") or "").strip().lower()
                ts = event.get("ts")
                if not name or not ts:
                    continue
                try:
                    parsed = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
                except ValueError:
                    continue
                if name not in usage or parsed > usage[name]:
                    usage[name] = parsed
    except OSError:
        return {}
    return usage


def curate_skills(*, dry_run: bool = False, now=None, usage: Optional[Dict[str, datetime]] = None) -> Dict[str, List[str]]:
    """Demote unused active skills to stale, then stale ones to archived.

    The curator can only demote. It never promotes, never touches ``staging``
    or ``review``, and never archives a pinned skill. ``dry_run=True`` reports
    the proposal without writing anything.
    """
    now = now or timezone.now()
    usage = _skill_last_loaded() if usage is None else usage
    stale_after = int(getattr(settings, "SKILL_STALE_AFTER_DAYS", 90) or 90)
    archive_after = int(getattr(settings, "SKILL_ARCHIVE_AFTER_DAYS", 180) or 180)

    result: Dict[str, List[str]] = {"stale": [], "archived": [], "skipped_pinned": []}
    for skill in discover_skills(include_inactive=True):
        if skill["stage"] not in {"active", "stale"}:
            continue
        target = "stale" if skill["stage"] == "active" else "archived"
        window_days = stale_after if target == "stale" else archive_after
        last_used = usage.get(skill["name"])
        if last_used is None:
            try:
                last_used = datetime.fromtimestamp(
                    (Path(skill["path"]) / "SKILL.md").stat().st_mtime,
                    tz=datetime_timezone.utc,
                )
            except OSError:
                continue
        if last_used > now - timedelta(days=window_days):
            continue
        if skill.get("pinned"):
            result["skipped_pinned"].append(skill["name"])
            continue
        result[target].append(skill["name"])
        if not dry_run:
            transition_skill(skill["name"], target, reason=f"curator: unused for {window_days}d")
    return result
