"""
Tool Router (legacy filename: mcp_router.py).

Routes parsed intents to appropriate connectors/tools.

DEPRECATION NOTE — v0.4 M2-3 Phase 1
====================================
The "MCP" name predates Anthropic's now-standard Model Context Protocol
and is a source of confusion for OSS readers. The runtime responsibility
is unchanged, but this module is being prepared for two follow-up moves
in v0.5:

1. **File rename** to `tool_router.py`. `mcp_router.py` will become a
   one-line re-export shim for one cycle, then be removed.
2. **Inline connector split** — the six connectors defined below
   (CalendarConnector, SearchConnector, WeatherConnector,
   GiphyConnector, CurrencyConnector, ReminderConnector) move into
   per-file modules under `Backend/orchestration/connectors/` to match
   the connector layout of every other connector in the project.

If you are writing new code: import the connector classes from their
eventual home (`from orchestration.connectors.<name>_connector import ...`)
once that lands; for now they live here and are re-exported by the
connector_registry path documented in `docs/contracts/tool-schema.md`.
"""
import json
import logging
import threading
from typing import Dict, List, Optional, Any
from datetime import datetime, timedelta
from django.core.cache import cache
from django_redis import get_redis_connection
from asgiref.sync import sync_to_async
import httpx
from users.encryption import TokenEncryption
from django.contrib.auth import get_user_model
from orchestration.action_catalog import (
    get_action_definition,
    get_capability_gate,
    get_required_params,
    is_high_risk_action,
    requires_confirmation as catalog_requires_confirmation,
    resolve_action_alias,
    validate_router_mappings,
)
from orchestration.contracts import build_orchestration_result
from orchestration.user_preferences import enforce_agent_caps
from .base_connector import BaseConnector
from .security_policy import (
    conservative_capability_prefs,
    sanitize_parameters,
    should_block_action,
    user_has_room_access,
)

logger = logging.getLogger(__name__)


class MCPRouter:
    """
    Central orchestration router that:
    1. Validates intents
    2. Routes to connectors
    3. Manages context and auth
    4. Returns structured data
    """

    # Dialog state helps fill missing parameters when users send follow-ups like
    # "same dates" or provide only the destination after a previous travel query.
    DIALOG_STATE_TTL_SECONDS = 60 * 60 * 6  # 6 hours
    TRAVEL_ACTIONS = {
        "search_buses", "search_hotels", "search_flights",
        "search_transfers", "search_events",
        "create_itinerary", "add_to_itinerary", "view_itinerary",
        "book_travel_item",
    }
    DEFAULT_CAPABILITY_PREFS = {
        "capability_mode": "custom",
        "allow_web_search": True,
        "allow_travel": True,
        "allow_payments": True,
        "allow_reminders": True,
        "allow_whatsapp": True,
        "allow_email": True,
        "allow_calendar": True,
    }
    RATE_LIMIT_PER_HOUR = 100
    _local_rate_lock = threading.Lock()
    _local_rate_counters: Dict[str, int] = {}

    def __init__(self):
        # Single source of truth for action -> connector resolution lives in
        # `connector_registry`. It composes built-in connectors, directory-scanned
        # new-style BaseConnector subclasses, and pip-installed entry points.
        from .connector_registry import discover_connectors

        self.connectors = dict(discover_connectors())
        self._validate_action_connector_integrity()

    def _validate_action_connector_integrity(self) -> None:
        missing, extra = validate_router_mappings(self.connectors.keys())
        if missing:
            logger.warning(
                "Action catalog has entries with no registered connector: %s. "
                "These actions will not be available. Check env vars (e.g. TELEGRAM_BOT_TOKEN).",
                ", ".join(missing),
            )
        if extra:
            logger.warning(
                "Router has connector mappings not present in action catalog: %s",
                ", ".join(extra),
            )

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
            raw_action = intent.get("action")
            action = resolve_action_alias(raw_action)
            risk_level = (get_action_definition(action) or {}).get("risk_level", "low")
            requires_confirmation = catalog_requires_confirmation(action)

            # Validate
            validation = await self._validate_request(intent, user_context)
            if not validation["valid"]:
                return build_orchestration_result(
                    status="error",
                    action=action,
                    risk_level=risk_level,
                    requires_confirmation=requires_confirmation,
                    clarification_prompt=validation["reason"] or "",
                    data={},
                    reason=validation["reason"] or "",
                    next_step="clarify",
                )

            required_params = get_required_params(action)
            params = intent.get("parameters") if isinstance(intent.get("parameters"), dict) else {}
            missing_slots = intent.get("missing_slots") or []
            if not isinstance(missing_slots, list):
                missing_slots = []
            for key in required_params:
                if key not in missing_slots and not params.get(key):
                    missing_slots.append(key)
            clarified = str(intent.get("clarifying_question") or "").strip()
            if missing_slots and is_high_risk_action(action):
                question = clarified or f"Before I proceed, I still need: {missing_slots[0].replace('_', ' ')}."
                return build_orchestration_result(
                    status="needs_clarification",
                    action=action,
                    risk_level=risk_level,
                    requires_confirmation=requires_confirmation,
                    clarification_prompt=question,
                    data={"missing_slots": missing_slots},
                    reason="missing_required_parameters",
                    next_step="clarify",
                )
            confidence = intent.get("confidence")
            try:
                confidence_value = float(confidence) if confidence is not None else 1.0
            except (TypeError, ValueError):
                confidence_value = 1.0
            if is_high_risk_action(action) and confidence_value < 0.75 and not intent.get("confirmed"):
                question = clarified or "Please confirm what you want me to do before I proceed."
                return build_orchestration_result(
                    status="needs_clarification",
                    action=action,
                    risk_level=risk_level,
                    requires_confirmation=True,
                    clarification_prompt=question,
                    data={"confidence": confidence_value},
                    reason="ambiguous_high_risk_request",
                    next_step="clarify",
                )

            # Get connector
            connector = self.connectors.get(action)
            if not connector:
                logger.warning(f"No connector for action: {action}")
                return build_orchestration_result(
                    status="error",
                    action=action,
                    risk_level=risk_level,
                    requires_confirmation=requires_confirmation,
                    clarification_prompt=f"Action '{action}' is not supported yet.",
                    data={},
                    reason="unsupported_action",
                )

            # Execute with timeout
            logger.info(f"Routing to connector: {action}")
            parameters = sanitize_parameters(intent.get("parameters") or {})
            if "action" not in parameters:
                parameters["action"] = action

            # Merge missing fields from recent dialog state for continuity
            dialog_state = await self._get_dialog_state(user_context)
            parameters = self._merge_with_dialog_state(parameters, dialog_state)
            parameters = sanitize_parameters(parameters)

            result = await connector.execute(parameters, user_context)

            # Cache result
            await self._cache_result(intent, user_context, result)

            # Persist dialog state for the next turn
            await self._store_dialog_state(user_context, action, parameters, status="success")
            payload = build_orchestration_result(
                status="success",
                action=action,
                risk_level=risk_level,
                requires_confirmation=requires_confirmation,
                data=result if isinstance(result, dict) else {"result": result},
            )
            payload["metadata"] = {
                "cached": False,
                "timestamp": datetime.now().isoformat(),
                "connector": connector.__class__.__name__,
            }
            return payload

        except Exception as e:
            logger.error("MCP routing error: %s", e)
            try:
                await self._store_dialog_state(user_context, action, intent.get("parameters", {}), status="error")
            except Exception:
                pass
            return build_orchestration_result(
                status="error",
                action=resolve_action_alias(intent.get("action")),
                risk_level=(get_action_definition(intent.get("action")) or {}).get("risk_level", "low"),
                requires_confirmation=catalog_requires_confirmation(intent.get("action")),
                clarification_prompt="Something went wrong processing your request. Please try again.",
                data={},
                reason=str(e),
            )

    # ----------------------------
    # Dialog state helpers
    # ----------------------------
    def _is_dialog_compatible(self, new_action: Optional[str], previous_action: Optional[str]) -> bool:
        """
        Decide if we should reuse parameters from the previous intent.
        - Same action => yes
        - Travel actions share common fields => yes
        """
        if not previous_action or not new_action:
            return False
        if new_action == previous_action:
            return True
        if new_action in self.TRAVEL_ACTIONS and previous_action in self.TRAVEL_ACTIONS:
            return True
        return False

    def _dialog_cache_key(self, context: Dict) -> str:
        user_id = context.get("user_id") or "anon"
        room_id = context.get("room_id") or "room"
        return f"dialog_state:{user_id}:{room_id}"

    async def _get_dialog_state(self, context: Dict) -> Optional[Dict]:
        key = self._dialog_cache_key(context)
        try:
            return await sync_to_async(cache.get)(key)
        except Exception as e:
            logger.warning(f"Dialog state read failed: {e}")
            return None

    async def _store_dialog_state(self, context: Dict, action: Optional[str], parameters: Dict, status: str = "success"):
        key = self._dialog_cache_key(context)
        state = {
            "action": action,
            "parameters": dict(parameters or {}),
            "status": status,
            "updated_at": datetime.now().isoformat()
        }
        try:
            await sync_to_async(cache.set)(key, state, self.DIALOG_STATE_TTL_SECONDS)
        except Exception as e:
            logger.warning(f"Dialog state write failed: {e}")

    def _merge_with_dialog_state(self, current_params: Dict, dialog_state: Optional[Dict]) -> Dict:
        """
        Merge missing parameters from the last compatible intent.
        Only fills empty/None values to avoid overwriting explicit user input.
        """
        if not dialog_state:
            return current_params

        previous_params = dialog_state.get("parameters") or {}
        previous_action = dialog_state.get("action")
        current_action = current_params.get("action")

        if not self._is_dialog_compatible(current_action, previous_action):
            return current_params

        merged = dict(current_params)
        filled = []
        for key, value in previous_params.items():
            if merged.get(key) in (None, "", [], {}):
                merged[key] = value
                filled.append(key)

        if filled:
            merged["_filled_from_dialog_state"] = filled
            merged["_dialog_origin"] = previous_action

        return merged

    async def _validate_request(self, intent: Dict, context: Dict) -> Dict:
        """Validate request against rate limits, auth, etc."""
        user_id = context.get("user_id")
        cache_key = f"mcp_rate:{user_id}"

        current = await self._count_request(cache_key)

        caps_enforced = await sync_to_async(enforce_agent_caps)(user_id)
        if caps_enforced and current >= self.RATE_LIMIT_PER_HOUR:
            return {"valid": False, "reason": "Rate limit exceeded. Try again in an hour."}

        action = resolve_action_alias(intent.get("action"))
        prefs = await self._get_user_prefs(user_id)
        if not self._is_action_allowed(action, prefs):
            return {
                "valid": False,
                "reason": "This action is disabled in your settings. You can enable it in Settings > Integrations.",
            }
        raw_query = intent.get("raw_query") or ""
        if should_block_action(raw_query, action):
            return {
                "valid": False,
                "reason": "I can't run that request. Please rephrase without system or tool instructions.",
            }
        room_id = context.get("room_id")
        if not await user_has_room_access(user_id, room_id):
            return {"valid": False, "reason": "Room access check failed for this request."}
        return {"valid": True, "reason": None}

    async def _count_request(self, cache_key: str) -> int:
        """Increment the hourly request counter.

        Uses the raw Redis client (INCR + EXPIRE) rather than the Django
        cache so the cache's ignore-exceptions mode cannot silently reset
        the counter to zero during an outage. When Redis is unreachable a
        per-process fallback keeps counting instead of failing open.
        """
        try:
            redis = get_redis_connection("default")
            current = int(await sync_to_async(redis.incr)(cache_key))
            await sync_to_async(redis.expire)(cache_key, 3600)
            return current
        except Exception:
            with self._local_rate_lock:
                current = self._local_rate_counters.get(cache_key, 0) + 1
                self._local_rate_counters[cache_key] = current
            return current

    async def _get_user_prefs(self, user_id: Optional[int]) -> Dict[str, Any]:
        if not user_id:
            return dict(self.DEFAULT_CAPABILITY_PREFS)
        User = get_user_model()
        try:
            user = await sync_to_async(User.objects.get)(pk=user_id)
        except User.DoesNotExist:
            return dict(self.DEFAULT_CAPABILITY_PREFS)
        except Exception:
            logger.warning(
                "Capability preference lookup failed for user %s; falling back "
                "to conservative (fail-closed) prefs for sensitive gates.",
                user_id,
            )
            return conservative_capability_prefs(dict(self.DEFAULT_CAPABILITY_PREFS))
        try:
            profile = await sync_to_async(lambda: getattr(user, "profile", None))()
        except Exception:
            logger.warning(
                "Profile read failed for user %s; falling back to conservative "
                "(fail-closed) prefs for sensitive gates.",
                user_id,
            )
            return conservative_capability_prefs(dict(self.DEFAULT_CAPABILITY_PREFS))
        prefs = dict(self.DEFAULT_CAPABILITY_PREFS)
        if profile and profile.notification_preferences:
            prefs.update(profile.notification_preferences)
        return prefs

    def _is_action_allowed(self, action: Optional[str], prefs: Dict[str, Any]) -> bool:
        if not action:
            return True
        gate_key = get_capability_gate(action)
        if gate_key:
            return bool(prefs.get(gate_key, True))
        return True

    async def _cache_result(self, intent: Dict, context: Dict, result: Any):
        """Cache results in Redis for quick retrieval"""
        try:
            cache_key = f"mcp_cache:{resolve_action_alias(intent.get('action'))}:{context.get('user_id')}"
            redis = get_redis_connection("default")

            # Store with 5 min TTL
            cache_data = json.dumps({
                "intent": intent,
                "result": result,
                "timestamp": datetime.now().isoformat()
            })

            await sync_to_async(redis.setex)(cache_key, 300, cache_data)
        except Exception as e:
            logger.error("Cache error: %s", e)


# ============================================
# CONNECTORS (all inherit from base_connector.BaseConnector)
# ============================================


class CalendarConnector(BaseConnector):
    """Real Calendly connector using CalendlyProfile.
    Uses httpx.AsyncClient for non-blocking HTTP in ASGI."""

    CALENDLY_EVENTS_URL = 'https://api.calendly.com/scheduled_events'
    CALENDLY_TOKEN_URL = 'https://auth.calendly.com/oauth/token'

    async def execute(self, parameters: Dict, context: Dict) -> Dict:
        """Execute Calendly actions"""
        from users.models import CalendlyProfile
        from django.contrib.auth import get_user_model

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

            user_uri = await sync_to_async(lambda: profile.calendly_user_uri)()

            # Self-heal: if the stored user URI is missing, fetch it from /users/me
            if not user_uri:
                async with httpx.AsyncClient(timeout=15.0) as client:
                    me_resp = await client.get(
                        'https://api.calendly.com/users/me',
                        headers={'Authorization': f'Bearer {access_token}'},
                    )
                    if me_resp.status_code == 200:
                        me_data = me_resp.json()
                        user_uri = (me_data.get('resource') or {}).get('uri') or me_data.get('uri')
                        if user_uri:
                            await sync_to_async(lambda: setattr(profile, 'calendly_user_uri', user_uri) or profile.save())()
                    else:
                        logger.error(
                            "Calendly self-heal /users/me failed: %s %s",
                            me_resp.status_code, me_resp.text[:300],
                        )
                    # Fallback via organization memberships
                    if not user_uri and me_resp.status_code == 200:
                        me_data = me_resp.json()
                        org_uri = (me_data.get('resource') or {}).get('current_organization')
                        if org_uri:
                            org_resp = await client.get(
                                'https://api.calendly.com/organization_memberships',
                                headers={'Authorization': f'Bearer {access_token}'},
                                params={'organization': org_uri},
                            )
                            if org_resp.status_code == 200:
                                members = org_resp.json().get('collection') or []
                                if members:
                                    user_obj = members[0].get('user') or (members[0].get('resource') or {}).get('user') or {}
                                    user_uri = user_obj.get('uri')
                                    if user_uri:
                                        await sync_to_async(lambda: setattr(profile, 'calendly_user_uri', user_uri) or profile.save())()

            if not user_uri:
                return {"status": "error", "message": "Your Calendly account URI is missing. Please reconnect Calendly.", "action_required": "connect_calendly"}

            # Fully async HTTP — no thread pool blocking
            async with httpx.AsyncClient(timeout=15.0) as client:
                response = await client.get(
                    self.CALENDLY_EVENTS_URL,
                    headers={'Authorization': f'Bearer {access_token}'},
                    params={'user': user_uri, 'status': 'active', 'sort': 'start_time:asc'},
                )

                if response.status_code == 401:
                    logger.info("Calendly token expired. Attempting refresh...")
                    new_token = await self._refresh_token(profile)
                    if new_token:
                        response = await client.get(
                            self.CALENDLY_EVENTS_URL,
                            headers={'Authorization': f'Bearer {new_token}'},
                            params={'user': user_uri, 'status': 'active', 'sort': 'start_time:asc'},
                        )
                    else:
                        return {"status": "error", "message": "Calendly authorization failed. Please reconnect.", "action_required": "connect_calendly"}

                if response.status_code != 200:
                    logger.error("Calendly API error: %s", response.text)
                    return {"status": "error", "message": "Failed to fetch Calendly events."}

            data = response.json()
            events = data.get('collection', [])

            formatted_events = []
            for event in events[:5]:
                formatted_events.append({"start": event.get('start_time'), "title": event.get('name'), "url": event.get('uri')})

            return {
                "status": "success",
                "type": "events",
                "events": formatted_events,
                "message": f"You have {len(formatted_events)} upcoming meetings." if formatted_events else "You have no upcoming meetings scheduled.",
            }

        except Exception as e:
            logger.error("CalendarConnector error: %s", e)
            return {"status": "error", "message": "Calendar operation failed. Please try again."}

    async def _refresh_token(self, profile):
        """Refresh the Calendly access token using async httpx."""
        from django.conf import settings

        refresh_token = await sync_to_async(profile.get_refresh_token)()
        if not refresh_token:
            logger.error("No refresh token available")
            return None

        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                response = await client.post(
                    self.CALENDLY_TOKEN_URL,
                    data={
                        'grant_type': 'refresh_token',
                        'refresh_token': refresh_token,
                        'client_id': settings.CALENDLY_CLIENT_ID,
                        'client_secret': settings.CALENDLY_CLIENT_SECRET,
                    },
                )

            if response.status_code == 200:
                data = response.json()
                new_access = data.get('access_token')
                new_refresh = data.get('refresh_token')

                def update_profile():
                    profile.encrypted_access_token = TokenEncryption.encrypt(new_access)
                    if new_refresh:
                        profile.encrypted_refresh_token = TokenEncryption.encrypt(new_refresh)
                    profile.save()
                    return new_access

                return await sync_to_async(update_profile)()
            else:
                logger.error("Token refresh failed: %s", response.text)
                return None

        except Exception as e:
            logger.error("Error refreshing token: %s", e)
            return None


class SearchConnector(BaseConnector):
    """Web search fallback for classic pipeline. Agent loop uses Claude's native web_search tool instead."""

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
            return {"status": "error", "message": "No search query provided"}

        # RATE LIMIT CHECK — atomic increment
        if user_id:
            today = datetime.now().strftime("%Y-%m-%d")
            limit_key = f"search_limit:{user_id}:{today}"
            try:
                # get-then-set with fresh TTL (see note above)
                current_count = int(cache.get(limit_key) or 0) + 1
                cache.set(limit_key, current_count, 86400)
            except Exception:
                cache.set(limit_key, 1, 86400)
                current_count = 1

            if current_count > 10:
                return {
                    "status": "error",
                    "message": "Daily search limit reached (10/10). Please try again tomorrow.",
                }

        if not self.llm.anthropic_key:
            logger.warning("Search requested but Anthropic key missing.")
            return {"results": [], "summary": "I cannot browse the live web right now.", "source": "system_fallback"}

        try:
            system_prompt = "You are a helpful research assistant."
            response = await self.llm.generate_text(
                system_prompt=system_prompt,
                user_prompt=f"Search for: {query}",
                temperature=0.7,
                model_role="executor",
            )
            return {"status": "success", "results": [{"title": "Search Result", "snippet": response[:200] + "..."}], "summary": response, "source": "claude_search"}
        except Exception as e:
            logger.error("Search failed: %s", e)
            return {"status": "error", "message": "Search functionality temporarily unavailable."}


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
            logger.error("Weather fetch error: %s", e)
            return {"status": "error", "message": "Weather lookup failed. Please try again."}


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
            logger.error("GIPHY fetch error: %s", e)
            return {"status": "error", "message": "GIF search failed. Please try again."}


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
            logger.error("Currency conversion error: %s", e)
            return {"status": "error", "message": "Currency conversion failed. Please try again."}


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
            try:
                from chatbot.tasks import schedule_reminder_delivery
                await sync_to_async(schedule_reminder_delivery)(reminder.id, scheduled_time)
            except Exception as e:
                logger.warning(f"Reminder scheduling skipped: {e}")

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
_router_lock = threading.Lock()


def get_mcp_router() -> MCPRouter:
    """Get or create the global MCP router instance (thread-safe)."""
    global _router
    if _router is None:
        with _router_lock:
            if _router is None:
                _router = MCPRouter()
    return _router


async def route_intent(intent: Dict, user_context: Dict) -> Dict:
    """Convenience function to route an intent"""
    router = get_mcp_router()
    return await router.route(intent, user_context)
