"""
Frozen model catalog and selection helpers.

Single source of truth for the models the chatroom model picker offers, plus
the ``provider/model`` id convention shared by the picker UI, the REST API, and
the agent-loop per-room override.
"""
from dataclasses import dataclass
from typing import List, Optional, Tuple

from django.conf import settings

# Per-room model preference is stored in Redis under this key template. A value
# of "" (or an absent key) means "Auto" — fall back to the agent loop's
# fast/high heuristic.
MODEL_PREF_KEY = "model_pref:{room_id}"


@dataclass(frozen=True)
class ModelInfo:
    provider: str
    model: str
    label: str
    tier: str  # "fast" | "high" | "vision"
    tool_calling: bool = True


CATALOG: List[ModelInfo] = [
    ModelInfo("deepseek", "deepseek-v4-pro", "DeepSeek Pro", "high"),
    ModelInfo("deepseek", "deepseek-v4-flash", "DeepSeek Flash", "fast"),
    ModelInfo("deepseek", "deepseek-v4-flash-vision-exp", "DeepSeek Flash Vision", "vision"),
    ModelInfo("anthropic", "claude-sonnet-4-6", "Claude Sonnet", "high"),
    ModelInfo("anthropic", "claude-haiku-4-5-20251001", "Claude Haiku", "fast"),
    ModelInfo("huggingface", "meta-llama/Llama-3.1-8B-Instruct", "Llama 3.1 8B", "fast"),
]

# provider -> settings/env key that gates whether its models are offered.
_PROVIDER_KEYS = {
    "anthropic": "ANTHROPIC_API_KEY",
    "deepseek": "DEEPSEEK_API_KEY",
    "huggingface": "HF_API_TOKEN",
}

_PROVIDER_LABELS = {
    "deepseek": "DeepSeek",
    "anthropic": "Anthropic",
    "huggingface": "Hugging Face",
}


def provider_configured(provider: str) -> bool:
    """True when the provider's API key is non-empty in settings/env."""
    key = _PROVIDER_KEYS.get(provider)
    if not key:
        return False
    return bool(str(getattr(settings, key, "") or "").strip())


def provider_label(provider: str) -> str:
    return _PROVIDER_LABELS.get(provider, provider)


def available_models() -> List[ModelInfo]:
    """Models whose provider key is configured — the "models you have" set."""
    return [info for info in CATALOG if provider_configured(info.provider)]


def model_id(info: ModelInfo) -> str:
    return f"{info.provider}/{info.model}"


def parse_model_id(value: str) -> Optional[Tuple[str, str]]:
    """Split "provider/model" into (provider, model), or None if malformed."""
    if not value or "/" not in value:
        return None
    provider, _, model = value.partition("/")
    provider = provider.strip()
    model = model.strip()
    if not provider or not model:
        return None
    return provider, model


def find_model(provider: str, model: str) -> Optional[ModelInfo]:
    for info in CATALOG:
        if info.provider == provider and info.model == model:
            return info
    return None


def pick(provider: str, tier: str) -> Optional[str]:
    """Return the model name for a provider/tier pairing, or None if absent."""
    for info in CATALOG:
        if info.provider == provider and info.tier == tier:
            return info.model
    return None


def model_pref_key(room_id) -> str:
    return MODEL_PREF_KEY.format(room_id=room_id)
