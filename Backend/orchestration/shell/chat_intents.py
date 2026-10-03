"""Chat replies that arm or disarm the shell autopilot window.

Matched as the *whole message*, never as a substring: a question about
autopilot, a refusal that mentions it, or "stop asking" must not arm anything
or confirm a pending command.
"""
from __future__ import annotations

import re
from typing import Any

_ARM = {"autopilot", "auto pilot", "auto-pilot", "autopilot on", "arm autopilot"}
_DISARM = {"autopilot off", "disarm autopilot", "stop autopilot"}
_TRIM = re.compile(r"[\s.!]+$")


def _normalized(text: Any) -> str:
    return _TRIM.sub("", " ".join(str(text or "").lower().split()))


def is_autopilot_request(text: Any) -> bool:
    return _normalized(text) in _ARM


def is_autopilot_disarm_request(text: Any) -> bool:
    return _normalized(text) in _DISARM
