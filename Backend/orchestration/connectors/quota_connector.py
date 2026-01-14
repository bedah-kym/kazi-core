
import logging
from ..base_connector import BaseConnector
from users.quota_service import QuotaService

logger = logging.getLogger(__name__)

class QuotaConnector(BaseConnector):
    """
    Connector for Quota and Usage Limits.
    Capabilities:
    - Check user quotas for search, actions, messages, and uploads.
    """
    
    def __init__(self):
        self.service = QuotaService()
        
    async def execute(self, intent: dict, user) -> dict:
        """
        Execute Quota actions.
        """
        action = intent.get("action")
        
        if action == "check_quotas":
            return await self.check_quotas(user)
            
        return {"error": f"Unknown Quota action: {action}"}

    async def check_quotas(self, user):
        try:
            if not user or not hasattr(user, 'id'):
                return {"status": "error", "message": "User not authenticated or invalid"}
                
            from asgiref.sync import sync_to_async
            quotas = await sync_to_async(self.service.get_user_quotas)(user.id)
            
            return {
                "status": "success",
                "data": quotas,
                "message": "Quotas retrieved successfully"
            }
                    
        except Exception as e:
            logger.error(f"Quota Connector Error: {str(e)}")
            return {"status": "error", "message": str(e)}
