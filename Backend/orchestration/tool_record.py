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
_SHORTENED = "... [record shortened: the call sent {length} characters]"


def _record_string(value: str) -> str:
    original_length = len(value)
    text = redact_sensitive_text(value)
    if len(text) <= RECORD_STRING_LIMIT:
        return text
    return text[:RECORD_STRING_LIMIT].rstrip() + _SHORTENED.format(length=original_length)


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
