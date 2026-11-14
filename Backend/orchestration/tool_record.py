"""Sanitised tool-call input for the record replayed in later turns.

A record is not a receipt: it is shown back to the model as its own earlier
call. When the record cuts a value for size, the cut must say so, or the model
reads it as the content the call actually sent and may try to repair something
that was never broken (C1). Restricted keys and secret patterns are still
removed without a marker; only the length cut is announced.
"""
from typing import Any, Dict, Optional

from orchestration.security_policy import redact_sensitive_text, sanitize_parameters

RECORD_STRING_LIMIT = 160
RECORD_STRING_MAX = RECORD_STRING_LIMIT + 80
_MAX_RECORD_LIST_ITEMS = 10
# Running the secret/PII regexes over a multi-megabyte value is pathological
# (the email/phone patterns backtrack). Above this length only the kept head
# and tail are redacted; the middle is omitted and never shown.
_REDACT_WHOLE_MAX = 100_000
# A window wider than the kept text: any finite pattern (the phone form is the
# longest at about 20 characters) and any key/scheme token is fully inside it.
_HUGE_MARGIN = 4096
# A non-whitespace token longer than this at the window start cannot be proven
# not to be an unbounded secret value, so the kept tail is dropped.
_HUGE_TOKEN_LIMIT = 4096
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


def _tail_window_start(value: str, start: int, limit: int) -> Optional[int]:
    """Move a tail window start left to include the key/scheme before a value.

    If the window starts inside a token, its key may be immediately before it;
    walk to that token's start and then over the whitespace to the previous
    token. A token longer than ``limit`` cannot be proven not to be an
    unbounded secret value, so return None and let the caller drop the tail.
    """
    steps = 0
    if start > 0 and not value[start].isspace() and not value[start - 1].isspace():
        while start > 0 and not value[start - 1].isspace():
            start -= 1
            steps += 1
            if steps > limit:
                return None
    else:
        while start > 0 and value[start - 1].isspace():
            start -= 1
            steps += 1
            if steps > limit:
                return None
    if start > 0:
        steps = 0
        while start > 0 and not value[start - 1].isspace():
            start -= 1
            steps += 1
            if steps > limit:
                return None
    return start


def _shorten_huge(value: str) -> str:
    length = len(value)
    marker_bound = len(_SHORTENED.format(length=length, omitted=length))
    keep = max(0, RECORD_STRING_MAX - marker_bound)
    head = keep // 2
    tail = keep - head
    marker = _SHORTENED.format(length=length, omitted=length - keep)

    # Head: redact a window wider than the kept text, with a sentinel whitespace
    # that closes any unbounded match crossing the cut, then cut the result.
    head_window = value[: head + _HUGE_MARGIN]
    head_text = redact_sensitive_text(head_window + " ")[:head]

    # Tail: the window ends at the text end, so an unbounded match is closed by
    # the text itself. The window start is moved left to include the key/scheme;
    # a token too long to prove safe drops the tail (fail closed).
    tail_text = ""
    if tail:
        start = max(0, length - (tail + _HUGE_MARGIN))
        if start == 0:
            tail_text = redact_sensitive_text(value)[-tail:]
        else:
            start = _tail_window_start(value, start, _HUGE_TOKEN_LIMIT)
            if start is not None:
                tail_text = redact_sensitive_text(value[start:])[-tail:]

    result = head_text + marker + tail_text
    if len(result) > RECORD_STRING_MAX:
        overflow = len(result) - RECORD_STRING_MAX
        if tail_text:
            tail_text = tail_text[: max(0, len(tail_text) - overflow)]
            result = head_text + marker + tail_text
            overflow = len(result) - RECORD_STRING_MAX
        if overflow > 0 and head_text:
            head_text = head_text[: max(0, len(head_text) - overflow)]
            result = head_text + marker + tail_text
    return result


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
