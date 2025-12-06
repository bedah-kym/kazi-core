
import logging
import os
from django.conf import settings
from ..base_connector import BaseConnector
from twilio.rest import Client
from twilio.base.exceptions import TwilioRestException

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
        
        if self.account_sid and self.auth_token:
            self.client = Client(self.account_sid, self.auth_token)
        else:
            self.client = None

    async def execute(self, intent: dict, user) -> dict:
        """
        Execute a WhatsApp action.
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
        if settings.DEBUG and not self.client:
             # Mock mode for dev
             print(f"[WhatsApp-Mock] Sending to {to}: {body}")
             return {"status": "sent", "mock": True}

        if self.provider == 'twilio' and self.client:
            try:
                # Ensure 'to' number is in whatsapp format
                to_number = to if to.startswith('whatsapp:') else f"whatsapp:{to}"
                
                msg_args = {
                    'from_': self.from_number,
                    'to': to_number,
                    'body': body
                }
                if media_url:
                    msg_args['media_url'] = [media_url]

                message = self.client.messages.create(**msg_args)
                
                return {
                    "status": "sent", 
                    "sid": message.sid, 
                    "error_code": message.error_code,
                    "error_message": message.error_message
                }
            except TwilioRestException as e:
                logger.error(f"Twilio API Error: {e}")
                return {"error": f"Twilio Error: {e.msg}", "code": e.code}
            except Exception as e:
                logger.error(f"WhatsApp Connector Error: {str(e)}")
                return {"error": str(e)}
        else:
            return {"error": "Unsupported WhatsApp provider or missing credentials"}
