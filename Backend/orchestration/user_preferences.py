"""User preference helpers for personalization and locale-aware parsing."""
from __future__ import annotations

from typing import Any, Dict, Optional

_ALLOWED_TONES = {"friendly", "formal", "direct", "warm", "casual"}
_ALLOWED_VERBOSITY = {"short", "balanced", "detailed"}
_ALLOWED_DIRECTNESS = {"direct", "neutral", "polite"}
_ALLOWED_DATE_ORDERS = {"DMY", "MDY", "YMD"}
_ALLOWED_TIME_FORMATS = {"24h", "12h"}
_ALLOWED_CAPABILITY_MODES = {"custom", "conserve", "balanced", "max"}
_ALLOWED_NUDGE_FREQUENCIES = {"off", "low", "medium", "high"}
_TRUTHY_VALUES = {"1", "true", "yes", "on"}
_FALSY_VALUES = {"0", "false", "no", "off"}

_REGION_LOCALE_FALLBACKS = {
    "America": "en-US",
    "Europe": "en-GB",
    "Africa": "en-KE",
}

_LOCALE_CURRENCY_MAP = {
    "en-US": "USD",
    "en-GB": "GBP",
    "en-KE": "KES",
    "en-UG": "UGX",
    "en-TZ": "TZS",
}


def _coerce_str(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _normalize_choice(value: Any, allowed: set, default: str) -> str:
    candidate = _coerce_str(value).lower()
    if not candidate:
        return default
    for option in allowed:
        if candidate == option:
            return option
    return default


def _coerce_bool(value: Any, default: bool) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    candidate = _coerce_str(value).lower()
    if candidate in _TRUTHY_VALUES:
        return True
    if candidate in _FALSY_VALUES:
        return False
    return default


def _normalize_approval_overrides(value: Any) -> Dict[str, str]:
    if not isinstance(value, dict):
        return {}
    normalized: Dict[str, str] = {}
    for action, policy in value.items():
        action_name = _coerce_str(action)
        policy_name = _coerce_str(policy).lower()
        if not action_name:
            continue
        if policy_name not in {"auto", "always"}:
            continue
        normalized[action_name] = policy_name
    return normalized


def _pull_pref(raw_prefs: Optional[Dict[str, Any]], key: str) -> Optional[Any]:
    if not isinstance(raw_prefs, dict):
        return None
    if key in raw_prefs:
        return raw_prefs.get(key)
    nested = raw_prefs.get("assistant_preferences")
    if isinstance(nested, dict) and key in nested:
        return nested.get(key)
    return None


def _infer_locale(language: str, timezone_name: str) -> str:
    if language and "-" in language:
        return language
    if timezone_name:
        region = timezone_name.split("/", 1)[0]
        fallback = _REGION_LOCALE_FALLBACKS.get(region)
        if fallback:
            return fallback
    return language or "en"


def _infer_date_order(locale: str, timezone_name: str) -> str:
    if locale.endswith("US") or timezone_name.startswith("America/"):
        return "MDY"
    if locale.endswith("JP") or locale.endswith("CN"):
        return "YMD"
    return "DMY"


def _infer_time_format(locale: str, timezone_name: str) -> str:
    if locale.endswith("US") or timezone_name.startswith("America/"):
        return "12h"
    return "24h"


def _infer_currency(locale: str, timezone_name: str, location: str) -> str:
    location_lower = location.lower()
    if "kenya" in location_lower or "nairobi" in location_lower:
        return "KES"
    if "uganda" in location_lower:
        return "UGX"
    if "tanzania" in location_lower:
        return "TZS"
    if locale in _LOCALE_CURRENCY_MAP:
        return _LOCALE_CURRENCY_MAP[locale]
    if timezone_name.startswith("America/"):
        return "USD"
    if timezone_name.startswith("Europe/"):
        return "EUR"
    if timezone_name.startswith("Africa/"):
        return "KES"
    return "USD"


def normalize_preferences(
    raw_prefs: Optional[Dict[str, Any]],
    *,
    language: str = "",
    timezone_name: str = "",
    location: str = "",
) -> Dict[str, Any]:
    tone = _normalize_choice(_pull_pref(raw_prefs, "assistant_tone"), _ALLOWED_TONES, "friendly")
    verbosity = _normalize_choice(_pull_pref(raw_prefs, "assistant_verbosity"), _ALLOWED_VERBOSITY, "balanced")
    directness = _normalize_choice(_pull_pref(raw_prefs, "assistant_directness"), _ALLOWED_DIRECTNESS, "neutral")

    locale = _coerce_str(_pull_pref(raw_prefs, "assistant_locale"))
    if not locale:
        locale = _infer_locale(_coerce_str(language), _coerce_str(timezone_name))

    date_order = _pull_pref(raw_prefs, "assistant_date_order")
    date_order = _normalize_choice(date_order, _ALLOWED_DATE_ORDERS, _infer_date_order(locale, timezone_name))

    time_format = _pull_pref(raw_prefs, "assistant_time_format")
    time_format = _normalize_choice(time_format, _ALLOWED_TIME_FORMATS, _infer_time_format(locale, timezone_name))

    currency = _coerce_str(_pull_pref(raw_prefs, "assistant_currency"))
    if not currency:
        currency = _infer_currency(locale, _coerce_str(timezone_name), _coerce_str(location))

    capability_mode = _normalize_choice(
        _pull_pref(raw_prefs, "capability_mode"),
        _ALLOWED_CAPABILITY_MODES,
        "custom",
    )
    nudge_frequency = _normalize_choice(
        _pull_pref(raw_prefs, "nudge_frequency"),
        _ALLOWED_NUDGE_FREQUENCIES,
        "low",
    )

    approval_overrides = _normalize_approval_overrides(
        _pull_pref(raw_prefs, "approval_overrides")
    )

    return {
        "tone": tone,
        "verbosity": verbosity,
        "directness": directness,
        "locale": locale,
        "date_order": date_order,
        "time_format": time_format,
        "currency": currency,
        "capability_mode": capability_mode,
        "language": _coerce_str(language),
        "timezone": _coerce_str(timezone_name),
        "location": _coerce_str(location),
        # Capability controls (used by agent tool filtering and executor gates)
        "proactive_assistant_enabled": _coerce_bool(
            _pull_pref(raw_prefs, "proactive_assistant_enabled"), True
        ),
        "nudge_frequency": nudge_frequency,
        "proactive_snooze_until": _coerce_str(_pull_pref(raw_prefs, "proactive_snooze_until")),
        "ai_voice_enabled": _coerce_bool(_pull_pref(raw_prefs, "ai_voice_enabled"), False),
        "manager_llm_enabled": _coerce_bool(_pull_pref(raw_prefs, "manager_llm_enabled"), True),
        "allow_web_search": _coerce_bool(_pull_pref(raw_prefs, "allow_web_search"), True),
        "allow_travel": _coerce_bool(_pull_pref(raw_prefs, "allow_travel"), True),
        "allow_payments": _coerce_bool(_pull_pref(raw_prefs, "allow_payments"), True),
        "allow_reminders": _coerce_bool(_pull_pref(raw_prefs, "allow_reminders"), True),
        "allow_whatsapp": _coerce_bool(_pull_pref(raw_prefs, "allow_whatsapp"), True),
        "allow_email": _coerce_bool(_pull_pref(raw_prefs, "allow_email"), True),
        "allow_calendar": _coerce_bool(_pull_pref(raw_prefs, "allow_calendar"), True),
        # Approval overrides for confirmation policy (e.g. {"send_email": "auto"})
        "approval_overrides": approval_overrides,
    }


def format_date_hint(preferences: Optional[Dict[str, Any]]) -> str:
    if not preferences:
        return "YYYY-MM-DD"
    order = preferences.get("date_order") or "DMY"
    if order == "MDY":
        return "MM/DD/YYYY"
    if order == "DMY":
        return "DD/MM/YYYY"
    return "YYYY-MM-DD"


def format_time_hint(preferences: Optional[Dict[str, Any]]) -> str:
    if not preferences:
        return "HH:MM"
    time_format = preferences.get("time_format") or "24h"
    if time_format == "12h":
        return "h:mm am/pm"
    return "HH:MM"


def dayfirst_default(preferences: Optional[Dict[str, Any]]) -> bool:
    if not preferences:
        return True
    order = preferences.get("date_order") or "DMY"
    return order != "MDY"


def format_style_prompt(preferences: Optional[Dict[str, Any]]) -> str:
    if not preferences:
        return ""
    tone = preferences.get("tone") or "friendly"
    verbosity = preferences.get("verbosity") or "balanced"
    directness = preferences.get("directness") or "neutral"
    locale = preferences.get("locale") or ""
    date_hint = format_date_hint(preferences)
    time_hint = format_time_hint(preferences)
    parts = [
        f"Style: {tone}, {verbosity}, {directness}.",
    ]
    if locale:
        parts.append(f"Locale: {locale}.")
    parts.append(f"Date format: {date_hint}. Time format: {time_hint}.")
    return " ".join(parts)


def get_user_preferences(user_id: Optional[int]) -> Dict[str, Any]:
    if not user_id:
        return normalize_preferences({}, language="", timezone_name="", location="")
    try:
        from django.contrib.auth import get_user_model
    except Exception:
        return normalize_preferences({}, language="", timezone_name="", location="")

    User = get_user_model()
    user = User.objects.select_related("profile").filter(id=user_id).first()
    if not user or not hasattr(user, "profile"):
        return normalize_preferences({}, language="", timezone_name="", location="")

    profile = user.profile
    prefs = profile.notification_preferences or {}
    return normalize_preferences(
        prefs,
        language=getattr(profile, "language", "") or "",
        timezone_name=getattr(profile, "timezone", "") or "",
        location=getattr(profile, "location", "") or "",
    )
