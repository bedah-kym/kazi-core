# Travel Planner MVP — Quick Reference Card

**Print this or keep it at your desk during Week 1 development.**

---

## 🎯 MVP in 3 Bullets

1. **User says:** "Plan a weekend trip to Mombasa"
2. **AI does:** Search buses, hotels, transfers, events in parallel
3. **User gets:** Day-by-day itinerary with booking links (redirect to providers)

---

## 📐 Architecture in One Diagram

```
User Chat Message
       ↓
IntentParser (LLM) → "create_itinerary"
       ↓
MCPRouter → ItineraryConnector
       ↓
ItineraryBuilder:
  • Calls 5 connectors in parallel
    (buses, hotels, flights, transfers, events)
  • Waits for all results
  • Calls LLM to compose JSON itinerary
       ↓
Save to Database
       ↓
Send back via WebSocket → User sees itinerary
```

---

## 🔧 12-Week Timeline (Compressed)

| Week | What | Who | Done? |
|------|------|-----|-------|
| 1 | Setup intent parser, Django app, register connectors | Backend | ☐ |
| 2-3 | Build 5 travel connectors | Backend | ☐ |
| 4 | REST API + exports | Backend + Frontend | ☐ |
| 5 | Chat integration + web UI | Frontend + Backend | ☐ |
| 6 | Testing + mocks | QA + Backend | ☐ |
| 7-8 | E2E tests + performance | QA + DevOps | ☐ |
| 9-10 | Staging + beta testing | DevOps + QA | ☐ |
| 11-12 | Launch + marketing | All | ☐ |

---

## 🗂️ File Structure (What to Create)

```
Backend/
  travel/                           NEW Django App
    models.py                       (Itinerary, ItineraryItem, Event, etc.)
    views.py                        (REST API endpoints)
    serializers.py                  (DRF serializers)
    urls.py
    tests.py
    
  orchestration/
    connectors/
      travel_buses_connector.py             NEW
      travel_hotels_connector.py            NEW
      travel_flights_connector.py           NEW
      travel_transfers_connector.py         NEW
      travel_events_connector.py            NEW
      base_travel_connector.py              NEW
      
      mocks/
        mock_buses.py                       NEW (dev only)
        mock_hotels.py                      NEW (dev only)
        ...
    
    integrations/                           NEW Folder
      buupass.py                           (Scraper)
      booking.py                           (Affiliate links)
      duffel.py                            (API client)
      karibu.py                            (API client)
      eventbrite.py                        (API client)
    
    services/                               NEW Folder
      itinerary_builder.py                 (LLM composition)
      export_service.py                    (PDF, JSON, iCal)
      booking_orchestrator.py              (Booking link generation)
    
    intent_parser.py                       MODIFY
      → Add travel actions to SUPPORTED_ACTIONS
      → Add travel examples to SYSTEM_PROMPT
    
    mcp_router.py                          MODIFY
      → Register 6 travel connectors in __init__()
```

---

## 🔌 5 Connectors (Summary)

| Connector | API | Free? | Fallback | Time |
|-----------|-----|-------|----------|------|
| **Buses** | Buupass | Scraper | Phone# | 1.5h |
| **Hotels** | Booking.com | Affiliate | Redirect | 1h |
| **Flights** | Duffel | Sandbox | Skyscanner | 1.5h |
| **Transfers** | Karibu Taxi | Partnership | Phone# | 45m |
| **Events** | Eventbrite | API (free) | Local scrape | 1h |

---

## 📋 Week 1 Checklist

- [ ] Extend `intent_parser.py` with travel actions
- [ ] Create `travel` Django app
- [ ] Create `travel/models.py` (5 models)
- [ ] Create `BaseTravelConnector` base class
- [ ] Register 6 connectors in MCPRouter
- [ ] Create `.env` file with placeholders for API keys:
  ```
  DUFFEL_TOKEN=xxx
  EVENTBRITE_TOKEN=xxx
  BOOKING_AFFILIATE_ID=xxx
  ```
- [ ] Run `python manage.py migrate travel`
- [ ] Test intent parsing with travel actions

---

## 🌐 5 APIs (How to Access)

| API | Signup | Time | Cost | Notes |
|-----|--------|------|------|-------|
| **Duffel** | https://app.duffel.com/join | 1 min | Free sandbox | Instant access |
| **Eventbrite** | https://www.eventbrite.com | 1 day | Free tier | OAuth needed |
| **Booking.com** | Via Awin/CJ affiliate | 3-5 days | Commission-based | 25% per booking |
| **Buupass API** | Contact support | 5-7 days | Partnership | May require company |
| **Karibu Taxi** | https://www.kaributaxi.com/page/api | 3-5 days | Commission | May require company |

---

## 💾 Data Models (Quick Schema)

```python
Itinerary
  ├─ user (FK)
  ├─ title
  ├─ region (e.g. "Mombasa")
  ├─ start_date, end_date
  ├─ budget_ksh
  └─ items (reverse FK to ItineraryItem)

ItineraryItem
  ├─ itinerary (FK)
  ├─ item_type (bus/hotel/flight/transfer/event)
  ├─ title
  ├─ start_datetime, end_datetime
  ├─ provider (e.g. "Buupass")
  ├─ price_ksh
  ├─ booking_url
  └─ status (draft/booked/cancelled)

Event
  ├─ title
  ├─ start_datetime, end_datetime
  ├─ location (lat, lon)
  ├─ price_ksh
  ├─ ticket_url
  └─ provider (Eventbrite/local_scrape)

SearchCache (Redis-backed)
  ├─ query_hash
  ├─ provider
  ├─ result_json
  └─ ttl_seconds (3600 default)
```

---

## 🧪 Connector Template

```python
# Base pattern (copy for each connector)

class Travel<Type>Connector(BaseTravelConnector):
    
    async def _fetch(self, parameters: Dict, context: Dict) -> Dict:
        """
        Fetch from API/scraper.
        parameters: {"origin": "...", "destination": "...", ...}
        returns: {
            "status": "success" | "error" | "partial",
            "provider": "...",
            "results": [{...}, {...}],
            "count": N
        }
        """
        try:
            # Call API or scraper
            raw_results = await self._call_api(parameters)
            
            # Transform to standard format
            results = [
                {
                    "title": "...",
                    "price_ksh": 1500,
                    "booking_url": "https://...",
                    "provider": "..."
                }
                for item in raw_results
            ]
            
            return {
                "status": "success",
                "provider": self.provider_name,
                "results": results,
                "count": len(results)
            }
        except Exception as e:
            logger.error(f"{self.provider_name} error: {e}")
            # Return fallback/error response
            return {
                "status": "error",
                "message": f"Could not fetch {self.provider_name} data",
                "results": [],
                "count": 0
            }
```

---

## 📡 Connector in MCPRouter

```python
# In mcp_router.py __init__():

self.connectors = {
    # ... existing ...
    "search_buses": TravelBusesConnector(),
    "search_hotels": TravelHotelsConnector(),
    "search_flights": TravelFlightsConnector(),
    "search_transfers": TravelTransfersConnector(),
    "search_events": TravelEventsConnector(),
    "create_itinerary": ItineraryConnector(),  # Composite
}
```

---

## 💬 Chat Integration (in consumers.py)

```python
# In ChatConsumer.receive():

intent = await parse_intent(user_message)

if intent["action"] in ["search_buses", "search_hotels", "create_itinerary", ...]:
    # Route to MCPRouter
    result = await self.mcp_router.route(intent, user_context)
    
    # Send back to user
    await self.send(json.dumps({
        "type": "travel_result",
        "data": result
    }))
else:
    # Existing logic for other intents
    ...
```

---

## 🧠 LLM Prompts (System Prompts to Use)

### Intent Parser (already has it, just extend)
```
Supported actions:
- search_buses: origin, destination, departure_date, passengers
- search_hotels: location, check_in_date, check_out_date, budget_max_ksh
- search_flights: origin, destination, departure_date, passengers
- search_transfers: origin, destination, transfer_type
- search_events: location, date_range, interests
- create_itinerary: destination, duration, budget_ksh, preferences
```

### Itinerary Composer
```
You are a travel planner. Given search results for buses, hotels, flights, 
transfers, and events, compose a JSON itinerary with:
- Day-by-day breakdown
- No time conflicts
- Budget adherence
- Reasonable transitions between locations

Return JSON only. No explanations.
```

---

## ✅ Testing Checklist (Week 6)

- [ ] Unit test: each connector returns correct schema
- [ ] Unit test: cache hit/miss behavior
- [ ] Unit test: fallback returns when API down
- [ ] Integration test: itinerary builder composes valid JSON
- [ ] Integration test: LLM produces itineraries without hallucinations
- [ ] E2E test: user message → itinerary → export flow
- [ ] Performance test: <3 sec cached search, <10 sec itinerary
- [ ] Stress test: 100 concurrent users

---

## 🚀 Go-Live Checklist (Week 11-12)

- [ ] All tests passing
- [ ] Security audit (PII encryption, CSRF, rate-limits)
- [ ] Performance profiling done
- [ ] Error handling verified for each provider
- [ ] Privacy policy + TOS finalized
- [ ] Analytics set up (Mixpanel or Plausible)
- [ ] Error alerting configured (Sentry)
- [ ] Logging configured (CloudWatch or similar)
- [ ] Beta feedback loop complete (top 5 bugs fixed)
- [ ] Deployment automated (GitHub Actions → Render/Railway)
- [ ] Marketing content ready (blog, social, email)
- [ ] Support playbook created (FAQ, escalation)

---

## 📞 Quick Help

**"How do I get an API key?"**
→ See **5 APIs** table above.

**"What if API X fails?"**
→ See fallback column in **5 Connectors** table.

**"How do I test without API keys?"**
→ Use **Mock Connectors** (Week 6).

**"Where's the data model?"**
→ See **Data Models** section above.

**"How do I add the connector to MCPRouter?"**
→ See **Connector in MCPRouter** section.

**"How do I integrate with chat?"**
→ See **Chat Integration** section.

---

## 🎯 Success = User Can...

- [ ] Send message: "Plan Mombasa trip"
- [ ] Get itinerary in <10 seconds
- [ ] See buses, hotels, transfers, events
- [ ] Click "Book" button
- [ ] Redirects to Booking.com / Buupass / etc.
- [ ] Export as PDF / JSON
- [ ] Save itinerary (if logged in)

---

**Printed on:** December 22, 2025  
**Valid through:** Week 1 development completion  
**Questions?** See full documents in workspace
