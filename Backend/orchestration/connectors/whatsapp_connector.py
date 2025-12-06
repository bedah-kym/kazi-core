
import logging
import os
import requests
from django.conf import settings
from ..mcp_router import BaseConnector

logger = logging.getLogger(__name__)

class WhatsAppConnector(BaseConnector):
    """
    Two-way sync with WhatsApp Business API (via Twilio or Meta Cloud API).
    """
    
    def __init__(self):
        # Allow switching between vendors via env
        self.provider = os.environ.get('WHATSAPP_PROVIDER', 'twilio')
        self.account_sid = os.environ.get('TWILIO_ACCOUNT_SID')
        self.auth_token = os.environ.get('TWILIO_AUTH_TOKEN')
        self.from_number = os.environ.get('TWILIO_FROM_NUMBER') # e.g., 'whatsapp:+254...'

    async def execute(self, intent: dict, user) -> dict:
        """
        Execute a WhatsApp action.
        Intent structure:
        {
            "connector": "whatsapp",
            "action": "send_message" | "send_invoice" | "get_templates",
            "phone_number": "+254...",
            "message": "Hello!",
            "media_url": "..." (optional)
        }
        """
        action = intent.get("action")
        
        if action == "send_message":
            return self.send_message(
                to=intent.get("phone_number"),
                body=intent.get("message"),
                media_url=intent.get("media_url")
            )
        elif action == "send_invoice":
            # Just a wrapper for sending a message with payment link
            return self.send_message(
                to=intent.get("phone_number"),
                body=f"Hello, here is your invoice: {intent.get('payment_link')}"
            )
        elif action == "get_templates":
            return {"templates": ["hello_world", "payment_reminder", "shipping_update"]} # Mock for now
            
        return {"error": f"Unknown WhatsApp action: {action}"}

    def send_message(self, to, body, media_url=None):
        if settings.DEBUG and not self.account_sid:
             # Mock mode for dev
             print(f"[WhatsApp-Mock] Sending to {to}: {body}")
             return {"status": "sent", "mock": True}

        if self.provider == 'twilio':
            try:
                # Use requests to avoid adding twilio-python dependency if not needed yet
                # or use local mock if installed.
                # Basic basic auth request to Twilio API
                url = f"https://api.twilio.com/2010-04-01/Accounts/{self.account_sid}/Messages.json"
                data = {
                    "From": self.from_number,
                    "To": f"whatsapp:{to.replace('whatsapp:', '')}", # Ensure format
                    "Body": body
                }
                if media_url:
                    data['MediaUrl'] = media_url
                
                resp = requests.post(url, data=data, auth=(self.account_sid, self.auth_token))
                
                if resp.status_code in [200, 201]:
                    return resp.json()
                else:
                    logger.error(f"Twilio error: {resp.text}")
                    return {"error": "Failed to send message via Twilio", "details": resp.text}
            except Exception as e:
                logger.error(f"WhatsApp Connector Error: {str(e)}")
                return {"error": str(e)}
        else:
            return {"error": "Unsupported WhatsApp provider"}
