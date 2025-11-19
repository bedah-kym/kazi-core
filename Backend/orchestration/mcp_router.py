"""
MCP (Multi-Control Protocol) Router for Mathia Orchestration
Routes parsed intents to appropriate connectors/tools
"""
import json
import logging
from typing import Dict, List, Optional, Any
from datetime import datetime, timedelta
from django.core.cache import cache
from django_redis import get_redis_connection
from asgiref.sync import sync_to_async

logger = logging.getLogger(__name__)


class MCPRouter:
    """
    Central orchestration router that:
    1. Validates intents
    2. Routes to connectors
    3. Manages context and auth
    4. Returns structured data
    """
    
    def __init__(self):
        self.connectors = {
            "find_jobs": UpworkConnector(),
            "schedule_meeting": CalendarConnector(),
            "check_payments": StripeConnector(),
            "search_info": SearchConnector(),
        }
    
    async def route(self, intent: Dict, user_context: Dict) -> Dict:
        """
        Route an intent to the appropriate connector
        
        Args:
            intent: Parsed intent from intent_parser
            user_context: User ID, room, preferences, etc.
            
        Returns:
            Dict with status, data, and metadata
        """
        try:
            action = intent.get("action")
            
            # Validate
            validation = await self._validate_request(intent, user_context)
            if not validation["valid"]:
                return {
                    "status": "error",
                    "message": validation["reason"],
                    "data": None
                }
            
            # Get connector
            connector = self.connectors.get(action)
            if not connector:
                logger.warning(f"No connector for action: {action}")
                return {
                    "status": "error",
                    "message": f"Action '{action}' not supported yet",
                    "data": None
                }
            
            # Execute with timeout
            logger.info(f"Routing to connector: {action}")
            result = await connector.execute(intent["parameters"], user_context)
            
            # Cache result
            await self._cache_result(intent, user_context, result)
            
            return {
                "status": "success",
                "action": action,
                "data": result,
                "metadata": {
                    "cached": False,
                    "timestamp": datetime.now().isoformat(),
                    "connector": connector.__class__.__name__
                }
            }
            
        except Exception as e:
            logger.error(f"MCP routing error: {e}")
            return {
                "status": "error",
                "message": str(e),
                "data": None
            }
    
    async def _validate_request(self, intent: Dict, context: Dict) -> Dict:
        """Validate request against rate limits, auth, etc."""
        
        # Rate limit check (100 requests per hour per user)
        user_id = context.get("user_id")
        cache_key = f"mcp_rate:{user_id}"
        
        current = cache.get(cache_key, 0)
        if current >= 100:
            return {
                "valid": False,
                "reason": "Rate limit exceeded. Try again in an hour."
            }
        
        cache.set(cache_key, current + 1, 3600)  # 1 hour TTL
        
        # TODO: Add auth checks when real APIs are connected
        # if intent["action"] == "find_jobs":
        #     if not await self._check_upwork_auth(user_id):
        #         return {"valid": False, "reason": "Upwork not connected"}
        
        return {"valid": True, "reason": None}
    
    async def _cache_result(self, intent: Dict, context: Dict, result: Any):
        """Cache results in Redis for quick retrieval"""
        try:
            cache_key = f"mcp_cache:{intent['action']}:{context['user_id']}"
            redis = get_redis_connection("default")
            
            # Store with 5 min TTL
            cache_data = json.dumps({
                "intent": intent,
                "result": result,
                "timestamp": datetime.now().isoformat()
            })
            
            await sync_to_async(redis.setex)(cache_key, 300, cache_data)
        except Exception as e:
            logger.error(f"Cache error: {e}")


# ============================================
# MOCK CONNECTORS (Replace with real APIs later)
# ============================================

class BaseConnector:
    """Base class for all connectors"""
    
    async def execute(self, parameters: Dict, context: Dict) -> Any:
        raise NotImplementedError


class UpworkConnector(BaseConnector):
    """Mock Upwork API connector - returns fake job listings"""
    
    async def execute(self, parameters: Dict, context: Dict) -> List[Dict]:
        """
        Search for jobs on Upwork
        
        Parameters:
            query: Search keywords
            budget_min: Minimum budget
            budget_max: Maximum budget
        """
        logger.info(f"UpworkConnector called with: {parameters}")
        
        # Simulate API delay
        import asyncio
        await asyncio.sleep(0.5)
        
        # Mock data
        query = parameters.get("query", "Python")
        budget_max = parameters.get("budget_max", 5000)
        
        jobs = [
            {
                "id": "job_001",
                "title": f"{query} Developer Needed",
                "budget": f"${budget_max - 200}-${budget_max}",
                "description": f"Looking for experienced {query} developer for 2-week project",
                "posted": "2 hours ago",
                "proposals": 8,
                "client_rating": 4.8,
                "url": "https://upwork.com/jobs/mock-001"
            },
            {
                "id": "job_002",
                "title": f"Senior {query} Engineer",
                "budget": f"${budget_max - 500}-${budget_max - 200}",
                "description": f"Full-time {query} position with benefits",
                "posted": "5 hours ago",
                "proposals": 15,
                "client_rating": 4.9,
                "url": "https://upwork.com/jobs/mock-002"
            },
            {
                "id": "job_003",
                "title": f"{query} API Integration",
                "budget": f"${budget_max // 2}-${budget_max - 100}",
                "description": f"Need help integrating {query} with our backend",
                "posted": "1 day ago",
                "proposals": 3,
                "client_rating": 4.5,
                "url": "https://upwork.com/jobs/mock-003"
            }
        ]
        
        return {
            "jobs": jobs,
            "total": len(jobs),
            "query": query
        }


class CalendarConnector(BaseConnector):
    """Mock Calendly connector - returns fake availability"""
    
    async def execute(self, parameters: Dict, context: Dict) -> Dict:
        """Get available meeting slots"""
        logger.info(f"CalendarConnector called with: {parameters}")
        
        # Mock available slots for next 3 days
        now = datetime.now()
        slots = []
        
        for day in range(1, 4):
            date = now + timedelta(days=day)
            for hour in [10, 14, 16]:
                slots.append({
                    "start": date.replace(hour=hour, minute=0).isoformat(),
                    "end": date.replace(hour=hour + 1, minute=0).isoformat(),
                    "available": True
                })
        
        return {
            "slots": slots[:5],  # Return first 5 slots
            "timezone": "UTC",
            "booking_url": "https://calendly.com/mathia/mock"
        }


class StripeConnector(BaseConnector):
    """Mock Stripe connector - returns fake payment data"""
    
    async def execute(self, parameters: Dict, context: Dict) -> Dict:
        """Get recent payments and balance"""
        logger.info(f"StripeConnector called with: {parameters}")
        
        return {
            "balance": 1250.50,
            "currency": "USD",
            "recent_payments": [
                {
                    "id": "pay_001",
                    "amount": 500.00,
                    "status": "completed",
                    "date": "2025-01-08",
                    "description": "Python Development Project"
                },
                {
                    "id": "pay_002",
                    "amount": 750.50,
                    "status": "pending",
                    "date": "2025-01-10",
                    "description": "API Integration Work"
                }
            ]
        }


class SearchConnector(BaseConnector):
    """Mock web search connector - returns fake search results"""
    
    async def execute(self, parameters: Dict, context: Dict) -> Dict:
        """Search the web for information"""
        query = parameters.get("query", "")
        logger.info(f"SearchConnector called with query: {query}")
        
        return {
            "results": [
                {
                    "title": f"Everything you need to know about {query}",
                    "snippet": f"Comprehensive guide covering {query} in detail...",
                    "url": "https://example.com/article1"
                },
                {
                    "title": f"{query} - Wikipedia",
                    "snippet": f"Wikipedia article about {query}...",
                    "url": "https://en.wikipedia.org/wiki/mock"
                }
            ],
            "query": query
        }


# ============================================
# SINGLETON AND CONVENIENCE FUNCTIONS
# ============================================

_router = None

def get_mcp_router() -> MCPRouter:
    """Get or create the global MCP router instance"""
    global _router
    if _router is None:
        _router = MCPRouter()
    return _router


async def route_intent(intent: Dict, user_context: Dict) -> Dict:
    """
    Convenience function to route an intent
    
    Usage in consumers:
        from orchestration.mcp_router import route_intent
        
        result = await route_intent(intent, {
            "user_id": user.id,
            "room_id": room_id,
            "username": username
        })
        
        if result["status"] == "success":
            # Process result data
            jobs = result["data"]["jobs"]
    """
    router = get_mcp_router()
    return await router.route(intent, user_context)