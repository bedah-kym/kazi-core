"""Sanitised tool-call input for the record replayed in later turns.

A record is not a receipt: it is shown back to the model as its own earlier
call. When the record cuts a value for size, the cut must say so, or the model
reads it as the content the call actually sent and may try to repair something
that was never broken (C1). Restricted keys and secret patterns are still
removed without a marker; only the length cut is announced.
"""
from typing import Any, Dict

from orchestration.security_policy import redact_sensitive_text, sanitize_parameters

RECORD_STRING_LIMIT = 160
RECORD_STRING_MAX = RECORD_STRING_LIMIT + 80
_MAX_RECORD_LIST_ITEMS = 10
# Running the secret/PII regexes over a multi-megabyte value is pathological
# (the email/phone patterns backtrack). Above this length only a redacted head
# is kept; the rest is omitted and never shown.
_REDACT_WHOLE_MAX = 100_000
# The head is redacted in a window this much wider than the kept text, and the
# window grows by this much until the kept head stops changing.
_HUGE_MARGIN = 4096
# Bound on the growth: a head still changing after this many steps is dropped.
_HUGE_WIDEN_STEPS = 8
_SHORTENED = (
    " ... [record shortened: the call sent {length} characters; "
    "{omitted:,} omitted] ... "
)


def _shorten_redacted(text: str) -> str:
    length = len(text)
    if length <= RECORD_STRING_MAX:
        return text
    # Size the kept text from the real marker so the whole result is never over
    # RECORD_STRING_MAX. Normal values still keep RECORD_STRING_LIMIT real
    # characters; at extreme lengths the marker itself needs the room.
    marker_bound = len(_SHORTENED.format(length=length, omitted=length))
    keep = max(0, RECORD_STRING_MAX - marker_bound)
    head = keep // 2
    tail = keep - head
    marker = _SHORTENED.format(length=length, omitted=length - keep)
    return text[:head] + marker + text[-tail:]


def _stable_redacted_head(value: str, head: int) -> str:
    """The first ``head`` characters of the redacted text, without redacting all of it.

    The window starts at the text start, so every pattern's key or scheme before
    a kept value is inside it. Redaction can shrink the text (a long
    ``password=...`` becomes one short token), so the kept head can reach text
    near the window end, where a secret may be cut and only half-matched. The
    window therefore grows by ``_HUGE_MARGIN`` until the kept head is the same
    for two windows in a row: a secret cut by the smaller window is then inside
    the larger one and has been redacted. A head that never settles is dropped.

    Known bound: a match whose deciding part lies more than ``_HUGE_MARGIN``
    past both windows (an email "local part" over 4096 characters) is not seen.
    """
    length = len(value)
    window = min(length, head + _HUGE_MARGIN)
    current = redact_sensitive_text(value[:window])[:head]
    for _ in range(_HUGE_WIDEN_STEPS):
        if window >= length:
            return current
        window = min(length, window + _HUGE_MARGIN)
        wider = redact_sensitive_text(value[:window])[:head]
        if wider == current:
            return current
        current = wider
    return ""


def _shorten_huge(value: str) -> str:
    """Keep only a redacted head of a value too long to redact whole.

    A tail has no safe window: ``Bearer``, ``key = `` and the email local part
    reach arbitrarily far to the left of any kept tail, so it is dropped.
    """
    length = len(value)
    marker_bound = len(_SHORTENED.format(length=length, omitted=length))
    head = max(0, RECORD_STRING_MAX - marker_bound)
    marker = _SHORTENED.format(length=length, omitted=length - head)
    return _stable_redacted_head(value, head) + marker.rstrip()


def _record_string(value: str) -> str:
    if len(value) > _REDACT_WHOLE_MAX:
        return _shorten_huge(value)
    return _shorten_redacted(redact_sensitive_text(value))


def _record_item(value: Any) -> Any:
    if isinstance(value, str):
        return _record_string(value)
    if isinstance(value, (int, float, bool)) or value is None:
        return value
    return _record_string(str(value))


def _record_value(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: _record_value(nested) for key, nested in value.items()}
    if isinstance(value, list):
        return [_record_item(item) for item in value[:_MAX_RECORD_LIST_ITEMS]]
    return _record_item(value)


def tool_record_input(raw_input: Any) -> Dict[str, Any]:
    """The call's input as it may be stored and replayed, with cuts announced."""
    if not isinstance(raw_input, dict):
        return {}
    return _record_value(sanitize_parameters(raw_input))
