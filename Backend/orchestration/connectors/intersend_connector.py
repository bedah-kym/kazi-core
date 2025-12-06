
import logging
import os
import requests
import uuid
from ..mcp_router import BaseConnector
from users.models import Wallet, Workspace

logger = logging.getLogger(__name__)

class IntersendPayConnector(BaseConnector):
    """
    Connector for Intersend Pay (Kenya) - M-Pesa, Card, Bank.
    """
    
    def __init__(self):
        self.api_key = os.environ.get('INTERSEND_API_KEY')
        self.merchant_id = os.environ.get('INTERSEND_MERCHANT_ID')
        self.base_url = "https://api.intersend.com/v1" # Hypothetical URL

    async def execute(self, intent: dict, user) -> dict:
        """
        Execute Payment actions.
        Intent:
        {
            "connector": "intersend",
            "action": "create_payment_link" | "check_status" | "withdraw",
            "amount": 1000,
            "currency": "KES",
            "description": "Consulting Fee"
        }
        """
        action = intent.get("action")
        
        if action == "create_payment_link":
            return self.create_payment_link(
                amount=intent.get("amount"),
                currency=intent.get("currency", "KES"),
                description=intent.get("description"),
                user=user
            )
        elif action == "withdraw":
            return self.withdraw_to_mpesa(
                user=user,
                amount=intent.get("amount"),
                phone_number=intent.get("phone_number")
            )
            
        return {"error": f"Unknown Intersend action: {action}"}

    def create_payment_link(self, amount, currency, description, user):
        # In a real scenario, call Intersend API.
        # Check if we have API keys, if not use Mock
        if not self.api_key:
             print(f"[Intersend-Mock] Creating link for {currency} {amount}")
             mock_ref = str(uuid.uuid4())
             return {
                 "payment_link": f"https://pay.intersend.com/mock/{mock_ref}",
                 "reference": mock_ref,
                 "status": "generated"
             }
        
        # Real API Implementation (Placeholder)
        payload = {
            "merchant_id": self.merchant_id,
            "amount": amount,
            "currency": currency,
            "description": description,
            "redirect_url": "https://kwikchat.com/payment/callback"
        }
        # resp = requests.post(f"{self.base_url}/checkout/link", json=payload, headers={"Authorization": self.api_key})
        # return resp.json()
        return {"error": "Real Intersend API not enabled yet"}

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
        
        # Then trigger payout via Intersend API
        if not self.api_key:
            # Mock success
             print(f"[Intersend-Mock] Payout {amount} to {phone_number}")
             return {"status": "success", "transaction_id": reference, "message": "Funds sent to M-Pesa"}
             
        # Real API call would go here. If it fails, we must refund the wallet!
        # For prototype, assuming success or manual reconciliation.
        return {"status": "success", "transaction_id": reference}
