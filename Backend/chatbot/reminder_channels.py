"""Readiness and channel planning for reminder delivery.

A reminder set in a chat room is always delivered in that room. Email,
WhatsApp and Telegram are extra channels: they are only used when the user
asked for them and the install can actually send them. Demo-mode mocks never
count as ready.
"""
import logging
import os

from django.conf import settings

logger = logging.getLogger(__name__)


def _mailgun_configured() -> bool:
    if os.environ.get("MAILGUN_USE_SANDBOX", "").lower() in ("1", "true", "yes"):
        return bool(
            os.environ.get("MAILGUN_API_KEY_SANDBOX")
            and os.environ.get("MAILGUN_DOMAIN_SANDBOX")
        )
    return bool(os.environ.get("MAILGUN_API_KEY") and os.environ.get("MAILGUN_DOMAIN"))


def _gmail_configured(user) -> bool:
    if not (
        getattr(settings, "GMAIL_OAUTH_CLIENT_ID", None)
        and getattr(settings, "GMAIL_OAUTH_CLIENT_SECRET", None)
    ):
        return False
    from users.models import UserIntegration

    return UserIntegration.objects.filter(
        user_id=getattr(user, "id", None),
        integration_type="gmail",
        is_connected=True,
    ).exists()


def user_phone(user):
    """Return the phone number the user configured for notifications."""
    profile = getattr(user, "profile", None)
    prefs = getattr(profile, "notification_preferences", None) or {}
    return (
        getattr(profile, "phone_number", None)
        or getattr(profile, "phone", None)
        or prefs.get("phone_number")
        or ""
    )


def _telegram_chat_id(user):
    from users.models import UserIntegration

    integration = UserIntegration.objects.filter(
        user_id=getattr(user, "id", None),
        integration_type="telegram",
        is_connected=True,
    ).first()
    if not integration:
        return None
    return (integration.metadata or {}).get("chat_id")


def channel_ready(user, channel):
    """Return ``(ready, reason)`` for an extra reminder channel.

    ``reason`` is a user-facing clause used in the creation reply and the
    error log. A demo-mode mock is deliberately never ready: a simulated send
    is not evidence that the reminder reached the user.
    """
    from orchestration.connector_registry import is_demo_mode

    if is_demo_mode():
        return False, "demo mode only mocks sends"

    if channel == "email":
        if not getattr(user, "email", None):
            return False, "you don't have an email address on your account"
        if _mailgun_configured():
            return True, ""
        if _gmail_configured(user):
            return True, ""
        return False, "email isn't set up on this install"

    if channel == "whatsapp":
        if not (
            os.environ.get("TWILIO_ACCOUNT_SID")
            and os.environ.get("TWILIO_AUTH_TOKEN")
        ):
            return False, "WhatsApp isn't set up on this install"
        if not user_phone(user):
            return False, "you don't have a phone number on your account"
        return True, ""

    if channel == "telegram":
        token = getattr(settings, "TELEGRAM_BOT_TOKEN", None) or os.environ.get(
            "TELEGRAM_BOT_TOKEN"
        )
        if not token:
            return False, "Telegram isn't set up on this install"
        if not _telegram_chat_id(user):
            return False, "Telegram isn't connected to your account"
        return True, ""

    return False, f"unknown channel {channel}"


def resolve_delivery(user, requested="auto", urgent=False, has_room=False):
    """Turn the requested mode into stored flags, channels and a reply clause.

    A requested extra channel that is not ready is dropped; the row then
    stores the chat-only mode. The returned ``message`` always names where the
    reminder will arrive.
    """
    requested = (requested or "auto").strip().lower()
    if requested not in ("auto", "in_app", "email", "whatsapp"):
        requested = "auto"
    if urgent:
        requested = "email"

    if requested == "email":
        requested_extras = ["email"]
    elif requested == "whatsapp":
        requested_extras = ["whatsapp"]
    elif requested == "auto":
        requested_extras = ["email"]
    else:
        requested_extras = []

    ready = {}
    not_ready = {}
    for channel in requested_extras:
        ok, reason = channel_ready(user, channel)
        if ok:
            ready[channel] = reason
        else:
            not_ready[channel] = reason

    if requested == "email":
        delivery = "email" if "email" in ready else "in_app"
    elif requested == "whatsapp":
        delivery = "whatsapp" if "whatsapp" in ready else "in_app"
    elif requested == "auto":
        delivery = "auto" if "email" in ready else "in_app"
    else:
        delivery = "in_app"

    if delivery == "auto":
        via_email, via_whatsapp = True, True
    elif delivery == "email":
        via_email, via_whatsapp = True, False
    elif delivery == "whatsapp":
        via_email, via_whatsapp = False, True
    else:
        via_email, via_whatsapp = False, False

    base = "here in chat" if has_room else "as an in-app notification"
    channels = ["chat" if has_room else "in_app"]
    if ready:
        labels = {"email": "email", "whatsapp": "WhatsApp"}
        labels_in = ", ".join(labels[channel] for channel in ready)
        phrase = f"I'll remind you {base} and by {labels_in}."
        channels.extend(ready)
    else:
        phrase = f"I'll remind you {base}."

    # Only an explicitly requested channel (or urgent email) explains why it
    # was dropped. Auto defaults to chat without lecturing about email setup.
    if requested in ("email", "whatsapp") and not_ready:
        reason = next(iter(not_ready.values()))
        reason = reason[:1].upper() + reason[1:]
        phrase = f"{reason}, so I'll remind you {base}."

    return {
        "delivery": delivery,
        "via_email": via_email,
        "via_whatsapp": via_whatsapp,
        "channels": channels,
        "message": phrase,
    }
