
import logging
import os
from django.conf import settings
from ..base_connector import BaseConnector
from decimal import Decimal
from users.models import WalletTransaction
from payments.services import WalletService
import uuid

logger = logging.getLogger(__name__)

class IntersendPayConnector(BaseConnector):
    """
    Connector for IntaSend Pay (Kenya) - M-Pesa, Card, Bank using the official SDK.
    """
    
    def __init__(self):
        self.publishable_key = os.environ.get('INTASEND_PUBLISHABLE_KEY')
        self.api_key = os.environ.get('INTASEND_API_KEY')
        self.is_test = os.environ.get('INTASEND_IS_TEST', 'True').lower() == 'true'
        self.config_error = ""
        
        try:
            from intasend import IntaSend
            if not self.publishable_key or not self.api_key:
                self.config_error = "IntaSend keys are not configured."
                self.intasend = None
                return
            self.intasend = IntaSend(
                public_key=self.publishable_key,
                secret_key=self.api_key,
                test=self.is_test
            )
        except ImportError:
            self.config_error = "IntaSend SDK is not installed."
            logger.warning("intasend-python not installed.")
            self.intasend = None

    async def execute(self, parameters: dict, context: dict) -> dict:
        """
        Execute Payment actions.
        """
        from django.contrib.auth import get_user_model
        from asgiref.sync import sync_to_async
        User = get_user_model()
        user_id = context.get("user_id")
        
        try:
            user = await sync_to_async(User.objects.get)(id=user_id)
        except Exception:
            return {"status": "error", "message": "User not found"}
            
        action = parameters.get("action")
        
        if action == "create_payment_link":
            return await sync_to_async(self.create_payment_link)(
                amount=parameters.get("amount"),
                currency=parameters.get("currency", "KES"),
                description=parameters.get("description"),
                phone_number=parameters.get("phone_number"),
                email=parameters.get("email"),
                user=user
            )
        elif action == "withdraw":
            return await sync_to_async(self.withdraw_to_mpesa)(
                user=user,
                amount=parameters.get("amount"),
                phone_number=parameters.get("phone_number")
            )
        elif action == "check_status":
            return await sync_to_async(self.check_status)(parameters.get("invoice_id"))

        return {"status": "error", "message": f"Unknown Intersend action: {action}"}

    def create_payment_link(self, amount, currency, description, phone_number=None, email=None, user=None):
        if not self.intasend:
            return {
                "status": "error",
                "message": "Payment provider is not configured. " + self.config_error,
                "action_required": "configure_intasend",
            }
        
        try:
            from intasend import APIService

            service = APIService(
                token=self.api_key,
                publishable_key=self.publishable_key,
                test=self.is_test,
            )
            
            # Using checkout API instead of paymentlinks because IntaSend sandbox returns 502 Bad Gateway
            response = service.collect.checkout(
                amount=float(amount),
                currency=(currency or "KES").upper(),
                email=email or getattr(user, 'email', ''),
                api_ref=f"wallet:{getattr(user, 'id', '')}",
                comment=description or "Payment request",
                mobile_tarrif="BUSINESS-PAYS"
            )

            payment_link = response.get("url")
            invoice_id = response.get("id")
            return {
                "status": "success",
                "payment_link": payment_link,
                "reference": invoice_id,
                "message": "Payment link created.",
            }

        except Exception as e:
            logger.error(f"Intersend Create Link Error: {e}")
            return {"status": "error", "message": str(e)}

    def withdraw_to_mpesa(self, user, amount, phone_number):
        if not self.intasend:
            return {
                "status": "error",
                "message": "Payment provider is not configured. " + self.config_error,
                "action_required": "configure_intasend",
            }
        try:
            wallet = WalletService.get_or_create_user_wallet(user)
        except Exception as e:
            return {"status": "error", "message": f"User has no wallet configured: {str(e)}"}

        amount = Decimal(str(amount))
        if amount <= Decimal('0'):
            return {"status": "error", "message": "Invalid amount"}

        if wallet.balance < amount:
            return {"status": "error", "message": "Insufficient balance"}

        reference = str(uuid.uuid4())
        success, message = wallet.withdraw(
            amount,
            reference,
            description=f"Withdrawal to M-Pesa {phone_number}"
        )
        if not success:
            return {"status": "error", "message": message}

        tx = WalletTransaction.objects.filter(reference=reference).first()
        if tx:
            tx.status = 'PENDING'
            tx.save(update_fields=['status'])

        try:
            service = self.intasend.transfer
            response = service.mpesa(
                currency='KES',
                transactions=[
                    {'name': f'Withdrawal for {user.username}', 'account': phone_number, 'amount': str(amount)}
                ]
            )
            if tx:
                tx.status = 'COMPLETED'
                tx.save(update_fields=['status'])
            if isinstance(response, dict) and "status" not in response:
                response["status"] = "success"
            elif not isinstance(response, dict):
                response = {"status": "success", "data": response}
            return response

        except Exception as e:
            logger.error(f"Refund due to error: {e}")
            wallet.deposit(
                amount,
                f"refund-{reference}",
                description="Refund - API Error"
            )
            if tx:
                tx.status = 'FAILED'
                tx.save(update_fields=['status'])
            return {"status": "error", "message": f"Payout failed: {str(e)}"}

    def check_status(self, invoice_id):
        if not self.intasend:
            return {
                "status": "error",
                "message": "Payment provider is not configured. " + self.config_error,
                "action_required": "configure_intasend",
            }
        
        try:
            service = self.intasend.collect
            result = service.status(invoice_id=invoice_id)
            if isinstance(result, dict) and "status" not in result:
                result["status"] = "success"
            elif not isinstance(result, dict):
                result = {"status": "success", "data": result}
            return result
        except Exception as e:
            return {"status": "error", "message": str(e)}
