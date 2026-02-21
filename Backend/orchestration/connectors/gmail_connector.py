import base64
import json
import logging
import time
from email.message import EmailMessage
from typing import Dict, Any, Optional

import httpx
from asgiref.sync import sync_to_async
from django.conf import settings
from django.contrib.auth import get_user_model

from orchestration.base_connector import BaseConnector
from users.encryption import TokenEncryption
from users.models import UserIntegration

logger = logging.getLogger(__name__)


class GmailConnector(BaseConnector):
    TOKEN_URL = "https://oauth2.googleapis.com/token"
    SEND_URL = "https://gmail.googleapis.com/gmail/v1/users/me/messages/send"

    def __init__(self):
        self.client_id = getattr(settings, "GMAIL_OAUTH_CLIENT_ID", None)
        self.client_secret = getattr(settings, "GMAIL_OAUTH_CLIENT_SECRET", None)

    async def execute(self, parameters: Dict[str, Any], context: Dict[str, Any]) -> Dict[str, Any]:
        action = parameters.get("action")
        if action != "send_email":
            return {"status": "error", "message": f"Unknown Gmail action: {action}"}

        return await self.send_email(
            to=parameters.get("to"),
            subject=parameters.get("subject"),
            text=parameters.get("text") or parameters.get("body"),
            html=parameters.get("html"),
            from_email=parameters.get("from"),
            user_id=context.get("user_id"),
        )

    async def send_email(
        self,
        to: Optional[str],
        subject: Optional[str],
        text: Optional[str],
        html: Optional[str],
        from_email: Optional[str],
        user_id: Optional[int],
    ) -> Dict[str, Any]:
        if isinstance(to, (list, tuple)):
            to = ", ".join([str(item) for item in to if item])
        if not user_id:
            return {"status": "error", "message": "Missing user context for Gmail send"}
        if not to:
            return {"status": "error", "message": "Recipient 'to' is required for send_email"}
        if not subject:
            return {"status": "error", "message": "Subject is required for send_email"}
        if not text and not html:
            return {"status": "error", "message": "Email text or html content is required for send_email"}

        if not self.client_id or not self.client_secret:
            return {"status": "error", "message": "Gmail OAuth credentials are not configured"}

        integration = await self._get_integration(user_id)
        if not integration or not integration.is_connected:
            return {
                "status": "error",
                "message": "Gmail is not connected. Please connect Gmail in Settings > Integrations.",
                "action_required": "connect_gmail",
            }

        credentials = self._decrypt_credentials(integration.encrypted_credentials)
        access_token = credentials.get("access_token")
        refresh_token = credentials.get("refresh_token")
        expires_at = credentials.get("expires_at")

        if not access_token:
            return {
                "status": "error",
                "message": "Gmail access token missing. Please reconnect Gmail.",
                "action_required": "connect_gmail",
            }

        if self._is_expired(expires_at):
            refreshed = await self._refresh_access_token(integration, refresh_token, credentials)
            if not refreshed:
                return {
                    "status": "error",
                    "message": "Gmail token expired. Please reconnect Gmail.",
                    "action_required": "connect_gmail",
                }
            access_token = refreshed.get("access_token")

        message = EmailMessage()
        from_address = (
            from_email
            or credentials.get("gmail_address")
            or (integration.metadata or {}).get("gmail_address")
        )
        if not from_address:
            from_address = await self._get_user_email(user_id)
        if from_address:
            message["From"] = from_address
        message["To"] = to
        message["Subject"] = subject

        if html and text:
            message.set_content(text)
            message.add_alternative(html, subtype="html")
        elif html:
            message.set_content(html, subtype="html")
        else:
            message.set_content(text or "")

        raw = base64.urlsafe_b64encode(message.as_bytes()).decode("utf-8")

        payload = {"raw": raw}
        headers = {"Authorization": f"Bearer {access_token}"}

        async with httpx.AsyncClient(timeout=20) as client:
            response = await client.post(self.SEND_URL, headers=headers, json=payload)

        if response.status_code == 401 and refresh_token:
            refreshed = await self._refresh_access_token(integration, refresh_token, credentials)
            if refreshed and refreshed.get("access_token"):
                headers["Authorization"] = f"Bearer {refreshed['access_token']}"
                async with httpx.AsyncClient(timeout=20) as client:
                    response = await client.post(self.SEND_URL, headers=headers, json=payload)

        if response.status_code in (200, 202):
            data = response.json()
            return {
                "status": "success",
                "id": data.get("id"),
                "message": "Email sent successfully",
            }

        logger.error("Gmail send failed: %s", response.text)
        return {
            "status": "error",
            "message": "Failed to send email via Gmail",
            "details": response.text,
        }

    async def _get_integration(self, user_id: int) -> Optional[UserIntegration]:
        return await sync_to_async(
            lambda: UserIntegration.objects.filter(user_id=user_id, integration_type="gmail").first()
        )()

    async def _get_user_email(self, user_id: int) -> Optional[str]:
        User = get_user_model()
        return await sync_to_async(
            lambda: User.objects.filter(pk=user_id).values_list("email", flat=True).first()
        )()

    def _decrypt_credentials(self, encrypted: Optional[str]) -> Dict[str, Any]:
        if not encrypted:
            return {}
        try:
            decrypted = TokenEncryption.safe_decrypt(encrypted, default="{}")
            return json.loads(decrypted or "{}")
        except Exception as exc:
            logger.warning("Failed to decrypt Gmail credentials: %s", exc)
            return {}

    def _is_expired(self, expires_at: Optional[int]) -> bool:
        if not expires_at:
            return False
        try:
            return time.time() >= int(expires_at) - 60
        except Exception:
            return False

    async def _refresh_access_token(
        self,
        integration: UserIntegration,
        refresh_token: Optional[str],
        credentials: Dict[str, Any],
    ) -> Optional[Dict[str, Any]]:
        if not refresh_token:
            return None

        data = {
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
            "client_id": self.client_id,
            "client_secret": self.client_secret,
        }

        async with httpx.AsyncClient(timeout=20) as client:
            response = await client.post(self.TOKEN_URL, data=data)

        if response.status_code != 200:
            logger.error("Gmail token refresh failed: %s", response.text)
            return None

        payload = response.json()
        access_token = payload.get("access_token")
        expires_in = payload.get("expires_in")
        if not access_token:
            return None

        credentials["access_token"] = access_token
        if expires_in:
            credentials["expires_at"] = int(time.time()) + int(expires_in)

        await sync_to_async(self._save_credentials)(integration, credentials)
        return credentials

    def _save_credentials(self, integration: UserIntegration, credentials: Dict[str, Any]) -> None:
        integration.encrypted_credentials = TokenEncryption.encrypt(json.dumps(credentials))
        integration.is_connected = True
        integration.save(update_fields=["encrypted_credentials", "is_connected", "updated_at"])
