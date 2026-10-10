"""Turn stored chat messages into model-ready history.

Roles come from who sent a message, never from its text, and a message stays
whole however many lines or colons it contains. A reply that carries tool
records is replayed in the model's native tool format; text the harness wrote
is replayed as a note, never as something the assistant said.
"""
import json
import re
from typing import Any, Dict, Iterable, List, Optional, Tuple

BOT_USERNAME = "kazi"
_WAKE_WORD = re.compile(r"^@kazi(?=$|[\s,:;.!?])[\s,:;]*", re.IGNORECASE)
_TRIMMED = "\n[... middle of this message trimmed for length ...]\n"

Row = Tuple


def strip_wake_word(text: str) -> Optional[str]:
    """The message without its leading ``@kazi``, or None when it is not addressed to the bot."""
    match = _WAKE_WORD.match(text or "")
    if match is None:
        return None
    return text[match.end():].strip()


def has_several_speakers(rows: Iterable[Row]) -> bool:
    """True when more than one person wrote in ``rows``."""
    speakers = {(row[1] or "").lower() for row in rows}
    speakers.discard(BOT_USERNAME)
    return len(speakers) > 1


def _shorten(text: str, limit: int) -> str:
    """``text`` cut to about ``limit`` characters, keeping its start and its end."""
    room = max(limit - len(_TRIMMED), 2)
    if len(text) <= room + len(_TRIMMED):
        return text
    head = room // 2
    return text[:head] + _TRIMMED + text[len(text) - (room - head):]


def _content_len(message: Dict[str, Any]) -> int:
    """Size of one message, measured the way the agent loop measures history.

    The loop trims history again, message by message. If it counted more than
    this does, it would cut a tool exchange in half after the fit below.
    """
    content = message.get("content")
    if isinstance(content, str):
        return len(content)
    if isinstance(content, list):
        return len(json.dumps(content, default=str))
    return len(str(content or ""))


def _as_blocks(content: Any) -> List[Dict[str, Any]]:
    if isinstance(content, list):
        return content
    if isinstance(content, str) and content:
        return [{"type": "text", "text": content}]
    return []


def _has_block(message: Dict[str, Any], kind: str) -> bool:
    content = message.get("content")
    if not isinstance(content, list):
        return False
    return any(isinstance(b, dict) and b.get("type") == kind for b in content)


def _append(messages: List[Dict[str, Any]], role: str, content: Any) -> None:
    """Add a message, merging it into the previous one when the role repeats."""
    if not content:
        return
    if messages and messages[-1]["role"] == role:
        previous = messages[-1]
        if isinstance(previous["content"], str) and isinstance(content, str):
            previous["content"] = previous["content"] + "\n\n" + content
        else:
            previous["content"] = _as_blocks(previous["content"]) + _as_blocks(content)
        return
    messages.append({"role": role, "content": content})


def _harness_note(harness: str) -> str:
    return f"[Harness: {harness}]"


def _harness_of(extra: Any) -> str:
    harness = extra.get("harness") if isinstance(extra, dict) else ""
    return harness.strip() if isinstance(harness, str) else ""


def _model_text(content: str, extra: Any) -> str:
    """What the model itself wrote in a reply.

    A reply saved with its parts stores the model's text on its own, so the
    harness's text never has to be cut out of the full reply. A reply without
    parts is all the model's.
    """
    model = extra.get("model") if isinstance(extra, dict) else None
    return model.strip() if isinstance(model, str) else content


def _assistant_messages(
    message_id: int,
    text: str,
    extra: Any,
    viewer_user_id: Optional[int],
) -> List[Tuple[str, Any]]:
    """The assistant/user messages one stored reply becomes."""
    out: List[Tuple[str, Any]] = []

    tools = extra.get("tools") if isinstance(extra, dict) else None
    replay = (
        isinstance(tools, list) and bool(tools)
        and viewer_user_id is not None
        and extra.get("by") == viewer_user_id
    )
    if replay:
        use_blocks: List[Dict[str, Any]] = []
        result_blocks: List[Dict[str, Any]] = []
        for index, record in enumerate(tools):
            tool_id = f"hist-{message_id}-{index}"
            tool_input = record.get("input") if isinstance(record, dict) else None
            use_blocks.append({
                "type": "tool_use",
                "id": tool_id,
                "name": (record.get("name") if isinstance(record, dict) else "") or "",
                "input": tool_input if isinstance(tool_input, dict) else {},
            })
            shown = (record.get("shown") if isinstance(record, dict) else "") or ""
            note = record.get("note") if isinstance(record, dict) else None
            if note:
                shown = f"{shown}\n{note}".strip()
            result_blocks.append({
                "type": "tool_result",
                "tool_use_id": tool_id,
                "content": shown,
            })
        out.append(("assistant", use_blocks))
        out.append(("user", result_blocks))

    model = _model_text(text, extra)
    if model:
        out.append(("assistant", model))
    return out


def _starts_turn(message: Dict[str, Any]) -> bool:
    """A user message that is not a tool result starts a new exchange."""
    if message["role"] != "user":
        return False
    return not _has_block(message, "tool_result")


def _turns(messages: List[Dict[str, Any]]) -> List[List[Dict[str, Any]]]:
    turns: List[List[Dict[str, Any]]] = []
    current: List[Dict[str, Any]] = []
    for message in messages:
        if _starts_turn(message) and current:
            turns.append(current)
            current = []
        current.append(message)
    if current:
        turns.append(current)
    return turns


def _strip_leading_assistant(messages: List[Dict[str, Any]]) -> None:
    """Drop a leading assistant message; a tool_use leaves only together with its result."""
    while messages and messages[0]["role"] == "assistant":
        first = messages.pop(0)
        if _has_block(first, "tool_use") and messages and _has_block(messages[0], "tool_result"):
            messages.pop(0)


def _without_tool_blocks(content: Any) -> Any:
    """``content`` without its tool calls and tool results; plain text comes back as a string."""
    if not isinstance(content, list):
        return content
    rest = [
        block for block in content
        if not (isinstance(block, dict) and block.get("type") in ("tool_use", "tool_result"))
    ]
    if all(isinstance(block, dict) and block.get("type") == "text" for block in rest):
        return "\n\n".join(block.get("text") or "" for block in rest if block.get("text"))
    return rest


def _fit(messages: List[Dict[str, Any]], max_chars: int) -> None:
    """Drop the oldest turns until the rest fits. The last turn always stays:
    when it is too long on its own, its longest plain-text message is shortened.
    A tool exchange is dropped whole, never half."""
    turns = _turns(messages)
    total = sum(_content_len(m) for m in messages)
    while total > max_chars and len(turns) > 1:
        for message in turns.pop(0):
            total -= _content_len(message)
    surviving = turns[0] if turns else []
    for message in sorted(
        (m for m in surviving if isinstance(m.get("content"), str)),
        key=lambda m: -len(m["content"]),
    ):
        if total <= max_chars:
            break
        before = len(message["content"])
        message["content"] = _shorten(message["content"], before - (total - max_chars))
        total -= before - len(message["content"])
    if total > max_chars:
        # Still too long: the tool exchanges of the last turn go, each one whole.
        # Text that shares a message with a tool result stays.
        kept: List[Dict[str, Any]] = []
        for message in surviving:
            _append(kept, message["role"], _without_tool_blocks(message["content"]))
        turns[0] = kept
    messages[:] = [message for turn in turns for message in turn]


def build_history_messages(
    rows: Iterable[Row],
    *,
    exclude_message_id: Optional[int] = None,
    multi_user: bool = False,
    max_chars: Optional[int] = None,
    viewer_user_id: Optional[int] = None,
) -> List[Dict[str, Any]]:
    """Build ``[{"role", "content"}]`` from ``(id, username, content[, extra])`` rows, oldest first.

    The history starts with a user message and never has two neighbours with
    the same role, which is what the model APIs expect. A reply ``extra`` may
    carry ``tools`` (its records), ``by`` (who started the turn), ``model``
    (what the model wrote) and ``harness`` (what the harness wrote). Records
    are replayed as native tool blocks only into a turn started by
    ``viewer_user_id``. The harness text becomes a note on the next user
    message, or a last user message of its own when the reply is the latest
    one. ``max_chars`` drops the oldest turns until the rest fits and never
    drops the last one.
    """
    messages: List[Dict[str, Any]] = []
    pending_note = ""

    for row in rows:
        message_id, username, content = row[0], row[1], row[2]
        extra = row[3] if len(row) > 3 else None
        if exclude_message_id is not None and message_id == exclude_message_id:
            continue
        text = (content or "").strip()

        if (username or "").lower() == BOT_USERNAME:
            for role, blocks in _assistant_messages(message_id, text, extra, viewer_user_id):
                _append(messages, role, blocks)
            harness = _harness_of(extra)
            if harness:
                note = _harness_note(harness)
                pending_note = f"{pending_note}\n{note}" if pending_note else note
        else:
            addressed = strip_wake_word(text)
            if addressed is not None:
                text = addressed
            if text and multi_user:
                text = f"{username}: {text}"
            if pending_note:
                text = f"{pending_note}\n{text}" if text else pending_note
                pending_note = ""
            if not text:
                continue
            _append(messages, "user", text)

    if pending_note:
        # The reply being answered is the latest one: its note goes last, right
        # before the user message the caller adds.
        _append(messages, "user", pending_note)

    _strip_leading_assistant(messages)
    if max_chars is not None:
        _fit(messages, max_chars)
    return messages
