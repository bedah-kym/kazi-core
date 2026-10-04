"""Turn stored chat messages into model-ready history.

Roles come from who sent a message, never from its text, and a message stays
whole however many lines or colons it contains.
"""
import re
from typing import Dict, Iterable, List, Optional, Tuple

BOT_USERNAME = "kazi"
_WAKE_WORD = re.compile(r"^@kazi(?=$|[\s,:;.!?])[\s,:;]*", re.IGNORECASE)

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
    oldest whole messages until the rest fits.
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

    if max_chars is not None:
        total = sum(len(m["content"]) for m in messages)
        while len(messages) > 1 and total > max_chars:
            total -= len(messages.pop(0)["content"])
    while messages and messages[0]["role"] == "assistant":
        messages.pop(0)
    return messages
