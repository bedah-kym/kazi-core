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
# Politeness around the exact phrase is still consent ("sure, autopilot",
# "autopilot please"). Anything else in the message is not.
_LEAD = re.compile(r"^(?:yes|yeah|yep|ok|okay|sure|please)[\s,.!]+")
_TAIL = re.compile(r"[\s,]+(?:please|pls|thanks)$")


def _normalized(text: Any) -> str:
    value = _TRIM.sub("", " ".join(str(text or "").lower().split()))
    return _TAIL.sub("", _LEAD.sub("", value))


def is_autopilot_request(text: Any) -> bool:
    return _normalized(text) in _ARM


def is_autopilot_disarm_request(text: Any) -> bool:
    return _normalized(text) in _DISARM
