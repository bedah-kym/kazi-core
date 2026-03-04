"""
Invoice connector that lets Mathia create an invoice (IntaSend sandbox) and optionally email it.
"""
import logging
import re
from decimal import Decimal, InvalidOperation
from orchestration.base_connector import BaseConnector
from orchestration.connectors.gmail_connector import GmailConnector
from orchestration.connectors.whatsapp_connector import WhatsAppConnector

logger = logging.getLogger(__name__)


class InvoiceConnector(BaseConnector):
    async def execute(self, parameters: dict, context: dict) -> dict:
        action = parameters.get("action")
        if action != "create_invoice":
            return {"status": "error", "message": "Unsupported action"}

        from django.contrib.auth import get_user_model
        from payments.services import InvoiceService
        from asgiref.sync import sync_to_async

        user_id = context.get("user_id")
        if not user_id:
            return {"status": "error", "message": "Missing user context"}

        try:
            User = get_user_model()
            issuer = await sync_to_async(User.objects.get)(id=user_id)
        except Exception:
            return {"status": "error", "message": "User not found"}

        raw_amount = parameters.get("amount")
        description = (parameters.get("description") or parameters.get("narrative") or "").strip()
        payer_email = (parameters.get("payer_email") or parameters.get("email") or "").strip()
        phone_number = (parameters.get("phone_number") or parameters.get("phone") or "").strip()
        currency = parameters.get("currency", "KES").upper()
        send_via_raw = parameters.get("send_via")

        def _normalize_channels(raw):
            channels = set()
            if isinstance(raw, str):
                tokens = re.split(r"[,\\s]+", raw.strip().lower())
            elif isinstance(raw, (list, tuple, set)):
                tokens = [str(item).strip().lower() for item in raw]
            else:
                tokens = []
            for token in tokens:
                if token in ("email", "mail"):
                    channels.add("email")
                if token in ("whatsapp", "wa", "sms", "message"):
                    channels.add("whatsapp")
                if token in ("both", "all"):
                    channels.update({"email", "whatsapp"})
            return channels

        send_via = _normalize_channels(send_via_raw)
        email_requested = bool(payer_email) or ("email" in send_via)
        whatsapp_requested = bool(phone_number) or ("whatsapp" in send_via)

        if not raw_amount:
            return {"status": "error", "message": "amount is required (e.g., 1500)"}
        try:
            amount = Decimal(str(raw_amount))
            if amount <= 0:
                raise InvalidOperation()
        except Exception:
            return {"status": "error", "message": f"Invalid amount: {raw_amount}"}

        if not description:
            description = "Payment request"

        # Create invoice + payment link via IntaSend
        try:
            invoice = await sync_to_async(InvoiceService.create_invoice)(
                issuer=issuer,
                amount=amount,
                description=description,
                payer_email=payer_email,
                recurrence='NONE',
                currency=currency,
            )
        except Exception as e:
            logger.error(f"Invoice creation failed: {e}")
            return {"status": "error", "message": "Failed to create invoice. Check payment provider keys."}

        payment_link = getattr(invoice, "intasend_payment_link", "") or ""

        preferences = context.get("preferences") or {}
        email_status = None
        whatsapp_status = None

        if email_requested:
            if not payer_email:
                email_status = {"status": "error", "message": "payer_email is required to send email."}
            elif not preferences.get("allow_email", True):
                email_status = {"status": "error", "message": "Email sending is disabled in your settings."}
            else:
                mailer = GmailConnector()
                email_status = await mailer.execute({
                    "action": "send_email",
                    "to": payer_email,
                    "subject": f"Invoice {invoice.reference_id}",
                    "text": f"Hello,\n\nHere is your invoice for {amount} {currency}.\nDescription: {description}\nPay securely: {payment_link}\n\nThank you."
                }, context)

        if whatsapp_requested:
            if not phone_number:
                whatsapp_status = {"status": "error", "message": "phone_number is required to send WhatsApp."}
            elif not preferences.get("allow_whatsapp", True):
                whatsapp_status = {"status": "error", "message": "WhatsApp sending is disabled in your settings."}
            else:
                messenger = WhatsAppConnector()
                whatsapp_status = await messenger.execute({
                    "action": "send_message",
                    "phone_number": phone_number,
                    "message": f"Hello, here is your invoice for {amount} {currency}. Pay securely: {payment_link}",
                }, context)

        status_bits = []
        if email_status:
            status_bits.append("Email sent." if email_status.get("status") == "sent" else "Email not sent.")
        if whatsapp_status:
            status_bits.append("WhatsApp sent." if whatsapp_status.get("status") == "sent" else "WhatsApp not sent.")
        status_text = " ".join(status_bits).strip() or "Share the payment link with the payer."

        return {
            "status": "success",
            "invoice": {
                "reference_id": str(invoice.reference_id),
                "amount": float(invoice.amount),
                "currency": currency,
                "description": invoice.description,
                "payer_email": invoice.payer_email,
                "payment_link": payment_link,
            },
            "email_status": email_status,
            "whatsapp_status": whatsapp_status,
            "message": f"Invoice created. {status_text}"
        }
