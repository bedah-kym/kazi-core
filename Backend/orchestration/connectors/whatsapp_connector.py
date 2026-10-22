
import logging
import os
from django.conf import settings
from ..base_connector import BaseConnector
from asgiref.sync import sync_to_async

logger = logging.getLogger(__name__)


def _twilio_client(account_sid, auth_token):
    """Create a Twilio client on demand using the supplied credentials.

    SDK import and client construction errors propagate to the caller.
    """
    from twilio.rest import Client

    return Client(account_sid, auth_token)


class WhatsAppConnector(BaseConnector):
    """
    Two-way sync with WhatsApp Business API (via Twilio or Meta Cloud API).
    """

    def __init__(self):
        """Read WhatsApp settings from the environment and initialize Twilio.

        Leave the client unset without both credentials. SDK import and client
        construction errors propagate when credentials are present.
        """
        # Allow switching between vendors via env
        self.provider = os.environ.get('WHATSAPP_PROVIDER', 'twilio')
        self.account_sid = os.environ.get('TWILIO_ACCOUNT_SID')
        self.auth_token = os.environ.get('TWILIO_AUTH_TOKEN')
        self.from_number = os.environ.get('TWILIO_FROM_NUMBER')  # e.g., 'whatsapp:+254...'

        if self.account_sid and self.auth_token:
            self.client = _twilio_client(self.account_sid, self.auth_token)
        else:
            self.client = None

    def validate_config(self):
        """Available with Twilio credentials, or in demo mode where sends mock.

        Without a client the only path is the in-process mock (see
        ``_send_message_sync``); that is intended for the demo install, so the
        actions are offered only there. Outside demo mode a missing key means
        the tool is not offered (T-F2).
        """
        if self.client:
            return True, ""
        from orchestration.connector_registry import is_demo_mode

        # The mock in _send_message_sync runs only under DEBUG, so offer the
        # keyless path only where it can actually answer.
        if is_demo_mode() and settings.DEBUG:
            return True, "demo mode: mocking WhatsApp sends"
        return False, "WhatsApp: set TWILIO_ACCOUNT_SID and TWILIO_AUTH_TOKEN"

    def validate_credentials(self, account_sid, auth_token):
        """
        Validate credentials by initializing a temporary client and fetching account info.

        Return a validity flag and message; SDK import, client construction,
        and API errors are converted to a False result.
        """
        try:
            temp_client = _twilio_client(account_sid, auth_token)
            # Fetch account details to verify credentials
            temp_client.api.v2010.accounts(account_sid).fetch()
            return True, "Credentials valid"
        except Exception as e:
            logger.error(f"WhatsApp Validation Error: {e}")
            return False, "Invalid WhatsApp credentials."

    def send_test_message(self, to, account_sid, auth_token, from_number):
        """
        Send a test message using provided credentials (not the env vars).

        Prefix the recipient with 'whatsapp:' if needed; from_number must
        already use the sender format Twilio expects. Return a sent status
        and SID, or an error status if client creation or sending fails.
        """
        try:
            temp_client = _twilio_client(account_sid, auth_token)
            to_number = to if to.startswith('whatsapp:') else f"whatsapp:{to}"

            message = temp_client.messages.create(
                from_=from_number,
                to=to_number,
                body="Hello from Kazi! Your WhatsApp integration is now connected."
            )
            return {"status": "sent", "sid": message.sid}
        except Exception:
            logger.exception("Failed to send WhatsApp test message")
            return {"status": "error", "message": "An unexpected error occurred. Please try again."}

    async def execute(self, parameters: dict, context: dict) -> dict:
        """
        Execute a WhatsApp action.
        """
        action = parameters.get("action")

        if action in ("send_message", "send_whatsapp"):
            return await self._send_message_async(
                to=parameters.get("phone_number"),
                body=parameters.get("message"),
                media_url=parameters.get("media_url")
            )
        elif action == "send_invoice":
            # Just a wrapper for sending a message with payment link
            return await self._send_message_async(
                to=parameters.get("phone_number"),
                body=parameters.get("message") or f"Hello, here is your invoice: {parameters.get('payment_link')}"
            )
        elif action == "get_templates":
            return {"status": "success", "templates": ["hello_world", "payment_reminder", "shipping_update"]}  # Mock for now

        return {"status": "error", "message": f"Unknown WhatsApp action: {action}"}

    async def _send_message_async(self, to, body, media_url=None):
        """Async wrapper around the sync Twilio send."""
        return await sync_to_async(self._send_message_sync)(to, body, media_url)

    def _send_message_sync(self, to, body, media_url=None):
        """Send a WhatsApp message, optionally attaching one media URL.

        Prefix the recipient with 'whatsapp:' if needed. Return a sent status
        with Twilio details, or a mock sent status in DEBUG without a client.
        Missing inputs, unavailable providers, and send failures return an
        error dict; Twilio API errors include a code. SDK import errors in
        error handling propagate.
        """
        # Basic validation
        if not to or not body:
            return {"status": "error", "message": "phone_number and message are required"}

        if settings.DEBUG and not self.client:
            # Mock mode for dev
            logger.debug("[WhatsApp-Mock] Sending mock message (no Twilio client configured)")
            return {"status": "sent", "mock": True}

        if self.provider == 'twilio' and self.client:
            try:
                # Ensure 'to' number is in whatsapp format
                to_number = to if isinstance(to, str) and to.startswith('whatsapp:') else f"whatsapp:{to}"

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
            except Exception as e:
                from twilio.base.exceptions import TwilioRestException

                if isinstance(e, TwilioRestException):
                    logger.error(f"Twilio API Error: {e}")
                    return {"status": "error", "message": f"Twilio Error: {e.msg}", "code": e.code}
                logger.error(f"WhatsApp Connector Error: {str(e)}")
                return {"status": "error", "message": "An unexpected error occurred. Please try again."}
        else:
            return {"status": "error", "message": "Unsupported WhatsApp provider or missing credentials"}
