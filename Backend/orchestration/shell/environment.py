"""What the model is told about where shell commands run.

Sandboxed profiles always run in a Linux container, so their facts are fixed.
The unsandboxed profile runs on the sidecar's own host, which reports its
operating system and shell on ``GET /health``.
"""
from __future__ import annotations

import logging
import re
from typing import Any, Dict, Optional

import httpx
from asgiref.sync import sync_to_async
from django.conf import settings
from django.core.cache import cache

from orchestration.shell.egress import proxy_enabled
from orchestration.shell.profiles import Profile

logger = logging.getLogger(__name__)

_HOST_CACHE_KEY = "shell_environment:host"
_HOST_CACHE_SECONDS = 300
_UNKNOWN_CACHE_SECONDS = 30
_UNKNOWN = "unknown"
# The answer goes into the system prompt, so it is held to a plain label: no
# newlines, markup or sentences, whoever answered on the sidecar's address.
_PLAIN_LABEL = re.compile(r"^[A-Za-z0-9 ._()-]{1,40}$")


def _plain_label(value: Any) -> Optional[str]:
    text = value if isinstance(value, str) else ""
    return text if _PLAIN_LABEL.match(text) else None


async def _sidecar_host() -> Optional[Dict[str, str]]:
    cached = await sync_to_async(cache.get)(_HOST_CACHE_KEY)
    if cached == _UNKNOWN:
        return None
    if isinstance(cached, dict):
        return cached

    host = str(getattr(settings, "SHELL_EXEC_HOST", "127.0.0.1") or "127.0.0.1")
    port = int(getattr(settings, "SHELL_EXEC_PORT", 8765) or 8765)
    facts = None
    try:
        async with httpx.AsyncClient(timeout=2.0) as client:
            response = await client.get(f"http://{host}:{port}/health")
        reported = response.json().get("host") if response.status_code == 200 else None
        if isinstance(reported, dict):
            platform_name = _plain_label(reported.get("platform"))
            shell = _plain_label(reported.get("shell"))
            if platform_name and shell:
                facts = {"platform": platform_name, "shell": shell}
    except Exception as exc:
        logger.debug("Shell sidecar health unavailable: %s", exc)

    try:
        if facts is None:
            await sync_to_async(cache.set)(_HOST_CACHE_KEY, _UNKNOWN, _UNKNOWN_CACHE_SECONDS)
        else:
            await sync_to_async(cache.set)(_HOST_CACHE_KEY, facts, _HOST_CACHE_SECONDS)
    except Exception as exc:
        logger.debug("Shell environment cache write skipped: %s", exc)
    return facts


async def get_shell_environment(profile: Profile) -> Dict[str, Any]:
    """Facts for the system prompt. ``platform`` and ``shell`` are absent when unknown."""
    facts: Dict[str, Any] = {"profile": profile.name, "sandboxed": profile.backend != "local"}
    if profile.backend != "local":
        image = _plain_label(str(getattr(settings, "SHELL_EXEC_IMAGE", "") or "").replace(":", " "))
        facts.update({
            "platform": "Linux",
            "shell": "sh",
            "image": image,
            "egress_proxy": proxy_enabled(),
        })
        return facts
    facts["taint_minutes"] = max(0, int(getattr(settings, "AGENT_TAINT_TTL_SECONDS", 900) or 0)) // 60
    host = await _sidecar_host()
    if host:
        facts.update(host)
    return facts
