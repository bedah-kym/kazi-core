"""Skill registry — discover and load instruction packs from the skills/ directory.

A skill is a folder containing a SKILL.md file with a minimal frontmatter block:

    ---
    name: web-scraper
    description: Extract structured data from a public webpage
    tools: search_info
    stage: active        # staging | review | active
    ---
    ...instructions...

Only ``active`` skills are exposed to the agent. Filesystem-first (mirrors the
``examples/connectors/`` philosophy) — no DB, no new dependencies.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Dict, List, Optional

from django.conf import settings

logger = logging.getLogger(__name__)

_VALID_STAGES = {"staging", "review", "active"}


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
    return {
        "name": name,
        "description": (meta.get("description") or "").strip(),
        "tools": tools,
        "stage": stage,
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
