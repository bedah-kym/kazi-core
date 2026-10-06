"""Turn stored chat messages into model-ready history.

Roles come from who sent a message, never from its text, and a message stays
whole however many lines or colons it contains.
"""
import re
from typing import Dict, Iterable, List, Optional, Tuple

BOT_USERNAME = "kazi"
_WAKE_WORD = re.compile(r"^@kazi(?=$|[\s,:;.!?])[\s,:;]*", re.IGNORECASE)
_TRIMMED = "\n[... middle of this message trimmed for length ...]\n"

Row = Tuple[int, str, str]


def strip_wake_word(text: str) -> Optional[str]:
    """The message without its leading ``@kazi``, or None when it is not addressed to the bot."""
    match = _WAKE_WORD.match(text or "")
    if match is None:
        return None
    return text[match.end():].strip()


def has_several_speakers(rows: Iterable[Row]) -> bool:
    """True when more than one person wrote in ``rows``."""
    speakers = {(username or "").lower() for _, username, _ in rows}
    speakers.discard(BOT_USERNAME)
    return len(speakers) > 1


def _shorten(text: str, limit: int) -> str:
    """``text`` cut to about ``limit`` characters, keeping its start and its end."""
    room = max(limit - len(_TRIMMED), 2)
    if len(text) <= room + len(_TRIMMED):
        return text
    head = room // 2
    return text[:head] + _TRIMMED + text[len(text) - (room - head):]


def _fit(messages: List[Dict[str, str]], max_chars: int) -> None:
    """Drop the oldest exchanges until the rest fits. The last exchange always stays:
    when it is too long on its own, its longest message loses its middle instead."""
    total = sum(len(m["content"]) for m in messages)
    while total > max_chars and any(m["role"] == "user" for m in messages[1:]):
        total -= len(messages.pop(0)["content"])
        while messages[0]["role"] == "assistant":
            total -= len(messages.pop(0)["content"])
    for message in sorted(messages, key=lambda m: -len(m["content"])):
        if total <= max_chars:
            break
        before = len(message["content"])
        message["content"] = _shorten(message["content"], before - (total - max_chars))
        total -= before - len(message["content"])


def build_history_messages(
    rows: Iterable[Row],
    *,
    exclude_message_id: Optional[int] = None,
    multi_user: bool = False,
    max_chars: Optional[int] = None,
) -> List[Dict[str, str]]:
    """Build ``[{"role", "content"}]`` from ``(message_id, username, content)`` rows, oldest first.

    The history starts with a user message and never has two neighbours with
    the same role, which is what the model APIs expect. ``max_chars`` drops the
    oldest exchanges until the rest fits and never drops the last one.
    """
    messages: List[Dict[str, str]] = []
    for message_id, username, content in rows:
        if exclude_message_id is not None and message_id == exclude_message_id:
            continue
        text = (content or "").strip()
        if (username or "").lower() == BOT_USERNAME:
            role = "assistant"
        else:
            role = "user"
            addressed = strip_wake_word(text)
            if addressed is not None:
                text = addressed
            if text and multi_user:
                text = f"{username}: {text}"
        if not text:
            continue
        if messages and messages[-1]["role"] == role:
            messages[-1]["content"] += "\n\n" + text
        else:
            messages.append({"role": role, "content": text})

    while messages and messages[0]["role"] == "assistant":
        messages.pop(0)
    if max_chars is not None:
        _fit(messages, max_chars)
    return messages
