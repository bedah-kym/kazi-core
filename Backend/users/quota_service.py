import logging
from datetime import datetime
from django.core.cache import cache
from django.conf import settings

logger = logging.getLogger(__name__)

class QuotaService:
    """
    Service to report user usage quotas for rate-limited features.
    """
    
    # Define limits (centralized here for reference, though enforced elsewhere)
    LIMITS = {
        'search': 10,       # per day
        'actions': 100,     # per hour
        'messages': 30      # per minute
    }

    def get_user_quotas(self, user_id: int) -> dict:
        """
        Get current usage and limits for a user.
        """
        # 1. Search Limit (Daily)
        today = datetime.now().strftime("%Y-%m-%d")
        search_key = f"search_limit:{user_id}:{today}"
        search_used = cache.get(search_key, 0)
        
        # 2. MCP Actions Limit (Hourly)
        action_key = f"mcp_rate:{user_id}"
        action_used = cache.get(action_key, 0)
        
        # 3. Chat Rate Limit (Minute)
        current_minute = datetime.now().strftime('%Y-%m-%d-%H-%M')
        msg_key = f"rate_limit:{user_id}:{current_minute}"
        msg_used = cache.get(msg_key, 0)

        # Calculate Percentages & Status
        def get_status(used, limit):
            pct = (used / limit) * 100
            if pct >= 100: return 'exhausted', 'red'
            if pct >= 80: return 'critical', 'orange'
            if pct >= 50: return 'warning', 'yellow'
            return 'good', 'green'

        s_status, s_color = get_status(search_used, self.LIMITS['search'])
        a_status, a_color = get_status(action_used, self.LIMITS['actions'])
        m_status, m_color = get_status(msg_used, self.LIMITS['messages'])

        return {
            "search": {
                "name": "Online Searches",
                "used": search_used, 
                "limit": self.LIMITS['search'],
                "unit": "per day",
                "status": s_status,
                "color": s_color,
                "reset": "Midnight"
            },
            "actions": {
                "name": "AI Actions",
                "used": action_used, 
                "limit": self.LIMITS['actions'],
                "unit": "per hour",
                "status": a_status,
                "color": a_color,
                "reset": "Rolling 1 hour"
            },
            "messages": {
                "name": "Chat Messages",
                "used": msg_used, 
                "limit": self.LIMITS['messages'],
                "unit": "per minute",
                "status": m_status,
                "color": m_color,
                "reset": "Rolling 1 min"
            }
        }
