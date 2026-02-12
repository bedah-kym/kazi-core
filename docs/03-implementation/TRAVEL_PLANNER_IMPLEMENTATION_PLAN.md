# AI Travel & Itinerary Planner — Implementation Plan (Fused with Existing Architecture)

**Version:** 1.0  
**Date:** December 22, 2025  
**Scope:** MVP (Kenya) — Low-Cost Deep Integration Strategy  
**Status:** Ready for Development

---

## Executive Summary

This plan fuses the travel planner into the existing **Mathia orchestration stack** (MCPRouter, LLMClient, Channels, Celery). Key principle: **Users say what they want → LLM understands → MCPRouter dispatches to travel connectors → Results returned via WebSocket chat or web UI.**

**Cost strategy:** Use **free tiers + open APIs + strategic scraping** to maximize data without premium features.

---

## Part 1: Architecture Alignment (How It Fits Existing Codebase)

### Current Stack (Already Working)

| Component | Purpose | Reuse? |
|-----------|---------|--------|
| **MCPRouter** (`orchestration/mcp_router.py`) | Central intent dispatcher | ✅ Yes—add travel connector actions |
| **LLMClient** (`orchestration/llm_client.py`) | Claude/HF Router LLM | ✅ Yes—use for itinerary generation |
| **IntentParser** (`orchestration/intent_parser.py`) | NLU (intent extraction) | ✅ Yes—extend SUPPORTED_ACTIONS |
| **ChatConsumer** (`chatbot/consumers.py`) | WebSocket chat handler | ✅ Yes—route travel intents here |
| **Celery** (`Backend/celery.py`) | Background tasks | ✅ Yes—for search caching, price tracking |
| **Redis** | Caching & rate-limits | ✅ Yes—SearchCache layer |
| **Channels** | Real-time updates | ✅ Yes—live itinerary updates |
| **Django Models** (`users/models.py`, etc.) | User management | ✅ Yes—reuse User model |

### New Travel-Specific Layer

```
Backend/
  orchestration/
    connectors/
      ├── travel_buses_connector.py       (Buupass API + scraper)
      ├── travel_hotels_connector.py      (Booking.com affiliate)
      ├── travel_flights_connector.py     (Duffel API)
      ├── travel_transfers_connector.py   (Karibu Taxi API)
      ├── travel_events_connector.py      (Eventbrite API)
      ├── travel_cache_manager.py         (Redis caching for all)
      └── travel_utils.py                 (Helpers: scraping, affiliate URL building, etc.)
    
    integrations/
      ├── buupass.py                      (Web scraper + API wrapper)
      ├── booking.py                      (Affiliate link builder)
      ├── duffel.py                       (API client wrapper)
      ├── karibu.py                       (API client wrapper)
      ├── eventbrite.py                   (API client wrapper)
      └── local_events.py                 (Scraper for local event aggregators)
    
    services/
      ├── itinerary_builder.py            (LLM → JSON itinerary composer)
      ├── booking_orchestrator.py         (Pre-fill booking links, redirect)
      └── travel_validator.py             (Conflict detection, budget checks)
  
  travel/  (NEW Django app)
    ├── models.py                         (Itinerary, ItineraryItem, Event, BookingRef)
    ├── views.py                          (REST API: /search/, /itinerary/, /export/)
    ├── serializers.py                    (DRF serializers)
    ├── urls.py
    ├── tests.py
    └── migrations/
```

---

## Part 2: Step-by-Step Implementation (Week 1–12)

### **WEEK 1: Setup & Intent Extension**

#### Step 1.1 — Extend IntentParser (Travel Actions)
**File:** `Backend/orchestration/intent_parser.py`

Add travel actions to `SUPPORTED_ACTIONS`:
```python
SUPPORTED_ACTIONS = [
    # ... existing actions ...
    "search_buses",           # New
    "search_hotels",          # New
    "search_flights",         # New
    "search_transfers",       # New
    "search_events",          # New
    "create_itinerary",       # New (composite)
    "book_itinerary",         # New
]
```

Update `SYSTEM_PROMPT` with travel examples:
```
- search_buses: User wants to search intercity buses
  Examples: "buses from Nairobi to Mombasa", "when's the next bus to Kisumu?", "find cheap buses"
  Parameters: origin, destination, departure_date, passengers
  
- search_hotels: User wants accommodation
  Examples: "hotels in Mombasa", "find a cheap hotel in Nairobi", "5-star hotels near CBD"
  Parameters: location, check_in_date, check_out_date, budget_max_ksh
  
[... similar for flights, transfers, events, create_itinerary ...]
```

**Time:** 30 minutes  
**Outcome:** Travel intents now parsed alongside existing actions

---

#### Step 1.2 — Create Travel Django App
**Command:**
```bash
cd Backend
python manage.py startapp travel
```

**File:** `Backend/travel/models.py` (copy from PRD)
- Itinerary
- ItineraryItem
- Event
- SearchCache
- BookingReference

Run migrations:
```bash
python manage.py makemigrations travel
python manage.py migrate travel
```

**Time:** 45 minutes  
**Outcome:** Database schema ready for itineraries & events

---

#### Step 1.3 — Register Travel Connectors in MCPRouter
**File:** `Backend/orchestration/mcp_router.py`

In `MCPRouter.__init__()`, add:
```python
self.connectors = {
    # ... existing connectors ...
    "search_buses": TravelBusesConnector(),
    "search_hotels": TravelHotelsConnector(),
    "search_flights": TravelFlightsConnector(),
    "search_transfers": TravelTransfersConnector(),
    "search_events": TravelEventsConnector(),
    "create_itinerary": ItineraryBuilderConnector(),
}
```

**Time:** 15 minutes  
**Outcome:** Router aware of travel actions

---

### **WEEK 2: Build Travel Connectors (Free Tier Focus)**

#### Step 2.1 — Base Travel Connector Class
**File:** `Backend/orchestration/connectors/base_travel_connector.py`

```python
from .base_connector import BaseConnector
from django.core.cache import cache
import hashlib

class BaseTravelConnector(BaseConnector):
    """Base for all travel connectors with caching & retry logic"""
    
    CACHE_TTL = 3600  # 1 hour for search results
    
    async def execute(self, parameters: Dict, context: Dict) -> Dict:
        """
        Template method:
        1. Check cache
        2. Call API/scraper
        3. Cache result
        4. Return standardized dict
        """
        cache_key = self._get_cache_key(parameters)
        cached = cache.get(cache_key)
        if cached:
            return cached
        
        result = await self._fetch(parameters, context)
        cache.set(cache_key, result, self.CACHE_TTL)
        return result
    
    async def _fetch(self, parameters: Dict, context: Dict) -> Dict:
        raise NotImplementedError()
    
    def _get_cache_key(self, parameters: Dict) -> str:
        key_str = json.dumps(parameters, sort_keys=True)
        return f"travel_{hashlib.md5(key_str.encode()).hexdigest()}"
```

**Time:** 30 minutes  
**Outcome:** Caching + retry pattern ready for all travel connectors

---

#### Step 2.2 — Buses Connector (Buupass Scraper + Fallback)
**Files:**
- `Backend/orchestration/integrations/buupass.py` (scraper)
- `Backend/orchestration/connectors/travel_buses_connector.py` (connector)

**Strategy:** Use web scraper (free) + mock API fallback for MVP

```python
# buupass.py (scraper)
from bs4 import BeautifulSoup
import httpx

async def search_buses(origin: str, destination: str, date: str) -> List[Dict]:
    """
    Scrape Buupass for buses.
    Returns: [{"provider": "Buupass", "departure": "06:00", "price_ksh": 1500, "booking_url": "..."}]
    """
    # Construct Buupass search URL
    url = f"https://buses.buupass.com/{origin}-to-{destination}"
    params = {"date": date}
    
    async with httpx.AsyncClient() as client:
        response = await client.get(url, params=params)
    
    soup = BeautifulSoup(response.text, "html.parser")
    results = []
    
    for item in soup.find_all("div", class_="bus-listing"):
        results.append({
            "provider": "Buupass",
            "departure": item.find(class_="departure").text,
            "arrival": item.find(class_="arrival").text,
            "price_ksh": int(item.find(class_="price").text.replace("KSh ", "")),
            "booking_url": item.find("a", class_="book-btn")["href"]
        })
    
    return results
```

```python
# travel_buses_connector.py (connector)
class TravelBusesConnector(BaseTravelConnector):
    async def _fetch(self, parameters: Dict, context: Dict) -> Dict:
        try:
            results = await buupass.search_buses(
                origin=parameters.get("origin"),
                destination=parameters.get("destination"),
                date=parameters.get("departure_date")
            )
            return {
                "status": "success",
                "provider": "Buupass",
                "results": results,
                "count": len(results)
            }
        except Exception as e:
            logger.error(f"Buupass search failed: {e}")
            # Fallback: return curated list of operators + phone numbers
            return {
                "status": "partial",
                "message": "Could not fetch live Buupass data. Showing alternative operators.",
                "results": FALLBACK_OPERATORS,  # Hardcoded list
                "count": len(FALLBACK_OPERATORS)
            }
```

**Time:** 1.5 hours  
**Outcome:** Bus search working with scraper + fallback

---

#### Step 2.3 — Hotels Connector (Booking.com Affiliate)
**File:** `Backend/orchestration/connectors/travel_hotels_connector.py`

**Strategy:** No direct API; use search redirect with affiliate ID

```python
class TravelHotelsConnector(BaseTravelConnector):
    BOOKING_AFFILIATE_ID = os.environ.get("BOOKING_AFFILIATE_ID")  # From Awin/CJ signup
    
    async def _fetch(self, parameters: Dict, context: Dict) -> Dict:
        location = parameters.get("location")
        check_in = parameters.get("check_in_date")
        check_out = parameters.get("check_out_date")
        
        # Build Booking.com affiliate search URL
        booking_url = f"https://www.booking.com/searchresults.en-gb.html?ss={location}&checkin={check_in}&checkout={check_out}&affiliate_id={self.BOOKING_AFFILIATE_ID}"
        
        # Optional: Scrape Booking.com search results to preview (if legally allowed)
        # For MVP, just return redirect URL with high-level recommendations
        
        return {
            "status": "success",
            "provider": "Booking.com",
            "results": [
                {
                    "name": "[Search results on Booking.com]",
                    "price_ksh": "Varies",
                    "booking_url": booking_url,
                    "note": "Click to view all available hotels with prices"
                }
            ],
            "search_url": booking_url
        }
```

**Time:** 1 hour  
**Outcome:** Hotel search redirects to Booking.com affiliate link

---

#### Step 2.4 — Flights Connector (Duffel API)
**Files:**
- `Backend/orchestration/integrations/duffel.py` (API wrapper)
- `Backend/orchestration/connectors/travel_flights_connector.py` (connector)

**Strategy:** Use Duffel free sandbox + redirect for MVP

```python
# duffel.py (API wrapper)
import httpx

class DuffelClient:
    def __init__(self):
        self.token = os.environ.get("DUFFEL_TOKEN")  # From sandbox signup
        self.base_url = "https://api.duffel.com"
    
    async def search_flights(self, origin: str, destination: str, date: str, passengers: int = 1) -> List[Dict]:
        """
        Call Duffel API for flight search (free sandbox).
        Returns: [{"airline": "Kenya Airways", "price": 5000, "booking_url": "..."}]
        """
        payload = {
            "slices": [{
                "origin_airport_iata_code": origin,
                "destination_airport_iata_code": destination,
                "departure_date": date
            }],
            "passengers": [{"type": "adult"} for _ in range(passengers)]
        }
        
        async with httpx.AsyncClient() as client:
            response = await client.post(
                f"{self.base_url}/air/offer_requests",
                json=payload,
                headers={"Authorization": f"Bearer {self.token}"}
            )
        
        offers = response.json().get("data", {}).get("offers", [])
        results = []
        for offer in offers:
            results.append({
                "airline": offer["owner"]["name"],
                "price_ksh": int(offer["total_amount"]["amount"]) * 150,  # Crude conversion
                "departure_time": offer["slices"][0]["segments"][0]["departing_at"],
                "arrival_time": offer["slices"][0]["segments"][-1]["arriving_at"],
                "offer_id": offer["id"],
                "booking_url": f"https://app.duffel.com/orders/create?offer_id={offer['id']}"  # Redirect
            })
        
        return results
```

**Time:** 1.5 hours  
**Outcome:** Flight search via Duffel sandbox

---

#### Step 2.5 — Transfers Connector (Karibu Taxi + Fallback)
**File:** `Backend/orchestration/connectors/travel_transfers_connector.py`

**Strategy:** API contact + hardcoded fallback quotes

```python
class TravelTransfersConnector(BaseTravelConnector):
    async def _fetch(self, parameters: Dict, context: Dict) -> Dict:
        origin = parameters.get("origin")
        destination = parameters.get("destination")
        
        try:
            # Try Karibu API (once credentials received)
            # For now, return fallback quotes
            return {
                "status": "success",
                "provider": "Karibu Taxi + Local Operators",
                "results": [
                    {
                        "operator": "Karibu Taxi",
                        "vehicle_type": "Standard SUV",
                        "price_ksh": 2000,
                        "booking_url": "https://www.kaributaxi.com/book?origin={origin}&destination={destination}"
                    },
                    {
                        "operator": "Local Taxi Partner",
                        "vehicle_type": "Sedan",
                        "price_ksh": 1500,
                        "booking_url": f"tel:+254-700-000-000"  # Partner phone number
                    }
                ],
                "count": 2
            }
        except Exception as e:
            logger.error(f"Transfer search failed: {e}")
            return {
                "status": "partial",
                "message": "Call partner directly for quote.",
                "results": FALLBACK_TRANSFER_OPERATORS
            }
```

**Time:** 45 minutes  
**Outcome:** Transfer quotes available with fallback

---

#### Step 2.6 — Events Connector (Eventbrite API)
**Files:**
- `Backend/orchestration/integrations/eventbrite_client.py` (API wrapper)
- `Backend/orchestration/connectors/travel_events_connector.py` (connector)

**Strategy:** Use Eventbrite free API + local event scraping

```python
# eventbrite_client.py
class EventbriteClient:
    def __init__(self):
        self.token = os.environ.get("EVENTBRITE_TOKEN")  # Free tier token
        self.base_url = "https://www.eventbriteapi.com/v3"
    
    async def search_events(self, location: str, keyword: str = "", start_date: str = None) -> List[Dict]:
        """
        Search Eventbrite for events in location.
        Free tier: search by location, keyword. No geofencing API in free tier.
        """
        params = {
            "location.address": location,
            "q": keyword,
            "token": self.token
        }
        
        async with httpx.AsyncClient() as client:
            response = await client.get(
                f"{self.base_url}/events/search/",
                params=params
            )
        
        events = response.json().get("events", [])
        results = []
        for event in events:
            results.append({
                "title": event["name"]["text"],
                "date": event["start"]["local"],
                "location": event["venue_id"],
                "price_ksh": 0,  # Free events only in MVP
                "booking_url": event["url"],
                "provider": "Eventbrite"
            })
        
        return results
```

**Time:** 1 hour  
**Outcome:** Event search via Eventbrite + local scraper fallback

---

### **WEEK 3: Itinerary Builder & LLM Integration**

#### Step 3.1 — Itinerary Builder Service
**File:** `Backend/orchestration/services/itinerary_builder.py`

```python
class ItineraryBuilder:
    """
    Takes user request → calls travel connectors in parallel → LLM composes JSON itinerary
    """
    
    async def build(self, user_request: str, user_context: Dict) -> Dict:
        """
        Example: user_request = "Plan a 3-day Mombasa trip. Budget: 5000 KES"
        Returns: JSON itinerary with day-by-day breakdown
        """
        # Step 1: Parse user intent (already done by IntentParser)
        # Step 2: Trigger parallel searches
        
        searches = await asyncio.gather(
            self.mcp_router.route({"action": "search_buses", "parameters": {...}}, user_context),
            self.mcp_router.route({"action": "search_hotels", "parameters": {...}}, user_context),
            self.mcp_router.route({"action": "search_events", "parameters": {...}}, user_context),
            # ... etc
        )
        
        # Step 3: Aggregate results
        aggregated = {
            "buses": searches[0]["results"],
            "hotels": searches[1]["results"],
            "events": searches[2]["results"],
        }
        
        # Step 4: Call LLM to compose itinerary
        system_prompt = """You are a travel planner. Compose a JSON itinerary from search results."""
        user_prompt = f"""
        User request: {user_request}
        Available options:
        {json.dumps(aggregated, indent=2)}
        
        Return a JSON itinerary with this schema:
        {{
          "itinerary": [
            {{
              "day": 1,
              "items": [
                {{"type": "bus", "title": "...", "price_ksh": 1500, "booking_url": "..."}}
              ]
            }}
          ],
          "total_cost_ksh": 5000,
          "notes": "..."
        }}
        """
        
        response = await self.llm_client.generate_text(system_prompt, user_prompt, json_mode=True)
        return json.loads(response)
```

**Time:** 1.5 hours  
**Outcome:** LLM can compose itineraries from search results

---

#### Step 3.2 — Travel Connector in MCPRouter (create_itinerary)
**File:** `Backend/orchestration/connectors/travel_itinerary_connector.py`

```python
class ItineraryConnector(BaseConnector):
    def __init__(self):
        self.builder = ItineraryBuilder()
    
    async def execute(self, parameters: Dict, context: Dict) -> Dict:
        """
        Called when user intent is "create_itinerary"
        parameters: {"request": "3-day Mombasa trip, 5000 KES budget"}
        """
        user_request = parameters.get("request")
        itinerary = await self.builder.build(user_request, context)
        
        return {
            "status": "success",
            "data": itinerary,
            "message": "Itinerary created. You can now book items or refine the plan."
        }
```

Register in MCPRouter:
```python
self.connectors["create_itinerary"] = ItineraryConnector()
```

**Time:** 45 minutes  
**Outcome:** Full itinerary generation via chat

---

### **WEEK 4: REST API & Export**

#### Step 4.1 — Travel REST API Endpoints
**File:** `Backend/travel/views.py`

```python
from rest_framework.views import APIView
from rest_framework.response import Response

class SearchBusesView(APIView):
    def post(self, request):
        # {origin, destination, departure_date, passengers}
        # Returns: bus search results
        pass

class CreateItineraryView(APIView):
    def post(self, request):
        # {destination, dates, budget_ksh, preferences}
        # Returns: full itinerary + booking links
        pass

class ExportItineraryView(APIView):
    def get(self, request, itinerary_id):
        # format=pdf|json|ical
        # Returns: exported itinerary
        pass
```

**Time:** 2 hours  
**Outcome:** REST API for web UI + mobile apps

---

#### Step 4.2 — Export Service (PDF, JSON, iCal)
**File:** `Backend/orchestration/services/export_service.py`

```python
class ExportService:
    @staticmethod
    async def to_json(itinerary: Itinerary) -> str:
        return json.dumps({
            "title": itinerary.title,
            "region": itinerary.region,
            "dates": f"{itinerary.start_date} to {itinerary.end_date}",
            "items": [
                {
                    "day": item.start_datetime.day,
                    "type": item.item_type,
                    "title": item.title,
                    "time": item.start_datetime.time(),
                    "price": item.price_ksh,
                    "booking_url": item.booking_url
                }
                for item in itinerary.items.all()
            ]
        })
    
    @staticmethod
    async def to_pdf(itinerary: Itinerary) -> bytes:
        # Use reportlab or weasyprint
        pass
    
    @staticmethod
    async def to_ical(itinerary: Itinerary) -> str:
        # Use ics library
        pass
```

**Time:** 1 hour  
**Outcome:** Itineraries exportable in multiple formats

---

### **WEEK 5: Chat Integration & UX Flow**

#### Step 5.1 — Route Travel Intents in ChatConsumer
**File:** `Backend/chatbot/consumers.py` (modify existing)

In `receive()` method, after intent parsing:
```python
async def receive(self, text_data):
    # ... existing code ...
    intent = await parse_intent(user_message)
    
    if intent["action"] in ["search_buses", "search_hotels", "search_flights", 
                             "search_transfers", "search_events", "create_itinerary"]:
        # Route to MCPRouter for travel
        result = await self.mcp_router.route(intent, user_context)
        
        # Format and send back via WebSocket
        response_message = await self._format_travel_response(result)
        await self.send(json.dumps({"type": "travel_result", "data": response_message}))
    else:
        # Existing logic for non-travel intents
        pass
```

**Time:** 1 hour  
**Outcome:** Chat can handle travel requests seamlessly

---

#### Step 5.2 — Web UI Component (Basic HTMX or React)
**Files:**
- `Backend/templates/travel_search.html` (search form)
- `Backend/templates/itinerary_editor.html` (timeline editor)

For MVP, use lightweight **HTMX** (no JS build step):

```html
<!-- travel_search.html -->
<form hx-post="/api/travel/search/" hx-target="#results">
  <input type="text" name="query" placeholder="Where do you want to go?" />
  <input type="date" name="start_date" />
  <input type="date" name="end_date" />
  <input type="number" name="budget" placeholder="Budget in KES" />
  <button type="submit">Plan My Trip</button>
</form>

<div id="results"></div>
```

**Time:** 2 hours  
**Outcome:** Basic web search + itinerary display

---

### **WEEK 6: Mock Servers & Testing Setup**

#### Step 6.1 — Mock Connectors (for dev without API keys)
**File:** `Backend/orchestration/connectors/mocks/`

```python
# mock_buses.py
class MockBusConnector(BaseTravelConnector):
    async def _fetch(self, parameters: Dict, context: Dict) -> Dict:
        return {
            "status": "success",
            "provider": "Mock Buupass",
            "results": [
                {
                    "provider": "Akamba",
                    "departure": "06:00",
                    "arrival": "12:00",
                    "price_ksh": 1500,
                    "booking_url": "https://example.com/book"
                },
                {
                    "provider": "Easy Plan",
                    "departure": "08:00",
                    "arrival": "14:00",
                    "price_ksh": 1800,
                    "booking_url": "https://example.com/book"
                }
            ]
        }
```

**Usage in development:**
```python
# settings.py (dev)
USE_MOCK_CONNECTORS = os.environ.get("USE_MOCK_CONNECTORS", False)

# mcp_router.py
if USE_MOCK_CONNECTORS:
    from .connectors.mocks.mock_buses import MockBusConnector
    self.connectors["search_buses"] = MockBusConnector()
```

**Time:** 1.5 hours  
**Outcome:** Full development without API keys

---

#### Step 6.2 — Unit Tests for Connectors
**File:** `Backend/travel/tests.py`

```python
import pytest
from unittest.mock import AsyncMock, patch

@pytest.mark.asyncio
async def test_bus_search_success():
    connector = TravelBusesConnector()
    result = await connector.execute(
        {"origin": "Nairobi", "destination": "Mombasa", "departure_date": "2025-12-25"},
        {}
    )
    assert result["status"] == "success"
    assert len(result["results"]) > 0

@pytest.mark.asyncio
async def test_itinerary_creation():
    builder = ItineraryBuilder()
    itinerary = await builder.build("3-day Mombasa trip", {})
    assert "itinerary" in itinerary
    assert len(itinerary["itinerary"]) == 3  # 3 days
```

**Time:** 2 hours  
**Outcome:** Test coverage for travel features

---

### **WEEKS 7–8: E2E Testing & Performance**

#### Step 7.1 — E2E Tests (Playwright)
**File:** `e2e/travel.spec.js` (add to existing Playwright setup)

```javascript
test('Create itinerary via chat', async ({ page }) => {
  await page.goto('/chat');
  await page.fill('input[placeholder="Ask AI..."]', 'Plan a weekend trip to Mombasa');
  await page.click('button:has-text("Send")');
  
  // Wait for itinerary response
  await page.waitForSelector('[data-testid="itinerary"]');
  
  // Verify itinerary structure
  const itinerary = await page.locator('[data-testid="itinerary"]').textContent();
  expect(itinerary).toContain('Mombasa');
  expect(itinerary).toContain('Bus');
  expect(itinerary).toContain('Hotel');
});
```

**Time:** 2 hours  
**Outcome:** Full flow tested (search → itinerary → export)

---

#### Step 7.2 — Performance & Caching Validation
**File:** `Backend/travel/tests.py`

```python
@pytest.mark.asyncio
async def test_cache_hit():
    """Verify cache works; second call should be instant"""
    connector = TravelBusesConnector()
    
    params = {"origin": "Nairobi", "destination": "Mombasa", "departure_date": "2025-12-25"}
    
    # First call (cache miss)
    start = time.time()
    result1 = await connector.execute(params, {})
    first_call_time = time.time() - start
    
    # Second call (cache hit)
    start = time.time()
    result2 = await connector.execute(params, {})
    second_call_time = time.time() - start
    
    assert result1 == result2
    assert second_call_time < first_call_time / 2  # Cache should be 2x faster
```

**Time:** 1 hour  
**Outcome:** Verified sub-second cached responses

---

### **WEEKS 9–10: Staging Deploy & Beta Testing**

#### Step 9.1 — Docker Compose for Staging
**File:** `docker-compose.staging.yml` (extend existing)

```yaml
version: '3.8'
services:
  web:
    build: .
    environment:
      - DJANGO_SETTINGS_MODULE=Backend.settings
      - DEBUG=False
      - REDIS_URL=redis://redis:6379/0
      - DUFFEL_TOKEN=<staging-key>
      - EVENTBRITE_TOKEN=<staging-key>
      - BOOKING_AFFILIATE_ID=<awin-id>
    ports:
      - "8000:8000"
    depends_on:
      - db
      - redis

  db:
    image: postgres:14
    environment:
      - POSTGRES_DB=mathia_travel
      - POSTGRES_USER=user
      - POSTGRES_PASSWORD=pass
    volumes:
      - postgres_data:/var/lib/postgresql/data

  redis:
    image: redis:7-alpine

volumes:
  postgres_data:
```

Deploy to **Render** or **Railway** (free tier):
```bash
git push  # Triggers deploy
```

**Time:** 1 hour  
**Outcome:** Staging URL live for beta testing

---

#### Step 9.2 — Beta Testing Loop
- Invite 50 Nairobi users (friends, tech community)
- Test all flows: search → itinerary → export → booking redirect
- Collect feedback via Google Form
- Fix top 5 bugs
- Monitor logs for errors (Sentry integration)

**Time:** 2 weeks (ongoing)  
**Outcome:** Real user feedback; bug fixes

---

### **WEEKS 11–12: Launch & Documentation**

#### Step 11.1 — Final QA & Deployment
- All test scenarios passing
- Security audit (PII encryption, CSRF, rate-limits)
- Performance profiling (target: <3 sec search, <10 sec itinerary)
- Privacy policy + TOS finalized
- Analytics setup (Mixpanel or Plausible for usage tracking)

**Time:** 1 week  
**Outcome:** Production-ready

---

#### Step 11.2 — Launch & Marketing
- Deploy to production (full Render/Railway instance)
- Publish blog post + social media announcement
- Prepare press release (optional)
- Set up email onboarding sequence
- Monitor first 48 hours for bugs

**Time:** 1 week  
**Outcome:** Live MVP in production

---

## Part 3: Implementation Checklist (Quick Reference)

### ✅ Setup Phase (Week 1)
- [ ] Extend `intent_parser.py` with travel actions
- [ ] Create `travel` Django app + models
- [ ] Register connectors in `mcp_router.py`
- [ ] Set up `.env` variables (API keys)

### ✅ Connectors Phase (Weeks 2–3)
- [ ] `travel_buses_connector.py` (+ Buupass scraper)
- [ ] `travel_hotels_connector.py` (Booking.com affiliate)
- [ ] `travel_flights_connector.py` (Duffel)
- [ ] `travel_transfers_connector.py` (Karibu + fallback)
- [ ] `travel_events_connector.py` (Eventbrite)
- [ ] `itinerary_builder.py` (LLM composition)

### ✅ API & Integration (Weeks 4–5)
- [ ] REST API endpoints (`travel/views.py`)
- [ ] Export service (PDF, JSON, iCal)
- [ ] Chat integration (`chatbot/consumers.py`)
- [ ] Web UI templates (HTMX or React)

### ✅ Testing & Mocks (Week 6)
- [ ] Mock connectors (dev without keys)
- [ ] Unit tests for all connectors
- [ ] Integration tests for itinerary builder

### ✅ E2E & Performance (Weeks 7–8)
- [ ] Playwright E2E tests
- [ ] Load testing (100 concurrent users)
- [ ] Cache validation

### ✅ Deploy & Beta (Weeks 9–10)
- [ ] Staging Docker Compose
- [ ] Deploy to Render/Railway
- [ ] Beta tester onboarding
- [ ] Bug fix & iteration

### ✅ Launch (Weeks 11–12)
- [ ] Final security audit
- [ ] Production deployment
- [ ] Marketing launch
- [ ] Monitor & support

---

## Part 4: Deep Integration Strategies (Free Tier Maximization)

### Buses (Buupass)
| Strategy | Cost | Coverage | Effort |
|----------|------|----------|--------|
| **Web Scraper** | Free | 80% | Medium |
| **Partner API** | 0 (partnership) | 90% | High (negotiation) |
| **Fallback Operators** | Free | 70% | Low |

**MVP Approach:** Scraper primary, operators fallback while negotiating API.

---

### Hotels (Booking.com)
| Strategy | Cost | Coverage | Effort |
|----------|------|----------|--------|
| **Affiliate Links** | Commission-based | 100% | Low |
| **Search Widget** | Free | 100% | Low |
| **Scraping** | Free | 50% (limited) | High |

**MVP Approach:** Affiliate links + redirect only. No scraping needed.

---

### Flights (Duffel)
| Strategy | Cost | Coverage | Effort |
|----------|------|----------|--------|
| **Duffel Sandbox** | Free | 90% (test airlines) | Low |
| **Duffel Live** | Pay-per-search | 100% | Low |
| **Skyscanner Redirect** | Free | 100% (redirect only) | Low |

**MVP Approach:** Duffel sandbox initially; redirect to Skyscanner if live pricing unavailable.

---

### Transfers (Karibu Taxi)
| Strategy | Cost | Coverage | Effort |
|----------|------|----------|--------|
| **Karibu API** | Commission | 80% | High (partnership) |
| **Local Partners** | Commission | 60% | Medium |
| **Phone/WhatsApp** | Free | 50% | Low |

**MVP Approach:** Partner operators + phone numbers. API once available.

---

### Events (Eventbrite)
| Strategy | Cost | Coverage | Effort |
|----------|------|----------|--------|
| **Eventbrite API** | Free tier | 70% (Kenya) | Low |
| **Local Scraping** | Free | 80% | Medium |
| **Facebook Events Graph** | Free tier | 90% | Medium |

**MVP Approach:** Eventbrite primary + fallback to local scraper.

---

## Part 5: Cost Breakdown (MVP to Launch)

| Item | Cost | Notes |
|------|------|-------|
| **API Keys & Signups** | $0 | All free tier or partnership-based |
| **Staging Server** | $0–$7/mo | Render/Railway free tier |
| **Production Server** | $10–$30/mo | Small instance (scale later) |
| **Redis** | $0–$5/mo | Built-in or free tier |
| **Postgres** | $0–$10/mo | Built-in or cheap managed |
| **Domain + SSL** | $12/yr | Standard domain |
| **Development Time** | **~300 hours (2 devs, 12 weeks)** | |
| **Total First Year** | **~$50–$150** | Minimal infrastructure cost |

---

## Part 6: Success Metrics (MVP Target)

| Metric | Target | How to Measure |
|--------|--------|---|
| **Time to itinerary** | <10 seconds | Timed from chat submit to display |
| **Search cache hit rate** | >60% | Redis hits / total searches |
| **Booking click-through** | >30% | Users clicking "Book" / users viewing itinerary |
| **Chat success rate** | >70% | Successful itineraries / total requests |
| **Mobile responsiveness** | >90% UI functional | Tested on 320px–768px |
| **Uptime** | >95% (MVP phase) | Monitoring via Sentry/UptimeRobot |
| **Support ticket volume** | <50/mo | Common issues < 1 per user |

---

## Part 7: Risk Mitigation (Concrete Fallbacks)

| Risk | Fallback | Trigger |
|------|----------|---------|
| **Buupass API fails** | Hardcoded operator list + phone | API returns 500 for 5+ min |
| **Duffel rate-limited** | Show cached results, ask "try again in 1 min" | HTTP 429 response |
| **Eventbrite API down** | Serve cached events or "check back soon" | API timeout |
| **LLM hallucination** | Schema validation + ask user to refine | Invalid JSON or time conflicts |
| **Redis down** | Fall back to in-memory cache + DB queries | Connection timeout |

---

## Summary

**This plan:**
1. ✅ Fuses travel planner seamlessly into existing MCPRouter + LLMClient + Channels
2. ✅ Maximizes free API tiers + strategic scraping (zero upfront cost)
3. ✅ Enables "user says what they want → AI handles heavy lifting" via existing LLM orchestration
4. ✅ Provides clear week-by-week roadmap with concrete deliverables
5. ✅ Includes testing, E2E, staging deploy, and beta loop
6. ✅ Ready for immediate handoff to development team

**Next Step:** Approval to proceed with Week 1 setup tasks.

---

**Document Prepared By:** AI Planning Agent  
**Status:** Ready for Implementation  
**Date:** December 22, 2025  

**Approval Sign-Off:**
- [ ] User approves implementation plan
- [ ] User confirms team readiness (dev capacity, API signups)
- [ ] User ready to begin Week 1 (intent extension + Django app setup)
