
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
        self.api_key = os.environ.get('MAILGUN_API_KEY')
        self.domain = os.environ.get('MAILGUN_DOMAIN')
        self.base_url = f"https://api.mailgun.net/v3/{self.domain}" if self.domain else None
        
    async def execute(self, intent: dict, user) -> dict:
        """
        Execute Email actions.
        """
        action = intent.get("action")
        
        if action == "send_email":
            return await self.send_email(
                to=intent.get("to"),
                subject=intent.get("subject"),
                text=intent.get("text"),
                html=intent.get("html"),
                from_email=intent.get("from")
            )
            
        return {"error": f"Unknown Mailgun action: {action}"}

    async def send_email(self, to, subject, text, html=None, from_email=None):
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
                        "message": "Email sent successfully"
                    }
                else:
                    logger.error(f"Mailgun Error: {response.text}")
                    return {"error": f"Failed to send email: {response.status_code}", "details": response.text}
                    
        except Exception as e:
            logger.error(f"Mailgun Connector Error: {str(e)}")
            return {"error": str(e)}
