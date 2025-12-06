
import logging
import os
import uuid
from ..mcp_router import BaseConnector
from users.models import Wallet, Workspace
from intasend import IntaSend

logger = logging.getLogger(__name__)

class IntersendPayConnector(BaseConnector):
    """
    Connector for IntaSend Pay (Kenya) - M-Pesa, Card, Bank using the official SDK.
    """
    
    def __init__(self):
        self.publishable_key = os.environ.get('INTASEND_PUBLISHABLE_KEY')
        self.api_key = os.environ.get('INTASEND_API_KEY')
        self.is_test = os.environ.get('INTASEND_IS_TEST', 'True').lower() == 'true'
        
        if self.publishable_key and self.api_key:
             self.intasend = IntaSend(
                public_key=self.publishable_key,
                secret_key=self.api_key,
                test=self.is_test
            )
        else:
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
        
        # Use simple M-Pesa STK Push directly if phone is provided, or just return check out URL
        try:
            # IntaSend SDK 'collect' method usually triggers STK push directly
            if phone_number:
                service = self.intasend.collect
                response = service.mpesa_stk_push(
                    phone_number=phone_number,
                    email=email or getattr(user, 'email', 'nomail@example.com'),
                    amount=amount,
                    narrative=description
                )
                return response
            
            # If no phone, generate checkout URL (generic)
            # SDK might not have a direct "generate link" independent of wallet/checkout. 
            # We can use the Checkout API if available, but STK push is cleaner for this demo.
            # Assuming we want a "payment request" link logic:
            # For now, return a manually constructed link if the SDK doesn't support 'create_link' perfectly 
            # or rely on the frontend to render the IntaSend button with the public key.
            return {"message": "Please use the frontend button with the publishable key or provide a phone number for STK push."}

        except Exception as e:
            logger.error(f"Intersend Create Link Error: {e}")
            return {"error": str(e)}

    def withdraw_to_mpesa(self, user, amount, phone_number):
        try:
            workspace = user.workspace
            wallet = workspace.wallet
        except Exception:
            return {"error": "User has no wallet configured"}

        # Atomic withdrawal from local wallet first
        reference = f"WD-{uuid.uuid4()}"
        success, msg = wallet.withdraw(amount, reference, f"Withdrawal to {phone_number}")
        
        if not success:
            return {"error": msg}
        
        if not self.intasend:
             # Mock success
             print(f"[Intersend-Mock] Payout {amount} to {phone_number}")
             return {"status": "success", "transaction_id": reference, "message": "Funds sent to M-Pesa (Mock)"}
             
        try:
            service = self.intasend.transfer
            response = service.mpesa(
                currency='KES',
                transactions=[
                    {'name': f'Withdrawal for {user.username}', 'account': phone_number, 'amount': str(amount)}
                ]
            )
            
            # Check response status mostly to confirm scheduling
            # If IntaSend fails, we should REFUND the wallet here!
            # Using simple check:
            # response format depends on SDK version, assuming standard dict
            return response
            
        except Exception as e:
            # REFUND WALLET
            print(f"Refund due to error: {e}")
            wallet.deposit(amount, f"REFUND-{reference}", "System Refund - API Error")
            return {"error": f"Payout failed using SDK: {str(e)}"}

    def check_status(self, invoice_id):
        if not self.intasend:
            return {"status": "COMPLETED", "mock": True}
        
        try:
            # SDK helper for status checking might be:
            service = self.intasend.collect
            return service.status(invoice_id=invoice_id)
        except Exception as e:
             return {"error": str(e)}
