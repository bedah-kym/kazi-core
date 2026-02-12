
import logging
import os
import httpx
from ..base_connector import BaseConnector

logger = logging.getLogger(__name__)

class MailgunConnector(BaseConnector):
    """
    Connector for Mailgun Email Service.
    Capabilities:
    - Send simple emails
    """
    
    def __init__(self):
        use_sandbox = os.environ.get('MAILGUN_USE_SANDBOX', '').lower() in ('1', 'true', 'yes')
        sandbox_domain = os.environ.get('MAILGUN_DOMAIN_SANDBOX')
        sandbox_key = os.environ.get('MAILGUN_API_KEY_SANDBOX')

        if use_sandbox:
            self.api_key = sandbox_key
            self.domain = sandbox_domain
            if not self.api_key or not self.domain:
                logger.warning("[Mailgun] Sandbox enabled but missing key/domain. Using mock send.")
        else:
            self.api_key = os.environ.get('MAILGUN_API_KEY')
            self.domain = os.environ.get('MAILGUN_DOMAIN')

        self.base_url = f"https://api.mailgun.net/v3/{self.domain}" if self.domain else None
        self.use_sandbox = use_sandbox
        
    async def execute(self, parameters: dict, context: dict) -> dict:
        """
        Execute Email actions.
        """
        action = parameters.get("action")
        
        if action == "send_email":
            return await self.send_email(
                to=parameters.get("to"),
                subject=parameters.get("subject"),
                text=parameters.get("text"),
                html=parameters.get("html"),
                from_email=parameters.get("from")
            )
            
        return {"error": f"Unknown Mailgun action: {action}"}

    async def send_email(self, to, subject, text, html=None, from_email=None):
        if not to:
            return {"error": "Recipient 'to' is required for send_email"}
        if not subject:
            return {"error": "Subject is required for send_email"}
        if not text and not html:
            return {"error": "Email text or html content is required for send_email"}

        if not self.api_key or not self.domain:
            logger.warning("[Mailgun] Missing credentials. Mocking email send.")
            return {
                "status": "success", 
                "message": f"Simulated email to {to}: {subject}",
                "mock": True
            }
            
        if not from_email:
            from_email = f"KwikChat <mailgun@{self.domain}>"

        try:
            url = f"{self.base_url}/messages"
            auth = ("api", self.api_key)
            data = {
                "from": from_email,
                "to": to,
                "subject": subject,
                "text": text
            }
            
            if html:
                data["html"] = html
                
            async with httpx.AsyncClient() as client:
                response = await client.post(url, auth=auth, data=data)
                
                if response.status_code == 200:
                    return {
                        "status": "success", 
                        "id": response.json().get("id"),
                        "message": "Email sent successfully",
                        "sandbox": self.use_sandbox
                    }
                else:
                    logger.error(f"Mailgun Error: {response.text}")
                    return {
                        "error": f"Failed to send email: {response.status_code}",
                        "details": response.text,
                        "sandbox": self.use_sandbox
                    }
                    
        except Exception as e:
            logger.error(f"Mailgun Connector Error: {str(e)}")
            return {"error": str(e)}
