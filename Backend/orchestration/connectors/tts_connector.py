"""Text-to-speech connector (``generate_speech``).

Turns text into a spoken audio clip (a voice note). Modality is a connector
choice, not a pipeline stage (v0.6 brief §4.2).

Provider-agnostic: the connector speaks the OpenAI-compatible ``/audio/speech``
shape (the de-facto standard that OpenAI, LocalAI, openedai-speech, Kokoro and
most self-hosted servers expose). Pick a provider with ``TTS_PROVIDER`` and
point ``TTS_URL`` at any compatible endpoint — including a self-hosted one — so
nothing is locked to OpenAI. ``TTS_API_KEY`` (or a provider-specific key like
``OPENAI_API_KEY``) supplies the credential when the endpoint needs one.
"""
from __future__ import annotations

import base64
import logging
from typing import Any, Dict, List, Optional

import httpx

from orchestration.base_connector import BaseConnector

logger = logging.getLogger(__name__)

_VALID_FORMATS = {"mp3", "opus", "aac", "flac", "wav", "pcm"}

# name -> defaults. ``credential`` is the per-provider key name; ``requires_key``
# is False for self-hosted endpoints that need no auth.
_PROVIDERS: Dict[str, Dict[str, Any]] = {
    "openai": {
        "url": "https://api.openai.com/v1/audio/speech",
        "model": "gpt-4o-mini-tts",
        "voice": "alloy",
        "credential": "OPENAI_API_KEY",
        "requires_key": True,
    },
    # Any OpenAI-compatible server (self-hosted Llama/Kokoro/LocalAI/…).
    "openai_compatible": {
        "url": "",
        "model": "tts-1",
        "voice": "alloy",
        "credential": None,
        "requires_key": False,
    },
}


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
    # Credentials are resolved per provider inside execute() so a keyless
    # self-hosted endpoint still registers.
    required_credentials: List[str] = []

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

    def _resolve_provider(self) -> Dict[str, Any]:
        """Return the resolved provider config or an error dict."""
        name = str(_setting("TTS_PROVIDER", "openai") or "openai").strip().lower()
        spec = _PROVIDERS.get(name)
        if not spec:
            supported = ", ".join(sorted(_PROVIDERS))
            return {"error": f"Unknown TTS provider {name!r}; supported: {supported}."}

        key: Optional[str] = self.get_credential("TTS_API_KEY")
        if not key and spec["credential"]:
            key = self.get_credential(spec["credential"])
        if spec["requires_key"] and not key:
            return {"error": "Voice is not configured (no TTS API key)."}

        url = str(_setting("TTS_URL", "") or spec["url"])
        if not url:
            return {"error": f"TTS provider {name!r} needs TTS_URL (no built-in endpoint)."}

        return {
            "name": name,
            "url": url,
            "key": key,
            "model": str(_setting("TTS_MODEL", "") or spec["model"]),
            "voice": str(_setting("TTS_VOICE", "") or spec["voice"]),
        }

    async def execute(self, parameters: Dict[str, Any], context: Dict[str, Any]) -> Dict[str, Any]:
        action = parameters.get("action", "generate_speech")
        if action != "generate_speech":
            return {"status": "error", "message": f"Unknown action: {action}"}

        text = str(parameters.get("text") or "").strip()
        if not text:
            return {"status": "error", "message": "generate_speech requires non-empty 'text'."}

        provider = self._resolve_provider()
        if provider.get("error"):
            return {"status": "error", "message": provider["error"]}

        fmt = str(parameters.get("format") or "mp3").lower()
        if fmt not in _VALID_FORMATS:
            fmt = "mp3"
        voice = str(parameters.get("voice") or provider["voice"])

        headers = {}
        if provider["key"]:
            headers["authorization"] = f"Bearer {provider['key']}"
        payload = {
            "model": provider["model"],
            "input": text,
            "voice": voice,
            "response_format": fmt,
        }

        try:
            async with httpx.AsyncClient(timeout=60.0) as client:
                response = await client.post(provider["url"], headers=headers, json=payload)
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
                "provider": provider["name"],
            },
        }
