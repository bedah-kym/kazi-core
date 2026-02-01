"""
Invoice connector that lets Mathia create an invoice (IntaSend sandbox) and optionally email it.
"""
import logging
from decimal import Decimal, InvalidOperation
from orchestration.base_connector import BaseConnector
from orchestration.connectors.mailgun_connector import MailgunConnector

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
        currency = parameters.get("currency", "KES").upper()

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
                recurrence='NONE'
            )
        except Exception as e:
            logger.error(f"Invoice creation failed: {e}")
            return {"status": "error", "message": "Failed to create invoice. Check payment provider keys."}

        payment_link = getattr(invoice, "intasend_payment_link", "") or ""

        # Optionally email the invoice link
        email_status = None
        if payer_email:
            mailer = MailgunConnector()
            email_status = await mailer.execute({
                "action": "send_email",
                "to": payer_email,
                "subject": f"Invoice {invoice.reference_id}",
                "body": f"Hello,\n\nHere is your invoice for {amount} {currency}.\nDescription: {description}\nPay securely: {payment_link}\n\nThank you."
            }, context)

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
            "message": f"Invoice created. {'Email sent.' if payer_email else 'No email sent (no payer email provided).'}"
        }
