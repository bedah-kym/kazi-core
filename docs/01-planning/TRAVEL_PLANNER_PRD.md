# AI Travel & Itinerary Planner — Product Requirements Document (Kenya MVP)

**Version:** 1.0 (MVP - Kenya)  
**Date:** December 22, 2025  
**Scope:** First Local Kenyan Users MVP  
**Target Launch:** Q1 2026  

---

## 1. Executive Summary

This document defines the MVP for an AI-powered travel and itinerary planner targeting Kenyan travelers first, with expansion roadmap to Pan-Africa and worldwide. The MVP focuses on **essential journey planning**: intercity buses, accommodations, airport transfers, flights (search-only initially), local events, and itinerary composition with booking redirects.

**Key constraint:** Non-registered individual developer (no company). Solution prioritizes low-friction API partners and affiliate/redirect booking flows (no direct payment collection).

**Success metrics (MVP):**
- 100 test itineraries successfully composed and validated
- End-to-end search → itinerary → booking redirect flow functional
- Conversational assistant can handle 80% of common Kenya travel queries
- Zero unintended payment collection issues

---

## 2. Product Vision

### Problem Statement
Kenyan travelers (and soon Pan-African) currently use fragmented tools to plan journeys:
- Bus search on Buupass or Safaricom SGR
- Hotel search on Booking.com or local sites
- Airport transfers via phone/WhatsApp (friction-heavy)
- Events discovered on Facebook or Nairobi event blogs

**Desired outcome:** One unified, AI-assisted platform to search, compare, and book all travel components (buses, hotels, transfers, flights, events) in a single itinerary, with pre-filled booking links for seamless onboarding.

### Target User Persona (MVP)

**Primary:** Urban Kenyan Travelers (ages 18–45)
- Frequent intercity travelers (Nairobi ↔ Mombasa, Kisumu, etc.)
- Comfortable with mobile-first platforms (WhatsApp, Safaricom-integrated)
- Budget to mid-range travelers (prefer value + convenience)
- Occasional event attendees (concerts, conferences, weekend markets)

**Use Case:**
- "I need to go to Mombasa next weekend for a beach trip. Help me book a bus, find a hotel near the beach, arrange airport transfer, and show me events happening."
- "Plan a 3-day Nairobi business trip: hotels in CBD, flights to Kisumu for a meeting, airport transfer, and networking events."

---

## 3. Scope & Core Features (MVP)

### 3.1 Feature Matrix

| Feature | Priority | Notes |
|---------|----------|-------|
| **Search Intercity Buses** | P0 (Must-Have) | Nairobi, Mombasa, Kisumu, Nakuru, Eldoret, Kisii routes. Price, time, availability. |
| **Search Hotels** | P0 (Must-Have) | Nationwide; Booking.com integration via affiliate links. |
| **Search Airport Transfers** | P0 (Must-Have) | Nairobi JKIA ↔ CBD, hotel destinations; Karibu Taxi integration or partner quotes. |
| **Search Flights** | P1 (Should-Have) | Duffel or search-only (redirect to Skyscanner/Expedia). Kenya Airways + international. |
| **Events Discovery** | P0 (Must-Have) | Geofenced search by city/region. Eventbrite + local aggregators. |
| **Itinerary Composition** | P0 (Must-Have) | Day-by-day timeline; drag-drop reordering; conflict detection. |
| **Booking Links** | P0 (Must-Have) | Pre-filled redirect to provider (affiliate or booking system). |
| **Conversational Assistant** | P0 (Must-Have) | Chat-based trip planning. LLM generates initial itinerary; user refines. |
| **Export Itinerary** | P1 (Nice-to-Have MVP+) | PDF, JSON, iCal for email/sharing. |
| **User Profiles & Saved Itineraries** | P1 (Nice-to-Have MVP+) | Login, save drafts, sharing links. |
| **Map View** | P1 (Nice-to-Have MVP+) | Leaflet or Google Maps; show hotels, events, transfers on map. |
| **Push Notifications** | P2 (Post-MVP) | Booking reminders, price drops (requires Celery task setup). |
| **Ratings & Reviews** | P2 (Post-MVP) | User feedback on buses, hotels, drivers. |
| **Direct M-Pesa Payments** | P2 (Post-MVP) | Phase 2 after MVP; requires business account. |
| **Multiple Languages** | P1 (Nice-to-Have MVP+) | English + Swahili. Start with English; add Swahili UI + chatbot localization. |

### 3.2 Out of Scope (MVP)

- Direct payment collection (M-Pesa integration, credit card processing)
- Visa/immigration assistance
- Travel insurance
- Car rental
- Multi-city trip optimization algorithm
- Real-time tracking of booked items
- Loyalty program integration
- Merchant dashboard or supplier panel

---

## 4. User Flows

### 4.1 Primary Flow: Search & Create Itinerary (Web)

```
User lands on homepage
  ↓
[Option A: Quick Search]
  → Select destination, dates, trip type (bus+hotel, events, etc.)
  → Click "Search & Plan"
  → Display results (buses, hotels, transfers, events)
  → User selects items
  → Itinerary auto-created
  
[Option B: Conversational]
  → Click "Ask AI"
  → Type: "I want to go to Mombasa this weekend"
  → LLM generates initial itinerary
  → Display in editor
  → User refines (drag-drop, add/remove items)
  
[Both paths converge]
  ↓
Review itinerary timeline
  ↓
Book via redirect (one-click per item)
  → Booking.com → hotel booking
  → Buupass → bus booking
  → Karibu → transfer booking
  → Eventbrite → event ticket
  ↓
Share/export (email, PDF, JSON)
  ↓
View saved itinerary (if logged in)
```

### 4.2 Conversational Flow (Chatbot)

```
User opens chat (Channels WebSocket)
  ↓
User: "Plan a 3-day Nairobi + Mombasa trip. Budget: 5000 KES."
  ↓
LLM (Anthropic/HF Router)
  → Parse intent: trip duration, destinations, budget
  → Call orchestrator (MCPRouter)
    → connector:search_buses (Nairobi→Mombasa, dates)
    → connector:search_hotels (Mombasa, dates, budget range)
    → connector:search_events (Mombasa, dates)
    → connector:search_transfers (JKIA→hotel or inter-city)
  ↓
LLM composes JSON itinerary:
  {
    "itinerary": [
      {"day": 1, "items": [
        {"type": "bus", "from": "Nairobi", "to": "Mombasa", "price": 1500, "booking_url": "..."},
        {"type": "hotel", "city": "Mombasa", "price": 2500, "booking_url": "..."},
        {"type": "event", "name": "Mombasa Food Festival", "time": "18:00", "booking_url": "..."}
      ]},
      ...
    ]
  }
  ↓
Display itinerary in editor
  ↓
User: "Add sunset beach tour" → LLM searches events, inserts
User: "Change bus to a matatu" → (P1) Show alternative operator options
  ↓
User: "Book everything" → Generate booking links, open tabs
```

---

## 5. System Architecture (High Level)

### 5.1 Stack & Reuse from Existing Repo

| Component | Choice | Rationale |
|-----------|--------|-----------|
| **Backend** | Django (existing) | Reuse chatbot, users, orchestration apps. |
| **Real-time** | Channels + WebSockets | Reuse chatbot consumers; live itinerary sync. |
| **Task Queue** | Celery + Beat | Reuse for background searches, price tracking. |
| **Cache** | Redis | Rate-limiting, search result caching. |
| **LLM Orchestration** | `llm_client.py` (existing) | Use existing Anthropic/HF Router setup. |
| **API Connectors** | `orchestration/connectors/` pattern | Extend existing pattern; add travel connectors. |
| **Frontend** | Django templates + HTMX or React | Consider lightweight HTMX for MVP (faster). |
| **Deploy** | Docker Compose (existing) | Reuse db, redis, web, worker, beat services. |

### 5.2 New Models

```python
# Backend/orchestration/models.py (add)

class Itinerary(models.Model):
    user = ForeignKey(User)
    title = CharField(max_length=200)
    description = TextField(blank=True)
    region = CharField(max_length=100)  # "Nairobi", "Mombasa", etc.
    start_date = DateField()
    end_date = DateField()
    budget_ksh = IntegerField(default=0, help_text="KES")
    is_public = BooleanField(default=False)
    created_at = DateTimeField(auto_now_add=True)
    updated_at = DateTimeField(auto_now=True)
    metadata = JSONField(default=dict)  # Custom user data

class ItineraryItem(models.Model):
    ITEM_TYPES = [
        ('bus', 'Bus'),
        ('hotel', 'Hotel'),
        ('flight', 'Flight'),
        ('transfer', 'Transfer'),
        ('event', 'Event'),
        ('activity', 'Activity'),
    ]
    
    itinerary = ForeignKey(Itinerary, on_delete=CASCADE)
    item_type = CharField(max_length=20, choices=ITEM_TYPES)
    start_datetime = DateTimeField()
    end_datetime = DateTimeField(blank=True, null=True)
    title = CharField(max_length=200)
    provider = CharField(max_length=100)  # "Buupass", "Booking.com", "Karibu", etc.
    price_ksh = IntegerField(default=0)
    booking_url = URLField(blank=True)
    booking_reference = CharField(max_length=100, blank=True)
    status = CharField(max_length=20, default='draft')  # draft, booked, cancelled
    metadata = JSONField(default=dict)  # Provider-specific data
    order = IntegerField(default=0)  # For sorting

class SearchCache(models.Model):
    """Caches search results to avoid redundant API calls."""
    query_hash = CharField(max_length=64, unique_together=[('provider', 'query_hash')])
    provider = CharField(max_length=100)  # "buupass", "booking", etc.
    result_json = JSONField()
    ttl_seconds = IntegerField(default=3600)  # 1 hour default
    created_at = DateTimeField(auto_now_add=True)

class BookingReference(models.Model):
    """Tracks affiliate/partner bookings."""
    itinerary_item = ForeignKey(ItineraryItem, on_delete=CASCADE)
    provider = CharField(max_length=100)
    provider_booking_id = CharField(max_length=200)
    status = CharField(max_length=20)  # pending, confirmed, cancelled
    booking_url = URLField()
    user_ip = GenericIPAddressField(blank=True, null=True)
    created_at = DateTimeField(auto_now_add=True)

class Event(models.Model):
    """Local events for display in itinerary."""
    title = CharField(max_length=200)
    description = TextField(blank=True)
    start_datetime = DateTimeField()
    end_datetime = DateTimeField()
    location_name = CharField(max_length=200)
    latitude = FloatField()
    longitude = FloatField()
    price_ksh = IntegerField(default=0, help_text="0 = free")
    ticket_url = URLField(blank=True)
    provider = CharField(max_length=100)  # "eventbrite", "local_scrape", etc.
    provider_event_id = CharField(max_length=200, blank=True)
    category = CharField(max_length=50, blank=True)  # "concert", "conference", "market"
    created_at = DateTimeField(auto_now_add=True)

    class Meta:
        indexes = [
            models.Index(fields=['start_datetime', 'location_name']),
            models.Index(fields=['latitude', 'longitude']),
        ]
```

### 5.3 New Connectors (in `Backend/orchestration/connectors/`)

```
connectors/
  __init__.py
  buses_connector.py      # Buupass API or scraper
  hotels_connector.py     # Booking.com affiliate search
  flights_connector.py    # Duffel API
  transfers_connector.py  # Karibu Taxi API
  events_connector.py     # Eventbrite API
  
  # Mocks for development (no keys needed)
  mock_buses.py
  mock_hotels.py
  mock_flights.py
  mock_transfers.py
  mock_events.py
```

Each connector implements:
```python
class BusConnector(BaseConnector):
    async def execute(self, parameters: Dict, context: Dict) -> Dict:
        """
        parameters: {
            "origin": "Nairobi",
            "destination": "Mombasa",
            "departure_date": "2025-12-25",
            "passengers": 1
        }
        
        returns: {
            "status": "success" | "error",
            "message": "...",
            "results": [
                {
                    "provider": "Buupass",
                    "departure_time": "06:00",
                    "arrival_time": "12:00",
                    "price_ksh": 1500,
                    "booking_url": "https://..."
                }
            ]
        }
        """
```

### 5.4 LLM Integration (Prompt Engineering)

**System Prompt for Itinerary Generation:**
```
You are a helpful travel assistant for Kenyan travelers. 
Your role is to help users plan multi-day itineraries combining:
- Intercity buses (Buupass, local operators)
- Hotels (Booking.com, local properties)
- Airport transfers (Karibu Taxi, partner operators)
- Flights (Duffel, Skyscanner)
- Local events (Eventbrite, local listings)

User provides: destination, dates, budget (KES), preferences.
You respond with a JSON itinerary object with this schema:

{
  "itinerary": [
    {
      "day": 1,
      "items": [
        {
          "type": "bus|hotel|flight|transfer|event",
          "title": "...",
          "provider": "...",
          "start_datetime": "2025-12-25T06:00:00",
          "end_datetime": "2025-12-25T12:00:00",
          "price_ksh": 1500,
          "booking_url": "https://..."
        }
      ]
    }
  ],
  "total_cost_ksh": 5000,
  "notes": "..."
}

Ensure: no time conflicts, reasonable transitions, budget adherence.
```

---

## 6. Success Criteria & Acceptance Tests

### 6.1 Functional Requirements (MVP Release)

| ID | Feature | Acceptance Criteria |
|----|---------|-------|
| F1 | Bus search | User can search intercity buses (origin, destination, date) and see ≥1 result with price and booking link |
| F2 | Hotel search | User can search hotels (location, dates) and see ≥3 results via Booking.com affiliate links |
| F3 | Transfer search | User can search airport transfers and see ≥1 quote with price |
| F4 | Flight search | User can search flights and see ≥1 option (search-only or Duffel quotes) |
| F5 | Event search | User can search events by location/date and see ≥2 results from Eventbrite |
| F6 | Create itinerary (Web) | User can manually select items and create a day-by-day itinerary without errors |
| F7 | Create itinerary (Chat) | User can describe a trip in chat; LLM generates JSON itinerary; user can refine |
| F8 | Booking redirects | Every item in itinerary has a clickable "Book" button that opens provider's booking page with pre-filled data |
| F9 | Export | User can export itinerary as JSON or PDF |
| F10 | Conversational safety | No erroneous payment requests; all money handling redirected to providers |

### 6.2 Non-Functional Requirements

| Requirement | Target | Rationale |
|-------------|--------|-----------|
| **Search response time** | <3 seconds (cached) | User expectation; cached queries should be instant. |
| **LLM itinerary generation** | <10 seconds | Conversational flow must feel snappy. |
| **Availability (MVP)** | 95% (during business hours) | Early MVP; not 99.9%. |
| **Mobile responsiveness** | >90% of UI functional on 320px+ | Kenya: significant mobile traffic. |
| **Accessibility (WCAG)** | AA | Standard web practice. |
| **Data security** | Encrypt PII (user emails, phone); no card storage | Reuse existing `users/encryption.py`. |
| **Audit logging** | Log all booking redirects, API calls | Fraud detection + debugging. |

### 6.3 Verification Test Scenarios

**Test 1: Happy Path (Nairobi → Mombasa weekend)**
```
1. Open homepage, select "Quick Search"
2. Nairobi → Mombasa, Dec 28–30, 1 pax
3. View results: ≥1 bus, ≥3 hotels, ≥1 transfer, ≥2 events
4. Select: Buupass bus (Ksh 1500), Booking.com hotel (Ksh 3000), Karibu transfer (Ksh 1000), Eventbrite beach event
5. Review itinerary (timeline, cost = Ksh 5500)
6. Click "Book Bus" → Buupass redirects with pre-filled data
7. Click "Book Hotel" → Booking.com affiliate redirects
8. Export as PDF → download successful
```

**Test 2: Conversational (Chat)**
```
1. Open chat widget
2. Type: "Help me plan a 3-day Nairobi business trip. Budget: 10k KES"
3. LLM responds with proposed itinerary (hotels in CBD, flights if needed, events)
4. User: "Change hotel to something cheaper" → LLM regenerates with budget adjustments
5. User: "Add CBD walking tour" → LLM searches events, inserts into Day 2
6. User: "Send me the plan" → Email itinerary as PDF
```

**Test 3: Error Handling (API Failure)**
```
1. Search buses with valid params
2. Buupass API returns 500 error
3. App displays: "Unable to fetch bus results. Showing alternatives from local operators."
4. Display fallback list (curated bus operators + phone numbers)
5. User can still create itinerary with alternatives
```

---

## 7. Rollout & Launch Plan

### 7.1 MVP Launch Timeline

| Phase | Duration | Activities | Deliverables |
|-------|----------|-----------|---|
| **Design & Setup** | Week 1–2 | API signups, architecture finalization, mock servers | Decision matrix ✓, Architecture doc, connector stubs |
| **Development** | Week 3–8 | Backend (models, connectors, orchestration), frontend UI, chat integration | Working search endpoints, UI screens, chat flow |
| **QA & Testing** | Week 9–10 | E2E tests, load testing, security audit, user feedback | 100 test itineraries, CI passing |
| **Staging & Launch** | Week 11–12 | Deploy to Render/Railway, beta user testing, go-live | Public staging URL, launch announcement |

**Target Live Date:** End of Q1 2026 (late March 2026)

### 7.2 Launch Checklist (Kenya MVP)

- [ ] All P0 features tested and working
- [ ] No unintended payment collection
- [ ] Privacy policy drafted
- [ ] Terms of service approved
- [ ] Affiliate links disclosed in UI
- [ ] Mobile responsiveness verified
- [ ] Chatbot prompts tested for safety and accuracy
- [ ] Load test (100 concurrent users)
- [ ] Error handling for each provider verified
- [ ] Logging and monitoring in place
- [ ] Team trained on support (common user issues)
- [ ] Social media launch content prepared

### 7.3 Beta & Feedback Loop

**Beta Duration:** 2–4 weeks (50–100 users)
- Internal team + friends + Nairobi tech community
- Collect feedback on UX, missing features, bugs
- Iterate on top 3 issues
- Prepare public announcement

---

## 8. Risk Register

| Risk | Likelihood | Impact | Mitigation |
|------|-----------|--------|-----------|
| Buupass API unavailable | Medium | High | Web scraper ready as fallback; manual operator contact list. |
| Duffel pricing shock | Low | Medium | Test volume pricing early; negotiate if needed. |
| Eventbrite API incomplete (limited Kenya events) | Medium | Low | Supplement with local scraping + manual curation. |
| Rate limits exceeded | Medium | Medium | Redis caching (2-hour TTL); exponential backoff in connectors. |
| Payment fraud via booking links | Low | High | Use affiliate/redirect model (provider handles payment). No direct card collection. |
| LLM hallucination (invalid itineraries) | Medium | Medium | JSON schema validation; manual review in MVP; user-facing warnings. |
| Mobile UX too complex | Medium | Medium | Prioritize mobile-first design; user testing in beta. |
| Scaling to Pan-Africa without redesign | Low | High | Design for region-agnostic connectors from day 1; plug-and-play new providers. |

---

## 9. Metrics & KPIs (Post-Launch)

| Metric | Target (Q1) | Definition |
|--------|----------|------------|
| **Monthly Active Users** | 500 | Users who create ≥1 itinerary/month |
| **Itineraries Created** | 1000+ | Total itineraries (draft + booked) |
| **Booking Click-Through Rate** | >30% | Users who click "Book" / users who view itinerary |
| **Affiliate Commission Revenue** | TBD | Booking.com + other affiliate income |
| **Chat Conversation Success Rate** | >70% | Successful itinerary generation without user correction |
| **Mobile Traffic %** | >60% | Mobile users / total users |
| **Support Tickets** | <50/month | Common issues resolved in beta; <1/user/month target |

---

## 10. Roadmap (Phase 2+)

### Phase 2 (Q2 2026): Expand to Pan-Africa

- Add more countries: Uganda, Tanzania, South Africa, Nigeria
- Integrate M-Pesa + local payment methods (Airtel Money, MTN Mobile Money, etc.)
- Add user reviews & ratings
- Multi-language support (Swahili, French, Portuguese)

### Phase 3 (Q3 2026+): Global + Monetization

- Worldwide coverage (flights, hotels, activities)
- Business partnerships (affiliate tracking, sponsored listings)
- Direct booking integration (higher risk/compliance)
- Mobile app (React Native or Flutter)
- Price tracking & drop alerts

---

## Appendix A: Glossary

| Term | Definition |
|------|-----------|
| **Affiliate link** | URL with tracking parameter (e.g., `booking.com/?aff_id=123`); provider tracks clicks, earns commission. |
| **Itinerary** | Day-by-day travel plan with buses, hotels, events, transfers. |
| **Booking reference** | Confirmation number for a booked item (e.g., Buupass ticket ID, Booking.com reservation code). |
| **Geofenced** | Location-based filtering (e.g., events within 10 km of Nairobi CBD). |
| **MVP** | Minimum Viable Product; Kenya launch with core features only. |
| **Provider** | External service (Buupass, Booking.com, Duffel, Karibu Taxi, Eventbrite). |

---

**Document Prepared By:** AI Planning Agent  
**Reviewed By:** [User to confirm]  
**Date:** December 22, 2025  
**Status:** Draft (awaiting user review)

---

## User Sign-Off

- [ ] User acknowledges PRD and MVP scope
- [ ] User approves feature priority and timeline
- [ ] User confirms budget and resource constraints
- [ ] User ready to proceed to Architecture & Design (Task 3)

**Please review and provide feedback; let me know if any sections need clarification or change before we proceed to detailed design.**
