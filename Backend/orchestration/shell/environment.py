"""What the model is told about where shell commands run.

Sandboxed profiles always run in a Linux container, so their facts are fixed.
The unsandboxed profile runs on the sidecar's own host, which reports its
operating system and shell on ``GET /health``.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, Optional

import httpx
from asgiref.sync import sync_to_async
from django.conf import settings
from django.core.cache import cache

logger = logging.getLogger(__name__)

_HOST_CACHE_KEY = "shell_environment:host"
_HOST_CACHE_SECONDS = 300
_UNREACHABLE_CACHE_SECONDS = 30
_UNREACHABLE = "unreachable"


async def _sidecar_host() -> Optional[Dict[str, str]]:
    cached = await sync_to_async(cache.get)(_HOST_CACHE_KEY)
    if cached == _UNREACHABLE:
        return None
    if isinstance(cached, dict):
        return cached

    host = str(getattr(settings, "SHELL_EXEC_HOST", "127.0.0.1") or "127.0.0.1")
    port = int(getattr(settings, "SHELL_EXEC_PORT", 8765) or 8765)
    facts = None
    try:
        async with httpx.AsyncClient(timeout=2.0) as client:
            response = await client.get(f"http://{host}:{port}/health")
        reported = response.json().get("host")
        if isinstance(reported, dict) and reported.get("platform"):
            facts = {
                "platform": str(reported.get("platform"))[:60],
                "shell": str(reported.get("shell") or "unknown")[:40],
            }
    except Exception as exc:
        logger.debug("Shell sidecar health unavailable: %s", exc)

    if facts is None:
        await sync_to_async(cache.set)(_HOST_CACHE_KEY, _UNREACHABLE, _UNREACHABLE_CACHE_SECONDS)
        return None
    await sync_to_async(cache.set)(_HOST_CACHE_KEY, facts, _HOST_CACHE_SECONDS)
    return facts


async def get_shell_environment(profile) -> Optional[Dict[str, Any]]:
    """Facts for the system prompt, or ``None`` when the shell cannot be described."""
    facts: Dict[str, Any] = {
        "profile": profile.name,
        "network": profile.network,
        "writable": profile.writable,
    }
    if profile.backend != "local":
        facts.update({"sandboxed": True, "platform": "Linux", "shell": "sh"})
        return facts
    host = await _sidecar_host()
    if host is None:
        return None
    facts.update({"sandboxed": False, **host})
    return facts
