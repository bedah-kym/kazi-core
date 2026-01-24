
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
        
        try:
            from intasend import IntaSend
            if self.publishable_key and self.api_key:
                self.intasend = IntaSend(
                    public_key=self.publishable_key,
                    secret_key=self.api_key,
                    test=self.is_test
                )
            else:
                self.intasend = None
        except ImportError:
            logger.warning("intasend-python not installed. Using mock mode.")
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
            return self.create_payment_link(
                amount=parameters.get("amount"),
                currency=parameters.get("currency", "KES"),
                description=parameters.get("description"),
                phone_number=parameters.get("phone_number"),
                email=parameters.get("email"),
                user=user
            )
        elif action == "withdraw":
            return self.withdraw_to_mpesa(
                user=user,
                amount=parameters.get("amount"),
                phone_number=parameters.get("phone_number")
            )
        elif action == "check_status":
            return self.check_status(parameters.get("invoice_id"))
            
        return {"error": f"Unknown Intersend action: {action}"}

    def create_payment_link(self, amount, currency, description, phone_number=None, email=None, user=None):
        if not self.intasend:
            print(f"[Intersend-Mock] Creating link for {currency} {amount}")
            mock_ref = str(uuid.uuid4())
            return {
                "payment_link": f"https://payment.intasend.com/pay/{mock_ref}",
                "reference": mock_ref,
                "status": "generated"
            }
        
        try:
            if phone_number:
                service = self.intasend.collect
                response = service.mpesa_stk_push(
                    phone_number=phone_number,
                    email=email or getattr(user, 'email', 'nomail@example.com'),
                    amount=amount,
                    narrative=description
                )
                return response
            
            return {"message": "Please provide phone number for STK push or use frontend button with publishable key."}

        except Exception as e:
            logger.error(f"Intersend Create Link Error: {e}")
            return {"error": str(e)}

    def withdraw_to_mpesa(self, user, amount, phone_number):
        try:
            wallet = WalletService.get_or_create_user_wallet(user)
        except Exception as e:
            return {"error": f"User has no wallet configured: {str(e)}"}

        amount = Decimal(str(amount))
        if amount <= Decimal('0'):
            return {"error": "Invalid amount"}

        if wallet.balance < amount:
            return {"error": "Insufficient balance"}

        reference = str(uuid.uuid4())
        success, message = wallet.withdraw(
            amount,
            reference,
            description=f"Withdrawal to M-Pesa {phone_number}"
        )
        if not success:
            return {"error": message}

        tx = WalletTransaction.objects.filter(reference=reference).first()
        if tx:
            tx.status = 'PENDING'
            tx.save(update_fields=['status'])

        if not self.intasend:
            if tx:
                tx.status = 'COMPLETED'
                tx.save(update_fields=['status'])
            print(f"[Intersend-Mock] Payout {amount} to {phone_number}")
            return {"status": "success", "message": "Funds sent to M-Pesa (Mock)", "reference": reference}

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
            return {"error": f"Payout failed: {str(e)}"}

    def check_status(self, invoice_id):
        if not self.intasend:
            return {"status": "COMPLETED", "mock": True}
        
        try:
            service = self.intasend.collect
            return service.status(invoice_id=invoice_id)
        except Exception as e:
            return {"error": str(e)}
