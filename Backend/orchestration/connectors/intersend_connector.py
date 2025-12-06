
import logging
import os
from django.conf import settings
from ..base_connector import BaseConnector
from decimal import Decimal
from users.models import Wallet, WalletTransaction
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

    async def execute(self, intent: dict, user) -> dict:
        """
        Execute Payment actions.
        """
        action = intent.get("action")
        
        if action == "create_payment_link":
            return self.create_payment_link(
                amount=intent.get("amount"),
                currency=intent.get("currency", "KES"),
                description=intent.get("description"),
                phone_number=intent.get("phone_number"),
                email=intent.get("email"),
                user=user
            )
        elif action == "withdraw":
            return self.withdraw_to_mpesa(
                user=user,
                amount=intent.get("amount"),
                phone_number=intent.get("phone_number")
            )
        elif action == "check_status":
            return self.check_status(intent.get("invoice_id"))
            
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
            workspace = user.workspace
            wallet = Wallet.objects.get(workspace=workspace, currency='KES')
        except Exception as e:
            return {"error": f"User has no wallet configured: {str(e)}"}

        # Check balance
        if wallet.balance < Decimal(str(amount)):
            return {"error": "Insufficient balance"}
        
        # Deduct from wallet
        wallet.balance -= Decimal(str(amount))
        wallet.save()
        
        # Log transaction
        WalletTransaction.objects.create(
            wallet=wallet,
            type='withdrawal',
            amount=Decimal(str(amount)),
            description=f"Withdrawal to M-Pesa {phone_number}",
            metadata={'phone': phone_number}
        )
        
        if not self.intasend:
            # Mock success
            print(f"[Intersend-Mock] Payout {amount} to {phone_number}")
            return {"status": "success", "message": "Funds sent to M-Pesa (Mock)"}
             
        try:
            service = self.intasend.transfer
            response = service.mpesa(
                currency='KES',
                transactions=[
                    {'name': f'Withdrawal for {user.username}', 'account': phone_number, 'amount': str(amount)}
                ]
            )
            return response
            
        except Exception as e:
            # REFUND WALLET
            logger.error(f"Refund due to error: {e}")
            wallet.balance += Decimal(str(amount))
            wallet.save()
            
            WalletTransaction.objects.create(
                wallet=wallet,
                type='refund',
                amount=Decimal(str(amount)),
                description=f"Refund - API Error",
                metadata={'error': str(e)}
            )
            return {"error": f"Payout failed: {str(e)}"}

    def check_status(self, invoice_id):
        if not self.intasend:
            return {"status": "COMPLETED", "mock": True}
        
        try:
            service = self.intasend.collect
            return service.status(invoice_id=invoice_id)
        except Exception as e:
            return {"error": str(e)}
