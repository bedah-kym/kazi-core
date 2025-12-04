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
import httpx

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
    """Real Calendly connector using CalendlyProfile"""
    
    async def execute(self, parameters: Dict, context: Dict) -> Dict:
        """
        Execute Calendly actions:
        - check_availability: Get scheduled events
        - schedule_meeting: Get booking link
        """
        from users.models import CalendlyProfile
        from django.contrib.auth import get_user_model
        import requests
        
        User = get_user_model()
        user_id = context.get("user_id")
        action = parameters.get("action", "check_availability") # Default action
        
        target_user_name = parameters.get("target_user")
        
        try:
            # Get current user
            try:
                user = await sync_to_async(User.objects.get)(pk=user_id)
            except User.DoesNotExist:
                return {"status": "error", "message": "User not found"}

            # Get profile
            try:
                profile = await sync_to_async(lambda: getattr(user, 'calendly', None))()
            except Exception:
                profile = None

            if not profile or not await sync_to_async(lambda: profile.is_connected)():
                return {
                    "status": "error", 
                    "message": "You are not connected to Calendly. Please connect first.",
                    "action_required": "connect_calendly"
                }

            # Handle "schedule_meeting"
            if action == "schedule_meeting":
                if target_user_name:
                    # Schedule with another user
                    target_username = target_user_name.lstrip('@')
                    try:
                        target_user = await sync_to_async(User.objects.get)(username=target_username)
                        target_profile = await sync_to_async(lambda: getattr(target_user, 'calendly', None))()
                        
                        if not target_profile or not await sync_to_async(lambda: target_profile.is_connected)():
                            return {
                                "status": "error",
                                "message": f"User @{target_username} has not connected their Calendly yet."
                            }
                        
                        booking_link = await sync_to_async(lambda: target_profile.booking_link)()
                        return {
                            "status": "success",
                            "type": "booking_link",
                            "booking_link": booking_link,
                            "message": f"Here is the booking link for @{target_username}"
                        }
                    except User.DoesNotExist:
                        return {
                            "status": "error", 
                            "message": f"User @{target_username} not found."
                        }
                else:
                    # Return own booking link
                    booking_link = await sync_to_async(lambda: profile.booking_link)()
                    if not booking_link:
                         return {
                            "status": "error",
                            "message": "You don't have a booking link configured. Please check your Calendly settings."
                        }
                    return {
                        "status": "success",
                        "type": "booking_link",
                        "booking_link": booking_link,
                        "message": "Here is your booking link."
                    }

            # Handle "check availability" / "list meetings"
            access_token = await sync_to_async(profile.get_access_token)()
            if not access_token:
                 return {
                    "status": "error", 
                    "message": "Could not retrieve access token. Please reconnect Calendly.",
                    "action_required": "connect_calendly"
                }
                
            # Fetch events
            headers = {'Authorization': f'Bearer {access_token}'}
            user_uri = await sync_to_async(lambda: profile.calendly_user_uri)()
            
            # Run request in thread to avoid blocking
            def fetch_events(token=None):
                req_headers = headers
                if token:
                    req_headers = {'Authorization': f'Bearer {token}'}
                    
                return requests.get(
                    'https://api.calendly.com/scheduled_events', 
                    headers=req_headers, 
                    params={'user': user_uri, 'status': 'active', 'sort': 'start_time:asc'}
                )
            
            response = await sync_to_async(fetch_events)()
            
            # Handle 401 - Token Expired
            if response.status_code == 401:
                logger.info("Calendly token expired. Attempting refresh...")
                new_token = await self._refresh_token(profile)
                
                if new_token:
                    # Retry with new token
                    response = await sync_to_async(fetch_events)(new_token)
                else:
                     return {
                        "status": "error", 
                        "message": "Calendly authorization failed. Please reconnect.",
                        "action_required": "connect_calendly"
                    }
            
            if response.status_code != 200:
                logger.error(f"Calendly API error: {response.text}")
                return {
                    "status": "error",
                    "message": "Failed to fetch Calendly events."
                }
                
            data = response.json()
            events = data.get('collection', [])
            
            # Format events
            formatted_events = []
            for event in events[:5]: # Top 5
                start_time = event.get('start_time')
                name = event.get('name')
                formatted_events.append({
                    "start": start_time,
                    "title": name,
                    "url": event.get('uri')
                })
                
            return {
                "status": "success",
                "type": "events",
                "events": formatted_events,
                "message": f"You have {len(formatted_events)} upcoming meetings." if formatted_events else "You have no upcoming meetings scheduled."
            }

        except Exception as e:
            logger.error(f"CalendarConnector error: {e}")
            return {
                "status": "error",
                "message": f"An error occurred: {str(e)}"
            }

    async def _refresh_token(self, profile):
        """Refresh the Calendly access token"""
        from django.conf import settings
        import requests
        
        refresh_token = await sync_to_async(profile.get_refresh_token)()
        if not refresh_token:
            logger.error("No refresh token available")
            return None
            
        try:
            def do_refresh():
                return requests.post(
                    'https://auth.calendly.com/oauth/token',
                    data={
                        'grant_type': 'refresh_token',
                        'refresh_token': refresh_token,
                        'client_id': settings.CALENDLY_CLIENT_ID,
                        'client_secret': settings.CALENDLY_CLIENT_SECRET
                    }
                )
            
            response = await sync_to_async(do_refresh)()
            
            if response.status_code == 200:
                data = response.json()
                new_access = data.get('access_token')
                new_refresh = data.get('refresh_token')
                
                # Update profile
                def update_profile():
                    # We need to re-encrypt and save
                    from cryptography.fernet import Fernet
                    import base64, hashlib
                    
                    secret = (settings.SECRET_KEY or 'changeme').encode('utf-8')
                    hash = hashlib.sha256(secret).digest()
                    fernet_key = base64.urlsafe_b64encode(hash)
                    f = Fernet(fernet_key)
                    
                    profile.encrypted_access_token = f.encrypt(new_access.encode('utf-8')).decode('utf-8')
                    if new_refresh:
                        profile.encrypted_refresh_token = f.encrypt(new_refresh.encode('utf-8')).decode('utf-8')
                    profile.save()
                    return new_access
                
                return await sync_to_async(update_profile)()
            else:
                logger.error(f"Token refresh failed: {response.text}")
                return None
                
        except Exception as e:
            logger.error(f"Error refreshing token: {e}")
            return None



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
    """
    Web search connector using LLM capabilities
    Falls back gracefully if Claude (Anthropic) is not available
    """
    
    def __init__(self):
        from .llm_client import get_llm_client
        self.llm = get_llm_client()
    
    async def execute(self, parameters: Dict, context: Dict) -> Dict:
        """
        Perform web search
        """
        query = parameters.get("query")
        if not query:
            return {"error": "No search query provided"}
            
        # Check if we have Claude capabilities (Anthropic key)
        if not self.llm.anthropic_key:
            logger.warning("Search requested but Anthropic key missing. HF fallback cannot search web.")
            return {
                "results": [],
                "summary": "I apologize, but I cannot browse the live web right now because my advanced search module (Claude) is not active. I can only answer based on my internal knowledge.",
                "source": "system_fallback"
            }
            
        # If we have Claude, use it for search
        try:
            system_prompt = "You are a helpful research assistant. Search the web for the user's query and provide a summary."
            response = await self.llm.generate_text(
                system_prompt=system_prompt,
                user_prompt=f"Search for: {query}",
                temperature=0.7
            )
            
            return {
                "results": [{"title": "Search Result", "snippet": response[:200] + "..."}],
                "summary": response,
                "source": "claude_search"
            }
            
        except Exception as e:
            logger.error(f"Search failed: {e}")
            return {
                "results": [],
                "summary": "Search failed due to an internal error.",
                "error": str(e)
            }


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
    """
    router = get_mcp_router()
    return await router.route(intent, user_context)