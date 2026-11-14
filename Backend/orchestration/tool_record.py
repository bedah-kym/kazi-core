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
# (the email/phone patterns backtrack). Above this length only the kept head
# and tail are redacted; the middle is omitted and never shown.
_REDACT_WHOLE_MAX = 100_000
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
    length = len(value)
    marker_bound = len(_SHORTENED.format(length=length, omitted=length))
    keep = max(0, RECORD_STRING_MAX - marker_bound)
    while keep > 0:
        head = keep // 2
        tail = keep - head
        marker = _SHORTENED.format(length=length, omitted=length - keep)
        head_text = redact_sensitive_text(value[:head])
        tail_text = redact_sensitive_text(value[-tail:]) if tail else ""
        result = head_text + marker + tail_text
        if len(result) <= RECORD_STRING_MAX:
            return result
        keep -= max(1, len(result) - RECORD_STRING_MAX)
    return _SHORTENED.format(length=length, omitted=length).strip()[
        :RECORD_STRING_MAX
    ]


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
