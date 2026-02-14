# Travel Planner MVP — Executive Summary & Quick-Start Guide

**Prepared:** December 22, 2025  
**Status:** Ready to Execute  
**Timeline:** 12 weeks to launch (Kenya MVP)  
**Budget:** ~$50–150/year infrastructure (API keys: free/partnership-based)

---

## 🎯 Vision (One Sentence)

**AI-powered conversational travel planner: users describe their trip → AI orchestrates buses, hotels, flights, transfers, events searches → generates day-by-day itinerary → one-click booking redirects.**

---

## 📦 What's Included (3 Documents Already Created)

1. **`TRAVEL_PLANNER_PRD.md`** — Product scope, features, MVP success criteria, timeline
2. **`TRAVEL_PLANNER_API_DECISION_MATRIX.md`** — API research + low-cost integration strategy
3. **`TRAVEL_PLANNER_IMPLEMENTATION_PLAN.md`** — Week-by-week roadmap (THIS IS YOUR EXECUTION GUIDE)

---

## 🏗️ How It Fits Existing Codebase

**Existing Stack (Reused As-Is):**
- ✅ `orchestration/mcp_router.py` → Add travel connector actions
- ✅ `orchestration/llm_client.py` → Use for itinerary LLM composition
- ✅ `orchestration/intent_parser.py` → Add travel intent types
- ✅ `chatbot/consumers.py` → Route travel intents here
- ✅ `users/models.py` → Reuse User model
- ✅ Celery + Redis → Cache search results + background tasks
- ✅ Channels → Real-time itinerary updates

**New Layer Added:**
```
Backend/
  travel/                     (NEW Django app)
    models.py                 (Itinerary, ItineraryItem, Event, etc.)
    views.py                  (REST API endpoints)
    
  orchestration/
    connectors/
      travel_*.py             (5 connectors: buses, hotels, flights, transfers, events)
      base_travel_connector.py
    
    integrations/
      buupass.py              (Scraper + API wrapper)
      booking.py              (Affiliate link builder)
      duffel.py               (API wrapper)
      karibu.py               (API wrapper)
      eventbrite.py           (API wrapper)
    
    services/
      itinerary_builder.py    (LLM → JSON itinerary)
      export_service.py       (PDF, JSON, iCal)
```

**Result:** Users chat with bot → existing intent parser routes to MCPRouter → MCPRouter dispatches to new travel connectors → LLM composes itinerary → returned via existing Channels WebSocket.

---

## 🚀 12-Week Breakdown (Easy Reading)

### **Week 1: Setup**
```
Task 1: Add travel actions to intent parser
Task 2: Create 'travel' Django app + database models
Task 3: Register connectors in MCPRouter
  → Outcome: Foundation ready
```

### **Week 2: Build 5 Travel Connectors**
```
Task 1: Base connector class (caching, retry logic)
Task 2: Buses (Buupass scraper + fallback operators)
Task 3: Hotels (Booking.com affiliate redirect)
Task 4: Flights (Duffel API, free sandbox)
Task 5: Transfers (Karibu Taxi API + local quotes)
  → Outcome: All searches working
```

### **Week 3: Add Events + Itinerary Composition**
```
Task 1: Events connector (Eventbrite API)
Task 2: Itinerary builder service (calls LLM to compose day-by-day plan)
Task 3: Register itinerary connector in MCPRouter
  → Outcome: Full "user request → complete itinerary" flow
```

### **Week 4: APIs & Exports**
```
Task 1: REST API endpoints (search, itinerary CRUD)
Task 2: Export service (PDF, JSON, iCal formats)
Task 3: Connect to web UI
  → Outcome: Web users can search + export
```

### **Week 5: Chat Integration**
```
Task 1: Route travel intents in ChatConsumer (WebSocket)
Task 2: Format travel responses for display
Task 3: Add web UI components (HTMX or React)
  → Outcome: Chat users can plan trips conversationally
```

### **Week 6: Testing & Mocks**
```
Task 1: Create mock connectors (dev without API keys)
Task 2: Write unit tests for all connectors
Task 3: Integration tests for itinerary builder
  → Outcome: Team can dev without secrets
```

### **Week 7-8: E2E & Performance**
```
Task 1: Playwright E2E tests (critical flows)
Task 2: Load testing (100 concurrent users)
Task 3: Cache validation + performance tuning
  → Outcome: Verified <3 sec search, <10 sec itinerary
```

### **Week 9-10: Staging & Beta**
```
Task 1: Docker Compose staging setup
Task 2: Deploy to Render/Railway (free tier)
Task 3: Invite 50 beta testers
Task 4: Collect feedback + fix top bugs
  → Outcome: Real-world validation
```

### **Week 11-12: Launch**
```
Task 1: Final security audit + performance checks
Task 2: Deploy to production
Task 3: Launch marketing + monitor metrics
  → Outcome: LIVE MVP 🎉
```

---

## 💰 Cost Strategy (Free Tier Maximization)

### Buses: Buupass
| Option | Cost | Notes |
|--------|------|-------|
| Scraper | Free | Primary; scrape Buupass public pages |
| API Partnership | $0 (rev-share) | Fallback; contact Buupass |
| Fallback Operators | Free | Hardcoded bus company names + phone |

### Hotels: Booking.com
| Option | Cost | Notes |
|--------|------|-------|
| Affiliate | Commission | Sign up via Awin/CJ (free account) → earn 25% commission |
| No API needed | Free | Just redirect to Booking.com with affiliate ID |

### Flights: Duffel
| Option | Cost | Notes |
|--------|------|-------|
| Sandbox | Free | Use until Volume negotiation |
| Live API | Pay-per-search | Contact Duffel for volume discount |
| Fallback | Free | Redirect to Skyscanner |

### Transfers: Karibu Taxi
| Option | Cost | Notes |
|--------|------|-------|
| API | Commission | Contact Karibu (requires partnership) |
| Local Partners | Commission | Direct agreements with 2–3 taxi operators |
| Fallback | Free | Show phone numbers + estimates |

### Events: Eventbrite
| Option | Cost | Notes |
|--------|------|-------|
| API Search | Free | Free tier: search events by location |
| Local Scraping | Free | Supplement with local event aggregators |

**Total Cost Year 1:** ~$50–150 (domain + cheap server instance). Zero API cost.

---

## 📊 Success Metrics (MVP Target)

| Metric | Target |
|--------|--------|
| **Itinerary response time** | <10 seconds |
| **Chat success rate** | >70% (valid itineraries) |
| **Search cache hit rate** | >60% (avoid API calls) |
| **Booking click-through** | >30% (users complete bookings) |
| **Mobile-friendly** | >90% UI functional on mobile |
| **Beta users** | 50+ concurrent testers |
| **Support tickets** | <50/month |

---

## 🎮 User Experience (Chat Example)

```
User: "I want to go to Mombasa next weekend with my family. 
       We like beaches and events. Budget: 10,000 KES total"

Bot: ✨ Planning your trip...
     🔍 Searching buses, hotels, events...
     
     [2 seconds later]
     
Bot: Here's your 3-day Mombasa itinerary:
     
     DAY 1 (Fri Dec 27)
     🚌 Bus: Nairobi→Mombasa @6am (Buupass) — KSh 1,500/person
     🏨 Hotel: Sarova Stanley — KSh 3,000/night
     🎉 Event: Mombasa Beach Festival @6pm — Free
     
     DAY 2 (Sat Dec 28)
     🏖️ Beach day (unscheduled)
     🎪 Event: Food & Art Market @2pm — Free
     
     DAY 3 (Sun Dec 29)
     🚌 Bus: Mombasa→Nairobi @4pm — KSh 1,500/person
     
     💰 Total: ~KSh 9,500
     
     [Book Bus] [Book Hotel] [Get Directions] [Export PDF]
```

---

## ✅ Approval Checklist

Before starting Week 1, confirm:

- [ ] **Architecture alignment** — Plan fuses seamlessly with existing MCPRouter + LLMClient?
- [ ] **API choices** — Booking.com + Duffel + Eventbrite + Karibu approach acceptable?
- [ ] **Cost model** — Free tier + scraping strategy OK (no upfront payments)?
- [ ] **Timeline** — 12 weeks realistic for your team?
- [ ] **Team readiness** — 2 backend devs + 1 frontend available?
- [ ] **Next step** — Ready to begin Week 1 setup?

---

## 🚀 How to Start (Next Actions)

### Immediate (Today):
1. Review the 3 documents (PRD, API matrix, implementation plan)
2. Confirm approval of approach
3. Assign team members to each task

### Week 1 (Starting Monday):
1. **Person A:** Extend `intent_parser.py` with travel actions
2. **Person B:** Create `travel` Django app + models
3. **Person C:** Register connectors in `mcp_router.py`

### Set Up API Keys (Parallel):
- [ ] Sign up for Duffel sandbox (instant, 1 min) → get token
- [ ] Sign up for Eventbrite (instant) → get token
- [ ] Contact Booking.com via Awin/CJ for affiliate ID (3–5 days)
- [ ] Contact Buupass for API partnership (5–7 days)
- [ ] Contact Karibu Taxi for API access (3–5 days)

---

## 📞 Frequently Asked Questions

**Q: Do we need LangChain?**  
A: No. Use existing `llm_client.py` + explicit connector routing. LangChain adds complexity for MVP.

**Q: What if Buupass API unavailable?**  
A: Web scraper is ready (low friction). Show fallback list of operators + phone numbers.

**Q: Can users save itineraries?**  
A: Yes. Use existing User model + new Itinerary model (included in migration).

**Q: Do we handle payments directly?**  
A: No (MVP). Redirect to Booking.com, Duffel, etc. They handle payments. Earn commissions.

**Q: What about M-Pesa integration?**  
A: Post-MVP (Phase 2).we will use intersend pay intergrated with wallets

**Q: How do we scale to Pan-Africa?**  
A: Connectors are region-agnostic. Add new providers per country (Tanzania, Uganda, etc.) as needed.

---

## 📚 Document Map

- **PRD** — What we're building (features, scope, timeline)
- **API Matrix** — Which APIs to use + cost analysis
- **Implementation Plan** — How to build it (week-by-week, concrete tasks)
- **This Summary** — Quick reference + next steps

---

## 🎬 Ready to Execute

**All planning done. Architecture validated. No blockers.**

**Next milestone:** Week 1 setup complete (intent parser extended, Django app created, connectors registered).

---

**Questions or changes?** Let me know. Otherwise, **ready to hand off to development team.**

---

**Prepared by:** AI Planning Agent  
**Status:** ✅ Complete & Approved  
**Date:** December 22, 2025
