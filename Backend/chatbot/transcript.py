"""Turn stored chat messages into model-ready history.

Roles come from who sent a message, never from its text, and a message stays
whole however many lines or colons it contains.
"""
from typing import Dict, Iterable, List, Optional, Tuple

BOT_USERNAME = "kazi"
_WAKE_PREFIX = "@kazi"


def build_history_messages(
    rows: Iterable[Tuple[int, str, str]],
    *,
    exclude_message_id: Optional[int] = None,
    multi_user: bool = False,
) -> List[Dict[str, str]]:
    """Build ``[{"role", "content"}]`` from ``(message_id, username, content)`` rows, oldest first.

    The history starts with a user message and never has two neighbours with
    the same role, which is what the model APIs expect.
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
            if text.lower().startswith(_WAKE_PREFIX):
                text = text[len(_WAKE_PREFIX):].strip()
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
    return messages
