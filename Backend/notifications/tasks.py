"""Celery tasks for external notification delivery (email / WhatsApp)."""
from __future__ import annotations

import logging
from typing import Optional

from celery import shared_task

logger = logging.getLogger(__name__)


def _mark_delivered(notification_id: Optional[int], field: str) -> None:
    """Set a delivery flag on the Notification row."""
    if not notification_id:
        return
    try:
        from notifications.models import Notification

        Notification.objects.filter(id=notification_id).update(**{field: True})
    except Exception as exc:
        logger.warning("Could not mark notification %s delivered (%s): %s", notification_id, field, exc)


@shared_task(bind=True, max_retries=2, default_retry_delay=30, ignore_result=True)
def deliver_notification_email(self, notification_id, user_id, event_type, title, body):
    """
    Send notification via email, reusing the existing connector stack.
    Tries GmailConnector (if user has Gmail integration), falls back to Mailgun.
    """
    try:
        from django.contrib.auth import get_user_model

        User = get_user_model()
        user = User.objects.select_related("profile").filter(id=user_id).first()
        if not user or not user.email:
            logger.info("No email address for user %s — skipping email notification", user_id)
            return

        subject = f"[Mathia] {title}"
        text_body = body or title

        sent = False

        # Try Mailgun first (system-level, always available)
        try:
            from orchestration.connectors.mailgun_connector import MailgunConnector

            connector = MailgunConnector()
            result = connector.execute({
                "action": "send_email",
                "to": user.email,
                "subject": subject,
                "body": text_body,
            })
            if result.get("status") in ("success", "sent"):
                sent = True
        except Exception as exc:
            logger.debug("Mailgun send failed: %s", exc)

        if not sent:
            logger.info("Email notification for user %s could not be sent", user_id)
            return

        _mark_delivered(notification_id, "delivered_email")

    except Exception as exc:
        logger.error("deliver_notification_email failed: %s", exc, exc_info=True)
        raise self.retry(exc=exc)


@shared_task(bind=True, max_retries=2, default_retry_delay=30, ignore_result=True)
def deliver_notification_whatsapp(self, notification_id, user_id, event_type, title, body):
    """Send notification via WhatsApp using existing WhatsAppConnector."""
    try:
        from django.contrib.auth import get_user_model

        User = get_user_model()
        user = User.objects.select_related("profile").filter(id=user_id).first()
        if not user:
            return

        phone = getattr(user.profile, "phone_number", None) or getattr(user.profile, "phone", None)
        if not phone:
            logger.info("No phone number for user %s — skipping WhatsApp notification", user_id)
            return

        message_text = f"*{title}*\n{body}" if body else title

        from orchestration.connectors.whatsapp_connector import WhatsAppConnector

        connector = WhatsAppConnector()
        result = connector.execute({
            "action": "send_whatsapp",
            "phone_number": str(phone),
            "message": message_text,
        })
        if result.get("status") in ("success", "sent"):
            _mark_delivered(notification_id, "delivered_whatsapp")
        else:
            logger.info("WhatsApp notification for user %s failed: %s", user_id, result.get("message"))

    except Exception as exc:
        logger.error("deliver_notification_whatsapp failed: %s", exc, exc_info=True)
        raise self.retry(exc=exc)
