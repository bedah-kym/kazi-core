"""Per-channel delivery capabilities (modality flags).

A channel declares what it can carry; modality degrades gracefully to text
rather than failing. This is the "connector + flags" half of v0.6 §4.2 — there
is no modality-selection pipeline stage.
"""
from __future__ import annotations

from typing import Any, Dict

_DEFAULT: Dict[str, Any] = {
    "supports_voice": False,
    "supports_image": False,
    "max_audio_bytes": 0,
}

CHANNEL_CAPABILITIES: Dict[str, Dict[str, Any]] = {
    "in_app": {"supports_voice": True, "supports_image": True, "max_audio_bytes": 5_000_000},
    "email": {"supports_voice": False, "supports_image": True, "max_audio_bytes": 0},
    "whatsapp": {"supports_voice": True, "supports_image": True, "max_audio_bytes": 16_000_000},
    "telegram": {"supports_voice": True, "supports_image": True, "max_audio_bytes": 16_000_000},
}


def channel_capabilities(channel: str) -> Dict[str, Any]:
    """Return the capability flags for a channel (unknown -> no audio)."""
    return dict(CHANNEL_CAPABILITIES.get(str(channel or "").lower(), _DEFAULT))


def plan_voice_delivery(channel: str, audio_bytes: int, text: str = "") -> Dict[str, Any]:
    """Decide voice vs text+note for a channel.

    Returns ``{"mode": "voice"|"text", "note": str, "text": str}``. A channel
    without voice, or audio over its cap, degrades to text with a note.
    """
    caps = channel_capabilities(channel)
    label = channel or "This channel"
    if not caps["supports_voice"]:
        return {
            "mode": "text",
            "note": f"{label} can't carry audio; sending the words as text instead.",
            "text": text,
        }
    if audio_bytes and audio_bytes > int(caps["max_audio_bytes"]):
        return {
            "mode": "text",
            "note": "The audio is too large for this channel; sending the words as text instead.",
            "text": text,
        }
    return {"mode": "voice", "note": "", "text": text}
