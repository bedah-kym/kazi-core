"""Chat phrases that turn a shell confirmation into a standing decision.

Kept deliberately narrow and separate from the generic confirmation detector:
these only count when a confirmation is already pending, and they lower a
prompt *only* through the same deterministic gates as the management command.
"""
from __future__ import annotations

import re
from typing import Any

_ALWAYS_ALLOW = re.compile(
    r"\b(?:always allow|always yes|always run|"
    r"don'?t ask(?: me)? again|stop asking(?: me)?|"
    r"never ask(?: me)?(?: again)?|auto[- ]?approve)\b",
    re.IGNORECASE,
)
_ALWAYS_ALLOW_NEGATED = re.compile(
    r"\b(?:no|not|don'?t|do not|never)\s+(?:always allow|always yes|always run|auto[- ]?approve)\b",
    re.IGNORECASE,
)
_AUTOPILOT = re.compile(
    r"\b(?:auto[- ]?pilot|take over|go autonomous|auto[- ]?run|"
    r"run on your own|handle it yourself)\b",
    re.IGNORECASE,
)
_AUTOPILOT_NEGATED = re.compile(
    r"\b(?:no|not|disable|turn off|stop)\s+(?:the\s+)?auto[- ]?pilot\b",
    re.IGNORECASE,
)


def is_always_allow_request(text: Any) -> bool:
    value = str(text or "")
    if _ALWAYS_ALLOW_NEGATED.search(value):
        return False
    return bool(_ALWAYS_ALLOW.search(value))


def is_autopilot_request(text: Any) -> bool:
    value = str(text or "")
    if _AUTOPILOT_NEGATED.search(value):
        return False
    return bool(_AUTOPILOT.search(value))
