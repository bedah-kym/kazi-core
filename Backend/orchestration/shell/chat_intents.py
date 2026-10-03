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
_NEGATORS = (
    r"(?:no|not|never|don'?t|do not|doesn'?t|won'?t|disable[d]?|"
    r"stop|turn(?:ed)?\s+off|without)"
)
_ALWAYS_ALLOW_PHRASES = r"(?:always allow|always yes|always run|auto[- ]?approve)"
_AUTOPILOT_PHRASES = (
    r"(?:auto[- ]?pilot|take over|go autonomous|auto[- ]?run|"
    r"run on your own|handle it yourself)"
)
_ALWAYS_ALLOW_NEGATED = re.compile(
    rf"\b{_NEGATORS}\b[^.!?;]{{0,24}}?\b{_ALWAYS_ALLOW_PHRASES}\b",
    re.IGNORECASE,
)
_AUTOPILOT = re.compile(rf"\b{_AUTOPILOT_PHRASES}\b", re.IGNORECASE)
_AUTOPILOT_NEGATED = re.compile(
    rf"\b{_NEGATORS}\b[^.!?;]{{0,24}}?\b{_AUTOPILOT_PHRASES}\b",
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
