# AI Travel & Itinerary Planner — API Decision Matrix (Kenya MVP)

**Date:** December 22, 2025  
**Status:** Research & Recommendations  
**Scope:** Kenya Launch Phase (Buses, Hotels, Airport Transfers, Flights, Events)

---

## Executive Summary

This document consolidates research on candidate APIs for the Kenya MVP. **Key finding:** Most target APIs have reasonable individual signup paths (no company registration strictly required) but have varying friction levels. Recommended approach: **prioritize Booking.com affiliate + Duffel + Karibu Taxi + Eventbrite, with web scraping fallback for buses if Buupass API proves unavailable.**

---

## 1. Intercity Buses (Kenya)

### Candidate: Buupass

| Aspect | Findings |
|--------|----------|
| **Website** | https://buses.buupass.com |
| **Company Status** | Kenya's market leader for online bus booking; partnership with Safaricom. |
| **Signup Friction** | **Moderate-High**. API access appears partnership-based (not self-serve). BuuPass mentions "APIs and partnerships" but no public signup documented. Direct contact with business team likely required. |
| **Auth Method** | Likely OAuth/bearer token (inferred from integration docs). |
| **Pricing** | Unknown publicly; likely commission or rev-share model. |
| **Individual Allowed?** | **Unclear**—likely requires business relationship; partnership model suggests company registration may be needed. |
| **Rate Limits** | Unknown. |
| **Data Coverage** | Extensive Kenya bus network coverage (Nairobi↔Mombasa, Nairobi↔Kisumu, etc.); prices, schedules, availability. |
| **Pros** | Market leader; good coverage; direct airline/bus operator integrations. |
| **Cons** | Likely requires partnership request; not self-serve for individuals. |
| **Sample Response** | Not publicly available. |

### Alternative: Web Scraping + Local Bus Operators

| Aspect | Findings |
|--------|----------|
| **Approach** | Scrape major Kenyan bus operator websites (Akamba, Easiplan, Standard Gauge Railway booking) + Buupass public pages if terms permit. |
| **Friction** | **Low**. No API signup needed; pure web scraping. |
| **Legal/Ethical** | Must respect `robots.txt` and terms of service; high-frequency scraping may trigger blocking. Cache heavily (TTL ≥ 2 hours) to reduce load. |
| **Data Quality** | Moderate; requires parsing diverse HTML layouts. |
| **Pros** | Zero friction to start; independent of partnership. |
| **Cons** | Fragile (site layout changes break parser); potentially legal gray area; rate-limit sensitive. |

### Alternative: RapidAPI Bus Aggregators

| Aspect | Findings |
|--------|----------|
| **Coverage** | RapidAPI marketplace hosts generic bus APIs, though limited Kenya-specific coverage. |
| **Signup Friction** | **Low**. Create RapidAPI account (free tier available); instant API key. |
| **Pricing** | Free tier + pay-as-you-go (typically $0.01–$0.10 per call). |
| **Data Quality** | Limited for Kenya; may require hybrid approach. |
| **Verdict** | Use as **fallback/supplementary** if Buupass unavailable. |

### Recommendation for MVP
- **Primary:** Contact Buupass for partnership/API access; expect 1–2 week negotiation.
- **Interim:** Implement web scraper for major Kenyan bus operators (fast, zero friction for MVP).
- **Fallback:** Seed DB with curated list of operator names + phone numbers, direct users to book via operator site.

---

## 2. Hotels & Accommodation

### Candidate: Booking.com (Affiliate)

| Aspect | Findings |
|--------|----------|
| **Website** | https://partnerships.booking.com/partners |
| **Signup Friction** | **Low-Moderate**. Booking.com no longer allows direct affiliate signup; must register via **Awin** or **CJ Affiliate** networks. These networks accept individuals and small publishers. |
| **Auth Method** | OAuth + unique affiliate ID per publisher. |
| **Pricing** | Commission-based: up to 25% commission on successful hotel bookings through your referral links. No upfront cost. |
| **Individual Allowed?** | **Yes**. Awin and CJ Affiliate accept individual content creators; no company registration required. |
| **Rate Limits** | Affiliate networks manage rate-limits; typical burst handling is generous. |
| **Data Coverage** | Millions of hotels worldwide, including Kenya (Nairobi, Mombasa, Kisumu, etc.). |
| **Booking Flow** | Affiliate → search widget or JSON API → Booking.com property links → user books → you earn commission. |
| **Pros** | Lowest friction; immediate commission revenue; massive inventory; trusted brand. |
| **Cons** | No direct availability data (must redirect user or use their widgets); cookie duration (24–30 days typical). |
| **Sample Response** | Not applicable (affiliate, not data API). Integration via links + widgets. |

### Steps to Activate Booking.com Affiliate for MVP
1. Sign up with **Awin** (https://www.awin.com) or **CJ Affiliate** (https://www.cj.com) as an individual publisher.
2. Apply for Booking.com partnership (usually approved in 3–5 days).
3. Integrate Booking.com hotel search widget or generate affiliate links dynamically.
4. Track referrals via Awin/CJ dashboard.

### Recommendation for MVP
- **Primary:** Use Booking.com affiliate + search widget integration.
- **Redirect Flow:** User searches hotel → display Booking.com-powered results → click → redirect to Booking.com booking page with your affiliate ID.
- **No direct payment needed:** Booking.com handles payments; you earn commission.

---

## 3. Flights

### Candidate: Duffel API

| Aspect | Findings |
|--------|----------|
| **Website** | https://duffel.com |
| **Signup Friction** | **Very Low**. 1-minute signup at https://app.duffel.com/join. Immediate sandbox access with test token. |
| **Auth Method** | Bearer token (REST API). |
| **Pricing** | Usage-based (pay per search/booking). No public per-unit pricing disclosed; requires contact for volume pricing. Free sandbox for testing. |
| **Individual Allowed?** | **Yes**. Designed for developers and startups; no company registration mandatory for sandbox. Production may require business info. |
| **Rate Limits** | Not publicly documented; contact Duffel for limits. Typically generous for MVP volumes. |
| **Airline Coverage** | 300+ airlines, including major African carriers (Kenya Airways, Ethiopian, etc.). Limited but growing Kenya-specific coverage. |
| **Data Available** | Search (itineraries, prices), booking, order management, payment collection (limited regions). |
| **Sandbox vs. Live** | Sandbox = unlimited balance; live = requires credit card + balance top-up. |
| **Pros** | Fast onboarding; developer-friendly; REST + client libraries (Python, JS, etc.); direct booking support. |
| **Cons** | Usage-based pricing (unknown cost per search); limited Kenya airline direct integration; requires payment setup for live. |
| **Sample Response** | Well-documented in GitHub repo (hackathon-starter-kit) and API docs. |

### Getting Started with Duffel
1. Sign up at https://app.duffel.com/join (name, email, password).
2. Navigate to Developers → Access Tokens → create test token.
3. Use sandbox for development (test airline "Duffel Airways").
4. Test flight search and order creation via provided Postman collection.

### Recommendation for MVP
- **Primary:** Integrate Duffel for flight search and price display.
- **Booking Strategy:** For MVP, use **redirect to Duffel dashboard** or **partner booking** rather than direct balance-funded orders (to avoid upfront cash outlay).
- **Alternative Approach:** Display flight search results but redirect user to Skyscanner/Kiwi/Expedia for final booking (search-only, no integration cost).

---

## 4. Airport Transfers & Local Taxis

### Candidate: Karibu Taxi

| Aspect | Findings |
|--------|----------|
| **Website** | https://www.kaributaxi.com |
| **API Availability** | Yes. "Open APIs that connect to your business with ease." API page: https://www.kaributaxi.com/page/api |
| **Signup Friction** | **Moderate**. No self-serve API signup found; requires direct contact via their API page to request credentials. |
| **Auth Method** | Likely bearer token or API key (not publicly documented). |
| **Pricing** | Commission or markup on bookings. Exact terms unclear; requires direct discussion. |
| **Individual Allowed?** | **Likely yes** (no explicit company requirement documented). |
| **Services Covered** | Airport → City, City → Airport, Intra-city transfers, Hourly bookings. Covers Nairobi (likely Jomo Kenyatta International Airport primary). |
| **Data Available** | Transfer quotes, availability, driver info, payment integration. |
| **Pros** | Focused on Kenya airport transfers (core need for MVP); pre-existing platform. |
| **Cons** | Not self-serve; limited public documentation; requires direct contact. |

### Getting Started with Karibu Taxi
1. Visit https://www.kaributaxi.com/page/api.
2. Fill out API request form (or call/email contact listed).
3. Expect response in 3–5 business days with sandbox credentials.
4. Test quote and booking endpoints in sandbox.

### Alternative: Manual Partner Integration
- If Karibu API unavailable, provide **phone number / WhatsApp booking link** for transfers (low-tech but functional for MVP).
- Offer preset quotes from popular operators (negotiate directly with 2–3 trusted taxi partners).

### Recommendation for MVP
- **Primary:** Apply for Karibu API access; plan 1–2 week negotiation.
- **Interim:** Partner with 1–2 direct taxi operators (e.g., call/WhatsApp quotes, add to itinerary as "Call to confirm" items).
- **Fallback:** Display verified operator contact info + estimated rates. User books manually.

---

## 5. Events Discovery

### Candidate: Eventbrite API

| Aspect | Findings |
|--------|----------|
| **Website** | https://www.eventbrite.com/platform/api |
| **Signup Friction** | **Low**. Create Eventbrite account (free); request API access via dashboard. Access typically granted within 24 hours. |
| **Auth Method** | OAuth 2.0 (server-side or client-side flow). |
| **Pricing** | Free for search API; commission on ticket sales (not applicable for search-only MVP). |
| **Individual Allowed?** | **Yes**. Eventbrite designed for individual event organizers and third-party tools. |
| **Rate Limits** | Not publicly specified; typical REST API limits apply. Contact support for high-volume needs. |
| **Data Available** | Event search by location, category, date; event details, ticket availability, pricing. |
| **Kenya Coverage** | Events across Kenya (Nairobi, Kisumu, Mombasa, etc.) indexed in Eventbrite. Coverage growing. |
| **Pros** | Easiest onboarding of all; free; great for MVP; covers Kenya events. |
| **Cons** | Kenya event coverage may be incomplete (many local events not on Eventbrite); requires supplementary data sources. |

### Getting Started with Eventbrite
1. Create account at https://www.eventbrite.com.
2. Go to Account Settings → Apps & Integrations → create personal access token (or register OAuth app).
3. Use API endpoint: `GET https://www.eventbriteapi.com/v3/events/search/?location=...` (with location/geo filtering).
4. Response includes event IDs, titles, dates, venue, ticket info.

### Supplementary Events Sources
- **Facebook Events:** Graph API available but heavier setup; good for local event discovery. Consider if budget allows.
- **Local Kenya event listing sites:** Pulse Kenya (pulselivingkenya.com), Aura (aura.events), local municipal calendars.
- **Scraping:** Small local event websites if needed (respecting robots.txt).

### Recommendation for MVP
- **Primary:** Eventbrite API for structured event search.
- **Supplementary:** Scrape or partner with 1–2 local Nairobi event aggregators to boost coverage.
- **Future:** Add Facebook Graph API integration once baseline is working.

---

## 6. Payments & Mobile Money

### Context: M-Pesa (Daraja)

| Aspect | Findings |
|--------|----------|
| **Service** | M-Pesa Daraja API for Kenya mobile payments. |
| **Signup Friction** | **Moderate-High**. Requires Safaricom business account; registration with Safaricom portal. Typical turnaround: 3–7 days. |
| **Individual Allowed?** | **Unclear**. Officially targets businesses. Individual registration may face friction. |
| **Recommendation for MVP** | **Skip for MVP.** Use redirect/affiliate payment flows (Booking.com + Duffel handle payments). For MVP, focus on **booking links** and **affiliate revenue**, not direct M-Pesa integration. |
| **Future** | Revisit M-Pesa after MVP if direct payment collection becomes critical. |

---

## Summary Decision Table

| Category | Primary Choice | Signup Friction | Individual OK? | Cost | Notes |
|----------|---|---|---|---|---|
| **Buses** | Buupass API (or web scrape) | Moderate-High | Unclear | Partnership/Free | Contact for partnership; interim scrape solution. |
| **Hotels** | Booking.com Affiliate (via Awin/CJ) | Low | **Yes** | Commission-based | Fastest route; 3–5 day approval. |
| **Flights** | Duffel API | Very Low | **Yes** | Usage-based | 1-min signup; sandbox free. |
| **Transfers** | Karibu Taxi API (or manual) | Moderate | Likely | Commission/Markup | Contact for API; fallback to direct partners. |
| **Events** | Eventbrite API | Low | **Yes** | Free (search) | Instant approval; supplement with local sources. |
| **Payments** | Skip (use redirects) | N/A | N/A | N/A | Address in Phase 2 after MVP. |

---

## Recommended MVP Implementation Sequence

### Phase 1 (Weeks 1–2): Parallel Setup
1. **Booking.com Affiliate:** Sign up via Awin/CJ (should be approved by end of Week 1).
2. **Duffel:** Sign up, explore sandbox, review flight search endpoints.
3. **Eventbrite:** Create account, get API token, test search endpoint.
4. **Buupass Contact:** Reach out to partnerships team; initiate discussion.
5. **Karibu Taxi Contact:** Submit API request form.

### Phase 2 (Weeks 3–4): Fallback Preparation
1. **Bus Scraper:** Build web scraper for major Kenyan operators (Akamba, Easiplan, etc.) as interim solution while Buupass negotiation is ongoing.
2. **Mock Connectors:** Create local mock connectors for all APIs (for dev/testing without live keys).
3. **Database Seeding:** Prepare CSV/JSON for pre-loaded bus routes, hotel partners, event categories.

### Phase 3 (Weeks 5–6): Integration
- Implement connectors for confirmed APIs (Booking.com, Duffel, Eventbrite, Karibu if available).
- Integrate scraped data or fallback sources as needed.
- Build itinerary composition and booking link generation.

---

## LangChain: Recommendation

**Short Answer:** Not needed for API orchestration in MVP. Use existing repo's `orchestration/llm_client.py` + connector pattern.

**Long Answer:**
- MVP connectors are straightforward (search, redirect, display results). No complex agent behavior needed.
- LangChain adds a dependency; its tool-calling and agent orchestration are overkill for initial launch.
- **Suggest:** Build connectors explicitly in code (you control every step). Later, if you need multi-step reasoning (e.g., "find hotel near event, then book transfer, then check available flights"), consider LangChain or similar for that dialog loop.
- For now: Use LLM client for **conversational intent parsing** (user writes "I want to go to Mombasa next weekend") and **itinerary composition** (LLM generates JSON schema), not tool calling.

---

## Risk Mitigation

| Risk | Mitigation |
|------|-----------|
| **Buupass API unavailable** | Fallback web scraper + direct partner contacts. |
| **Duffel pricing shock** | Test small volume in sandbox; negotiate volume discount before go-live. |
| **Karibu API not available** | Direct taxi operator partnerships (phone/WhatsApp). |
| **Eventbrite coverage gaps** | Supplement with local event aggregators or scraping. |
| **Rate limits hit** | Implement caching (Redis) and exponential backoff in connectors; monitor usage. |
| **Payment friction** | Use affiliate/redirect model for MVP; direct M-Pesa in Phase 2. |

---

## Next Steps

1. **Immediate (This Week):**
   - User review and approve this decision matrix.
   - Start Booking.com affiliate signup (via Awin/CJ).
   - Create Duffel sandbox account and test flight search.
   - Create Eventbrite account and test event search.

2. **Short Term (Week 2):**
   - Contact Buupass for API partnership.
   - Contact Karibu Taxi for API access.
   - Build bus web scraper as interim solution.
   - Create mock connectors for all 5 categories.

3. **Implementation (Week 3+):**
   - Implement live connectors as APIs become available.
   - Integrate into orchestration layer.
   - Build UI and test end-to-end flows.

---

## Appendices

### Appendix A: Quick Reference — Signup Links

| Service | Signup Link | Expected Friction | Time to API Access |
|---------|---|---|---|
| Awin Affiliate (for Booking.com) | https://www.awin.com | Low | 1–3 days |
| CJ Affiliate (for Booking.com) | https://www.cj.com | Low | 1–3 days |
| Duffel | https://app.duffel.com/join | Very Low | Instant |
| Eventbrite | https://www.eventbrite.com | Low | 1 day |
| Buupass Partnerships | https://buupass.com (contact form) | High | 3–7 days (estimate) |
| Karibu Taxi API | https://www.kaributaxi.com/page/api | Moderate | 3–5 days (estimate) |

### Appendix B: Sample API Call Skeleton (Python)

```python
# Duffel flight search (requires token)
import requests

DUFFEL_BASE = "https://api.duffel.com"
token = "duffel_test_..."

def search_flights(origin, destination, departure_date):
    payload = {
        "slices": [
            {
                "origin_airport_iata_code": origin,
                "destination_airport_iata_code": destination,
                "departure_date": departure_date,
            }
        ],
        "passengers": [{"type": "adult"}],
    }
    headers = {"Authorization": f"Bearer {token}"}
    response = requests.post(f"{DUFFEL_BASE}/air/offer_requests", json=payload, headers=headers)
    return response.json()

# Eventbrite event search (no auth needed for public search)
def search_events(location, keyword=""):
    params = {
        "location.address": location,
        "q": keyword,
        "token": EVENTBRITE_TOKEN,
    }
    response = requests.get("https://www.eventbriteapi.com/v3/events/search/", params=params)
    return response.json()
```

---

**Document prepared by:** AI Planning Agent  
**Last updated:** December 22, 2025  
**Next review:** After Week 1 signup attempts (Dec 29, 2025)
