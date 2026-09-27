"""Isolation profiles for the governed shell (roadmap §3).

Three profiles decide the backend, user, filesystem, network, and escalation
envelope. The sandbox — not the classifier — is the security boundary:

- ``open``     — direct subprocess on the shell host. Full power, all access.
                 Disposable boxes only; never the default.
- ``standard`` — Docker, non-root, read-only rootfs, network off by default.
                 The default on a fresh install.
- ``locked``   — Docker, read-only, no writable workspace, network none.
                 For a machine with real data.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Dict, Optional

logger = logging.getLogger(__name__)

DEFAULT_PROFILE = "standard"
PROFILE_PREF_KEY = "kazi:shell:profile:{room_id}"


@dataclass(frozen=True)
class Profile:
    name: str
    backend: str  # "docker" | "local"
    user: str  # container user, or "host" for the local backend
    rootfs_readonly: bool
    writable: str  # "workspace" | "full" | "none"
    network: str  # "none" | "allowlist" | "full"
    escalation: Dict[str, str]  # command category -> tier
    description: str = ""
    allow_root: bool = False


PROFILES: Dict[str, Profile] = {
    "open": Profile(
        name="open",
        backend="local",
        user="host",
        rootfs_readonly=False,
        writable="full",
        network="full",
        escalation={"root": "bounded", "network": "safe", "destructive": "destructive"},
        description="Direct subprocess on the shell host. Full power — disposable boxes only.",
        allow_root=True,
    ),
    "standard": Profile(
        name="standard",
        backend="docker",
        user="65534:65534",
        rootfs_readonly=True,
        writable="workspace",
        network="none",
        escalation={"root": "denied", "network": "bounded", "destructive": "destructive"},
        description="Docker, non-root, read-only rootfs, network off by default. The default.",
    ),
    "locked": Profile(
        name="locked",
        backend="docker",
        user="65534:65534",
        rootfs_readonly=True,
        writable="none",
        network="none",
        escalation={"root": "denied", "network": "denied", "destructive": "destructive"},
        description="Docker, read-only, no writable workspace, network none. Real-data machines.",
    ),
}


def get_profile(name: Optional[str]) -> Profile:
    """Return the named profile, falling back to ``standard`` for unknown names."""
    return PROFILES.get(str(name or "").strip().lower(), PROFILES[DEFAULT_PROFILE])


def default_profile_name() -> str:
    """The configured global default profile, normalized (unknown -> standard)."""
    try:
        from django.conf import settings
        configured = str(getattr(settings, "SHELL_EXEC_PROFILE", DEFAULT_PROFILE) or DEFAULT_PROFILE)
    except Exception:
        return DEFAULT_PROFILE
    return configured.lower() if configured.lower() in PROFILES else DEFAULT_PROFILE


def shell_profile_pref_key(room_id) -> str:
    return PROFILE_PREF_KEY.format(room_id=room_id)


def room_profile_override(room_id) -> Optional[str]:
    """Read the per-room profile override, fail-closed to ``None`` on any error."""
    if not room_id:
        return None
    try:
        from django.core.cache import cache
        value = cache.get(shell_profile_pref_key(room_id))
    except Exception:
        logger.warning("Shell profile lookup failed for room %s; using the default.", room_id)
        return None
    if value and str(value).lower() in PROFILES:
        return str(value).lower()
    return None


def resolve_profile(room_id=None, preferences: Optional[Dict] = None) -> Profile:
    """Per-room override (preferences dict, then Redis) else the global default.

    Any lookup error falls back to the global default — never to ``open``.
    """
    if isinstance(preferences, dict):
        pref = preferences.get("shell_profile")
        if pref and str(pref).lower() in PROFILES:
            return PROFILES[str(pref).lower()]
    override = room_profile_override(room_id)
    if override:
        return PROFILES[override]
    return PROFILES[default_profile_name()]


def warn_if_open_profile() -> bool:
    """Log a loud banner when the global default profile is ``open``.

    Returns True when the warning fired. Called from the app ready() hook.
    """
    if default_profile_name() != "open":
        return False
    banner = (
        "================================================================\n"
        "  SHELL_EXEC_PROFILE=open  —  the shell runs UNSANDBOXED directly on\n"
        "  this host with full network and filesystem access. This is for a\n"
        "  disposable box only; DO NOT run it on a machine with real data.\n"
        "  Set SHELL_EXEC_PROFILE=standard (or locked) to sandbox it.\n"
        "================================================================"
    )
    logger.warning(banner)
    return True
