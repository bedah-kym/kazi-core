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
from .base_connector import BaseConnector
from .connectors.whatsapp_connector import WhatsAppConnector
from .connectors.intersend_connector import IntersendPayConnector
from .connectors.mailgun_connector import MailgunConnector

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
        # Import travel connectors
        from .connectors.travel_buses_connector import TravelBusesConnector
        from .connectors.travel_hotels_connector import TravelHotelsConnector
        from .connectors.travel_flights_connector import TravelFlightsConnector
        from .connectors.travel_transfers_connector import TravelTransfersConnector
        from .connectors.travel_events_connector import TravelEventsConnector
        from .connectors.itinerary_connector import ItineraryConnector
        
        self.connectors = {
            # Existing connectors
            "find_jobs": UpworkConnector(),
            "schedule_meeting": CalendarConnector(),
            "check_payments": StripeConnector(),
            "search_info": SearchConnector(),
            "get_weather": WeatherConnector(),
            "search_gif": GiphyConnector(),
            "convert_currency": CurrencyConnector(),
            "send_whatsapp": WhatsAppConnector(),
            "payment_action": IntersendPayConnector(),
            "send_email": MailgunConnector(),
            "set_reminder": ReminderConnector(),
            
            # Travel planner connectors
            "search_buses": TravelBusesConnector(),
            "search_hotels": TravelHotelsConnector(),
            "search_flights": TravelFlightsConnector(),
            "search_transfers": TravelTransfersConnector(),
            "search_events": TravelEventsConnector(),
            "create_itinerary": ItineraryConnector(),
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
# CONNECTORS
# ============================================

class BaseConnector:
    """Base class for all connectors"""
    
    async def execute(self, parameters: Dict, context: Dict) -> Any:
        raise NotImplementedError


class UpworkConnector(BaseConnector):
    """Mock Upwork API connector - returns fake job listings"""
    
    async def execute(self, parameters: Dict, context: Dict) -> List[Dict]:
        """Search for jobs on Upwork"""
        logger.info(f"UpworkConnector called with: {parameters}")
        
        import asyncio
        await asyncio.sleep(0.5)
        
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
        ]
        
        return {"jobs": jobs, "total": len(jobs), "query": query}


class CalendarConnector(BaseConnector):
    """Real Calendly connector using CalendlyProfile"""
    
    async def execute(self, parameters: Dict, context: Dict) -> Dict:
        """Execute Calendly actions"""
        from users.models import CalendlyProfile
        from django.contrib.auth import get_user_model
        import requests
        
        User = get_user_model()
        user_id = context.get("user_id")
        action = parameters.get("action", "check_availability")
        target_user_name = parameters.get("target_user")
        
        try:
            try:
                user = await sync_to_async(User.objects.get)(pk=user_id)
            except User.DoesNotExist:
                return {"status": "error", "message": "User not found"}

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

            if action == "schedule_meeting":
                if target_user_name:
                    target_username = target_user_name.lstrip('@')
                    try:
                        target_user = await sync_to_async(User.objects.get)(username=target_username)
                        target_profile = await sync_to_async(lambda: getattr(target_user, 'calendly', None))()
                        
                        if not target_profile or not await sync_to_async(lambda: target_profile.is_connected)():
                            return {"status": "error", "message": f"User @{target_username} has not connected their Calendly yet."}
                        
                        booking_link = await sync_to_async(lambda: target_profile.booking_link)()
                        return {"status": "success", "type": "booking_link", "booking_link": booking_link, "message": f"Here is the booking link for @{target_username}"}
                    except User.DoesNotExist:
                        return {"status": "error", "message": f"User @{target_username} not found."}
                else:
                    booking_link = await sync_to_async(lambda: profile.booking_link)()
                    if not booking_link:
                        return {"status": "error", "message": "You don't have a booking link configured."}
                    return {"status": "success", "type": "booking_link", "booking_link": booking_link, "message": "Here is your booking link."}

            access_token = await sync_to_async(profile.get_access_token)()
            if not access_token:
                return {"status": "error", "message": "Could not retrieve access token. Please reconnect Calendly.", "action_required": "connect_calendly"}
                
            headers = {'Authorization': f'Bearer {access_token}'}
            user_uri = await sync_to_async(lambda: profile.calendly_user_uri)()
            
            def fetch_events(token=None):
                req_headers = headers
                if token:
                    req_headers = {'Authorization': f'Bearer {token}'}
                return requests.get('https://api.calendly.com/scheduled_events', headers=req_headers, params={'user': user_uri, 'status': 'active', 'sort': 'start_time:asc'})
            
            response = await sync_to_async(fetch_events)()
            
            if response.status_code == 401:
                logger.info("Calendly token expired. Attempting refresh...")
                new_token = await self._refresh_token(profile)
                if new_token:
                    response = await sync_to_async(fetch_events)(new_token)
                else:
                    return {"status": "error", "message": "Calendly authorization failed. Please reconnect.", "action_required": "connect_calendly"}
            
            if response.status_code != 200:
                logger.error(f"Calendly API error: {response.text}")
                return {"status": "error", "message": "Failed to fetch Calendly events."}
                
            data = response.json()
            events = data.get('collection', [])
            
            formatted_events = []
            for event in events[:5]:
                formatted_events.append({"start": event.get('start_time'), "title": event.get('name'), "url": event.get('uri')})
                
            return {"status": "success", "type": "events", "events": formatted_events, "message": f"You have {len(formatted_events)} upcoming meetings." if formatted_events else "You have no upcoming meetings scheduled."}

        except Exception as e:
            logger.error(f"CalendarConnector error: {e}")
            return {"status": "error", "message": f"An error occurred: {str(e)}"}

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
                return requests.post('https://auth.calendly.com/oauth/token', data={'grant_type': 'refresh_token', 'refresh_token': refresh_token, 'client_id': settings.CALENDLY_CLIENT_ID, 'client_secret': settings.CALENDLY_CLIENT_SECRET})
            
            response = await sync_to_async(do_refresh)()
            
            if response.status_code == 200:
                data = response.json()
                new_access = data.get('access_token')
                new_refresh = data.get('refresh_token')
                
                def update_profile():
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
                {"id": "pay_001", "amount": 500.00, "status": "completed", "date": "2025-01-08", "description": "Python Development Project"},
                {"id": "pay_002", "amount": 750.50, "status": "pending", "date": "2025-01-10", "description": "API Integration Work"}
            ]
        }


class SearchConnector(BaseConnector):
    """Web search connector using LLM capabilities"""
    
    def __init__(self):
        from .llm_client import get_llm_client
        self.llm = get_llm_client()
    
    async def execute(self, parameters: Dict, context: Dict) -> Dict:
        """Perform web search with strict rate limiting"""
        from django.core.cache import cache
        from datetime import datetime
        
        user_id = context.get("user_id")
        query = parameters.get("query")
        
        if not query:
            return {"error": "No search query provided"}

        # RATE LIMIT CHECK
        if user_id:
            today = datetime.now().strftime("%Y-%m-%d")
            limit_key = f"search_limit:{user_id}:{today}"
            current_count = cache.get(limit_key, 0)
            
            if current_count >= 10:
                return {
                    "results": [],
                    "summary": "Daily search limit reached (10/10). Please try again tomorrow.",
                    "error": "rate_limit_exceeded"
                }
            
        if not self.llm.anthropic_key:
            logger.warning("Search requested but Anthropic key missing.")
            return {"results": [], "summary": "I cannot browse the live web right now.", "source": "system_fallback"}
            
        try:
            # Increment usage
            if user_id:
                cache.incr(limit_key) if cache.get(limit_key) else cache.set(limit_key, 1, 86400)

            system_prompt = "You are a helpful research assistant."
            response = await self.llm.generate_text(system_prompt=system_prompt, user_prompt=f"Search for: {query}", temperature=0.7)
            return {"results": [{"title": "Search Result", "snippet": response[:200] + "..."}], "summary": response, "source": "claude_search"}
        except Exception as e:
            logger.error(f"Search failed: {e}")
            return {"results": [{"title": "Error", "snippet": "Search functionality temporarily unavailable."}], "summary": "Search failed.", "error": str(e)}


class WeatherConnector(BaseConnector):
    """Weather connector using OpenWeatherMap API"""
    
    async def execute(self, parameters: Dict, context: Dict) -> Dict:
        """Get weather for a city"""
        from django.conf import settings
        
        city = parameters.get("city", parameters.get("location", "Nairobi"))
        api_key = getattr(settings, 'OPENWEATHER_API_KEY', '')
        
        if not api_key:
            logger.warning("Weather requested but OPENWEATHER_API_KEY not configured")
            return {"status": "error", "message": "Weather service is not configured. Please add OPENWEATHER_API_KEY to your environment."}
        
        try:
            url = "https://api.openweathermap.org/data/2.5/weather"
            params = {"q": city, "appid": api_key, "units": "metric"}
            
            async with httpx.AsyncClient(timeout=10.0) as client:
                response = await client.get(url, params=params)
                
                if response.status_code == 404:
                    return {"status": "error", "message": f"City '{city}' not found."}
                
                if response.status_code != 200:
                    logger.error(f"OpenWeatherMap error: {response.text}")
                    return {"status": "error", "message": "Failed to fetch weather data."}
                
                data = response.json()
                weather = data.get("weather", [{}])[0]
                main = data.get("main", {})
                wind = data.get("wind", {})
                
                return {
                    "status": "success",
                    "city": data.get("name", city),
                    "country": data.get("sys", {}).get("country", ""),
                    "temperature": round(main.get("temp", 0), 1),
                    "feels_like": round(main.get("feels_like", 0), 1),
                    "humidity": main.get("humidity", 0),
                    "description": weather.get("description", "").capitalize(),
                    "wind_speed": round(wind.get("speed", 0) * 3.6, 1),
                    "message": f"🌡️ {data.get('name', city)}: {round(main.get('temp', 0), 1)}°C, {weather.get('description', '').capitalize()}. Humidity: {main.get('humidity', 0)}%, Wind: {round(wind.get('speed', 0) * 3.6, 1)} km/h"
                }
                
        except Exception as e:
            logger.error(f"Weather fetch error: {e}")
            return {"status": "error", "message": f"Weather lookup failed: {str(e)}"}


class GiphyConnector(BaseConnector):
    """GIPHY connector for searching and returning GIFs"""
    
    async def execute(self, parameters: Dict, context: Dict) -> Dict:
        """Search GIPHY for a GIF"""
        from django.conf import settings
        import random
        
        query = parameters.get("query", parameters.get("search", "funny"))
        api_key = getattr(settings, 'GIPHY_API_KEY', '')
        
        if not api_key:
            logger.warning("GIPHY requested but GIPHY_API_KEY not configured")
            return {"status": "error", "message": "GIF service is not configured. Please add GIPHY_API_KEY to your environment."}
        
        try:
            url = "https://api.giphy.com/v1/gifs/search"
            params = {"api_key": api_key, "q": query, "limit": 10, "rating": "pg-13"}
            
            async with httpx.AsyncClient(timeout=10.0) as client:
                response = await client.get(url, params=params)
                
                if response.status_code != 200:
                    logger.error(f"GIPHY error: {response.text}")
                    return {"status": "error", "message": "Failed to search for GIFs."}
                
                data = response.json()
                gifs = data.get("data", [])
                
                if not gifs:
                    return {"status": "error", "message": f"No GIFs found for '{query}'."}
                
                gif = random.choice(gifs)
                images = gif.get("images", {})
                fixed = images.get("fixed_height", {})
                original = images.get("original", {})
                
                return {
                    "status": "success",
                    "query": query,
                    "title": gif.get("title", ""),
                    "url": fixed.get("url", original.get("url", "")),
                    "giphy_url": gif.get("url", ""),
                    "message": f"🎬 Here's a GIF for '{query}'!",
                    "embed_html": f'<img src="{fixed.get("url", original.get("url", ""))}" alt="{query} GIF" style="max-width: 300px; border-radius: 8px;" />'
                }
                
        except Exception as e:
            logger.error(f"GIPHY fetch error: {e}")
            return {"status": "error", "message": f"GIF search failed: {str(e)}"}


class CurrencyConnector(BaseConnector):
    """Currency conversion connector using ExchangeRate-API"""
    
    async def execute(self, parameters: Dict, context: Dict) -> Dict:
        """Convert currency"""
        from django.conf import settings
        
        try:
            amount = float(parameters.get("amount", 1))
        except (ValueError, TypeError):
            return {"status": "error", "message": "Invalid amount. Please provide a valid number."}
            
        from_currency = parameters.get("from_currency", parameters.get("from", "USD")).upper()
        to_currency = parameters.get("to_currency", parameters.get("to", "KES")).upper()
        api_key = getattr(settings, 'EXCHANGE_RATE_API_KEY', '')
        
        if not api_key:
            logger.warning("Currency conversion requested but EXCHANGE_RATE_API_KEY not configured")
            return {"status": "error", "message": "Currency service is not configured. Please add EXCHANGE_RATE_API_KEY to your environment."}
        
        try:
            url = f"https://v6.exchangerate-api.com/v6/{api_key}/pair/{from_currency}/{to_currency}/{amount}"
            
            async with httpx.AsyncClient(timeout=10.0) as client:
                response = await client.get(url)
                
                if response.status_code != 200:
                    logger.error(f"ExchangeRate-API error: {response.text}")
                    return {"status": "error", "message": "Failed to fetch exchange rates."}
                
                data = response.json()
                
                if data.get("result") != "success":
                    error_type = data.get("error-type", "unknown")
                    if error_type == "unsupported-code":
                        return {"status": "error", "message": "Currency code not supported. Use valid ISO codes like USD, EUR, KES."}
                    return {"status": "error", "message": f"Currency conversion failed: {error_type}"}
                
                conversion_result = data.get("conversion_result", 0)
                rate = data.get("conversion_rate", 0)
                
                return {
                    "status": "success",
                    "amount": amount,
                    "from_currency": from_currency,
                    "to_currency": to_currency,
                    "rate": round(rate, 4),
                    "result": round(conversion_result, 2),
                    "message": f"💱 {amount:,.2f} {from_currency} = {conversion_result:,.2f} {to_currency} (Rate: 1 {from_currency} = {rate:.4f} {to_currency})"
                }
                
        except Exception as e:
            logger.error(f"Currency conversion error: {e}")
            return {"status": "error", "message": f"Currency conversion failed: {str(e)}"}


class ReminderConnector(BaseConnector):
    """
    Sets reminders for the user
    Expects LLM to return ISO time or relative time string
    """
    
    async def execute(self, parameters: Dict, context: Dict) -> Dict:
        """Create a reminder"""
        from chatbot.models import Reminder, Chatroom
        from django.contrib.auth import get_user_model
        from django.utils import timezone
        import dateutil.parser
        from asgiref.sync import sync_to_async
        
        User = get_user_model()
        user_id = context.get("user_id")
        room_id = context.get("room_id")
        
        content = parameters.get("content", "Reminder")
        time_str = parameters.get("time")
        priority = parameters.get("priority", "medium")
        
        if not time_str:
            return {"status": "error", "message": "When should I remind you?"}
            
        try:
            # 1. Try ISO parsing (LLM should prefer this)
            try:
                scheduled_time = dateutil.parser.parse(time_str)
            except Exception:
                # 2. Fallback: simple check if it's a number (minutes)
                # In robust prod, use dateparser
                if "min" in time_str or time_str.isdigit():
                    minutes = int(''.join(filter(str.isdigit, time_str)))
                    scheduled_time = timezone.now() + timedelta(minutes=minutes)
                else:
                    return {"status": "error", "message": f"I couldn't understand the time '{time_str}'. Please use format like '10 minutes' or '5pm'."}
            
            # Ensure timezone aware
            if timezone.is_naive(scheduled_time):
                scheduled_time = timezone.make_aware(scheduled_time)
                
            if scheduled_time < timezone.now():
                # Assume tomorrow if time has passed today (simple heuristic)
                scheduled_time += timedelta(days=1)
            
            # Create Reminder
            user = await sync_to_async(User.objects.get)(pk=user_id)
            room = await sync_to_async(Chatroom.objects.get)(pk=room_id) if room_id else None
            
            reminder = await sync_to_async(Reminder.objects.create)(
                user=user,
                room=room,
                content=content,
                scheduled_time=scheduled_time,
                priority=priority,
                status='pending'
            )
            
            # Format friendly time display
            local_time = scheduled_time.strftime("%I:%M %p")
            
            return {
                "status": "success",
                "message": f"✅ I've set a reminder: '{content}' for {local_time}.",
                "reminder_id": reminder.id,
                "timestamp": scheduled_time.isoformat()
            }
            
        except Exception as e:
            logger.error(f"Reminder error: {e}")
            return {"status": "error", "message": "Failed to set reminder."}


_router = None

def get_mcp_router() -> MCPRouter:
    """Get or create the global MCP router instance"""
    global _router
    if _router is None:
        _router = MCPRouter()
    return _router


async def route_intent(intent: Dict, user_context: Dict) -> Dict:
    """Convenience function to route an intent"""
    router = get_mcp_router()
    return await router.route(intent, user_context)