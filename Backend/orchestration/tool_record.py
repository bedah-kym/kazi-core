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
# The head is redacted in a window this much wider than the kept text, so a
# secret value that starts in the kept text has its whole minimum length inside
# the window. A match that runs past the window still ends at its edge: the
# window end satisfies ``\b`` and ends ``\S+``.
_HUGE_MARGIN = 4096
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


def _shorten_huge(value: str) -> str:
    """Keep only a redacted head of a value too long to redact whole.

    The head window starts at the text start, so every pattern's key or scheme
    before a kept value is inside it. A secret value that starts in the kept
    head has at least ``_HUGE_MARGIN`` characters of window after it, more than
    any pattern's minimum, and a match that runs past the window ends at its
    edge. A tail has no such guarantee: ``Bearer``, ``key = `` and the
    email local part reach arbitrarily far to the left of the kept text, so no
    finite window around a tail proves it safe. It is dropped instead.
    """
    length = len(value)
    marker_bound = len(_SHORTENED.format(length=length, omitted=length))
    head = max(0, RECORD_STRING_MAX - marker_bound)
    marker = _SHORTENED.format(length=length, omitted=length - head)
    head_text = redact_sensitive_text(value[: head + _HUGE_MARGIN])[:head]
    return head_text + marker.rstrip()


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
