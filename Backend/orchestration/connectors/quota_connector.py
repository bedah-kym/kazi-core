
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
        
    async def execute(self, parameters: dict, context: dict) -> dict:
        """
        Execute Quota actions.
        """
        # Always return quotas since it's routed here for check_quotas
        return await self.check_quotas(context)

    async def check_quotas(self, context):
        try:
            user_id = context.get("user_id")
            if not user_id:
                return {"status": "error", "message": "User ID not found in context"}
                
            from asgiref.sync import sync_to_async
            quotas = await sync_to_async(self.service.get_user_quotas)(user_id)
            
            return {
                "status": "success",
                "data": quotas,
                "message": "Quotas retrieved successfully"
            }
                    
        except Exception as e:
            logger.error(f"Quota Connector Error: {str(e)}")
            return {"status": "error", "message": str(e)}
