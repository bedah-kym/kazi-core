"""Text-to-speech connector (``generate_speech``).

Turns text into a spoken audio clip (a voice note). Modality is a connector
choice, not a pipeline stage (v0.6 brief §4.2). The provider is OpenAI-compatible
``/audio/speech`` behind ``OPENAI_TTS_URL``; the key is read via
``self.get_credential("OPENAI_API_KEY")``.
"""
from __future__ import annotations

import base64
import logging
from typing import Any, Dict, List

import httpx

from orchestration.base_connector import BaseConnector

logger = logging.getLogger(__name__)

_DEFAULT_TTS_URL = "https://api.openai.com/v1/audio/speech"
_DEFAULT_MODEL = "gpt-4o-mini-tts"
_DEFAULT_VOICE = "alloy"
_VALID_FORMATS = {"mp3", "opus", "aac", "flac", "wav", "pcm"}


def _setting(name: str, default: Any = None) -> Any:
    try:
        from django.conf import settings
        return getattr(settings, name, default)
    except Exception:
        return default


class TTSConnector(BaseConnector):
    name = "tts"
    version = "1.0.0"
    actions = ["generate_speech"]
    required_credentials = ["OPENAI_API_KEY"]

    def get_action_catalog_entries(self) -> List[Dict[str, Any]]:
        return [
            {
                "action": "generate_speech",
                "aliases": ["speak", "text_to_speech", "tts", "sing"],
                "service": "voice",
                "description": (
                    "Generate a spoken audio clip (voice note) from text. Prefer this "
                    "when the user asks you to sing, speak, or play something aloud."
                ),
                "params": {
                    "text": {"type": "string", "required": True, "description": "The words to speak or sing."},
                    "voice": {"type": "string", "required": False, "description": "Voice name (defaults to the configured voice)."},
                    "format": {"type": "string", "required": False, "description": "Audio format: mp3 (default), opus, wav, etc."},
                },
                "return_description": "Returns base64 audio, its format, and byte size.",
                "risk_level": "low",
                "confirmation_policy": "never",
                "capability_gate": None,
            }
        ]

    async def execute(self, parameters: Dict[str, Any], context: Dict[str, Any]) -> Dict[str, Any]:
        action = parameters.get("action", "generate_speech")
        if action != "generate_speech":
            return {"status": "error", "message": f"Unknown action: {action}"}

        text = str(parameters.get("text") or "").strip()
        if not text:
            return {"status": "error", "message": "generate_speech requires non-empty 'text'."}

        key = self.get_credential("OPENAI_API_KEY")
        if not key:
            return {"status": "error", "message": "Voice is not configured (OPENAI_API_KEY is unset)."}

        fmt = str(parameters.get("format") or "mp3").lower()
        if fmt not in _VALID_FORMATS:
            fmt = "mp3"
        voice = str(parameters.get("voice") or _setting("OPENAI_TTS_VOICE", _DEFAULT_VOICE) or _DEFAULT_VOICE)
        url = str(_setting("OPENAI_TTS_URL", _DEFAULT_TTS_URL) or _DEFAULT_TTS_URL)
        model = str(_setting("OPENAI_TTS_MODEL", _DEFAULT_MODEL) or _DEFAULT_MODEL)

        payload = {"model": model, "input": text, "voice": voice, "response_format": fmt}
        try:
            async with httpx.AsyncClient(timeout=60.0) as client:
                response = await client.post(
                    url, headers={"authorization": f"Bearer {key}"}, json=payload,
                )
        except httpx.HTTPError as exc:
            logger.warning("TTS provider unreachable: %s", exc)
            return {"status": "error", "message": "The voice provider is unreachable."}

        if response.status_code != 200:
            return {"status": "error", "message": f"The voice provider returned status {response.status_code}."}

        audio = response.content or b""
        if not audio:
            return {"status": "error", "message": "The voice provider returned no audio."}
        return {
            "status": "success",
            "message": f"Generated {len(audio)} bytes of speech.",
            "data": {
                "audio_base64": base64.b64encode(audio).decode("ascii"),
                "format": fmt,
                "bytes": len(audio),
                "voice": voice,
            },
        }
