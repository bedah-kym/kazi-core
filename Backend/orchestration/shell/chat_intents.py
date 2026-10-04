"""Chat replies that approve a pending action or arm/disarm the shell autopilot window.

Matched as the *whole message*, never as a substring or a prefix: a question
about autopilot, a refusal that mentions it, "stop asking", or "ok, but ..."
must not arm anything or confirm a pending command.
"""
from __future__ import annotations

import re
from typing import Any, Optional, Tuple

_ARM = {"autopilot", "auto pilot", "auto-pilot", "autopilot on", "arm autopilot"}
_DISARM = {"autopilot off", "disarm autopilot", "stop autopilot"}
_APPROVE = {
    "y", "yes", "yeah", "yep", "yup", "ok", "okay", "sure", "approve", "approved",
    "confirm", "confirmed", "proceed", "go ahead", "do it", "run it",
    "please go ahead", "please proceed", "please do", "please do it", "please run it",
    "\U0001F44D",
}
_DECLINE = {"n", "no", "nope", "nah", "no thanks", "no thank you", "don't", "do not", "\U0001F44E"}
_MAX_REPLY_CHARS = 300
_APPROVAL_EDGE = re.compile(r"^[\s,:;]+|[\s.!,]+$")
_APPROVAL_LEAD = re.compile(r"^(?:yes|yeah|yep|yup|ok|okay|sure)[\s,.!]+")
_APPROVAL_TAIL = re.compile(r"[\s,]+(?:please|pls|thanks|thank you)$")
_TRIM = re.compile(r"[\s.!]+$")
# Politeness around the exact phrase is still consent ("sure, autopilot",
# "autopilot please"). Anything else in the message is not.
_LEAD = re.compile(r"^(?:yes|yeah|yep|ok|okay|sure|please)[\s,.!]+")
_TAIL = re.compile(r"[\s,]+(?:please|pls|thanks)$")


def _normalized(text: Any) -> str:
    raw = str(text or "")
    if len(raw) > _MAX_REPLY_CHARS:
        return ""
    value = _TRIM.sub("", " ".join(raw.lower().split()))
    return _TAIL.sub("", _LEAD.sub("", value))


_HOST_COMMAND = re.compile(r"^(allow|approve|revoke|block) host (\S+)$")


def host_grant_request(text: Any) -> Optional[Tuple[str, str]]:
    """``("allow" | "revoke", host)`` for an exact ``allow host <name>`` reply.

    Whole-message only, like the autopilot replies: a sentence that merely
    mentions a host never changes the allowlist.
    """
    match = _HOST_COMMAND.match(_normalized(text))
    if not match:
        return None
    verb = "allow" if match.group(1) in ("allow", "approve") else "revoke"
    return verb, match.group(2).strip("`'")


def _reply_core(text: Any) -> str:
    if not isinstance(text, str) or len(text) > _MAX_REPLY_CHARS:
        return ""
    value = _APPROVAL_EDGE.sub("", " ".join(text.lower().split()))
    return _APPROVAL_TAIL.sub("", value)


def is_approval_reply(text: Any) -> bool:
    """True only when the whole reply is an approval.

    "yes", "ok.", "sure, go ahead" and "yes please go ahead" approve. A reply
    that starts with an approval word and then says something else ("ok but
    use a different folder", "ok what does that do?") is not consent.
    """
    value = _reply_core(text)
    for _ in range(4):
        if value in _APPROVE:
            return True
        rest = _APPROVAL_LEAD.sub("", value)
        if rest == value:
            return False
        value = rest
    return value in _APPROVE


def is_decline_reply(text: Any) -> bool:
    """True when the whole reply is a plain no."""
    return _reply_core(text) in _DECLINE


def is_autopilot_request(text: Any) -> bool:
    return _normalized(text) in _ARM


def is_autopilot_disarm_request(text: Any) -> bool:
    return _normalized(text) in _DISARM
