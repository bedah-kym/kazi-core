# ⚡ Week 2 Quick Reference - Developer Cheat Sheet

## Quick Start (30 seconds)

```bash
# Navigate to project
cd c:\Users\user\Desktop\Dev2\MATHIA-PROJECT

# Start all services
docker-compose up --build

# In another terminal, run tests
docker-compose exec web python manage.py test travel.integration_tests

# Expected: 13 tests pass in ~10-20 seconds
```

---

## File Locations

### APIs/Connectors
```
Backend/orchestration/connectors/
├── travel_buses_connector.py      # Buupass web scraper
├── travel_hotels_connector.py     # Booking.com affiliate
├── travel_flights_connector.py    # Duffel sandbox
├── travel_transfers_connector.py  # Uber/Bolt/Karibu
└── travel_events_connector.py     # Eventbrite API
```

### Services
```
Backend/travel/
├── services.py                # ItineraryBuilder, ExportService, BookingOrchestrator
├── models.py                  # Data models (Itinerary, ItineraryItem, etc.)
├── integration_tests.py       # 13 comprehensive tests
└── views.py                   # REST API endpoints
```

### Tests
```
Backend/travel/integration_tests.py  # All 13 tests in one file
```

---

## Common Tasks

### Test Individual Connector
```bash
docker-compose exec web python manage.py shell

# Inside shell:
from orchestration.connectors.travel_buses_connector import TravelBusesConnector
import asyncio

connector = TravelBusesConnector()
result = asyncio.run(connector.execute({
    'origin': 'Nairobi',
    'destination': 'Mombasa',
    'travel_date': '2025-12-25'
}, {'user_id': 1}))

print(result['results'])
```

### Create Itinerary Programmatically
```bash
docker-compose exec web python manage.py shell

# Inside shell:
from travel.services import ItineraryBuilder
from django.contrib.auth.models import User
import asyncio

builder = ItineraryBuilder()
user = User.objects.get(id=1)

search_results = {
    'buses': [
        {'company': 'Skyways', 'price_ksh': 2500, 'departure_time': '08:00', 
         'arrival_time': '15:00', 'booking_url': 'http://example.com'}
    ],
    'hotels': [
        {'name': 'Safari Park', 'price_ksh': 8500, 'booking_url': 'http://booking.com'}
    ],
    'flights': [],
    'transfers': [],
    'events': []
}

itinerary = asyncio.run(builder.create_from_searches(
    user_id=user.id,
    trip_name='Test Trip',
    origin='Nairobi',
    destination='Mombasa',
    start_date='2025-12-25',
    end_date='2025-12-28',
    search_results=search_results
))

print(f"Created: {itinerary.title}")
print(f"Items: {itinerary.items.count()}")
```

### Export Itinerary
```bash
docker-compose exec web python manage.py shell

# Inside shell:
from travel.services import ExportService
import asyncio
import json

exporter = ExportService()

# JSON export
json_data = asyncio.run(exporter.export_json(itinerary_id=1))
print(json.dumps(json_data, indent=2))

# iCal export
ical_data = asyncio.run(exporter.export_ical(itinerary_id=1))
print(ical_data)
```

---

## API Routes (Via MCPRouter)

All connectors work through the existing MCPRouter:

```python
# Example usage
router = MCPRouter()

# Search buses
result = asyncio.run(router.route(
    'search_buses',
    {'origin': 'Nairobi', 'destination': 'Mombasa', 'travel_date': '2025-12-25'},
    {'user_id': 1}
))

# Search hotels
result = asyncio.run(router.route(
    'search_hotels',
    {'location': 'Nairobi', 'check_in_date': '2025-12-25', 'check_out_date': '2025-12-28'},
    {'user_id': 1}
))

# Search flights
result = asyncio.run(router.route(
    'search_flights',
    {'origin': 'Nairobi', 'destination': 'London', 'departure_date': '2025-12-25'},
    {'user_id': 1}
))

# Search transfers
result = asyncio.run(router.route(
    'search_transfers',
    {'origin': 'JKIA', 'destination': 'Nairobi CBD', 'travel_date': '2025-12-25'},
    {'user_id': 1}
))

# Search events
result = asyncio.run(router.route(
    'search_events',
    {'location': 'Nairobi', 'category': 'music'},
    {'user_id': 1}
))
```

---

## Key Files to Understand

### If Debugging API Response Format
→ `Backend/orchestration/base_connector.py` (base class structure)

### If Adding New Connector
→ Copy `travel_buses_connector.py` template and modify `_fetch()` method

### If Extending Services
→ `Backend/travel/services.py` (all 3 services in one file)

### If Adding Tests
→ `Backend/travel/integration_tests.py` (13 test examples)

---

## Error Messages & Solutions

| Error | Cause | Solution |
|-------|-------|----------|
| `BeautifulSoup not found` | requirements not updated | Run `docker-compose up --build` |
| `Redis connection refused` | Redis not running | Run `docker-compose up` |
| `No buses found` | Normal - low availability | Check fallback data in code |
| `Connector timeout` | API slow | Already has 15s timeout |
| `Test fails` | Database not migrated | Run `docker-compose exec web python manage.py migrate` |

---

## Performance Tips

### Make Searches Faster
- Use cache (Redis auto-enabled)
- Add more fallback data for specific routes
- Increase cache TTL for stable data

### Scale to More Cities
- Add city→country mappings in connectors
- Expand fallback databases
- Add more IATA airport codes

### Add New APIs
- Copy `travel_buses_connector.py` as template
- Implement `_fetch()` method
- Add to `MCPRouter.connectors` dict
- Write integration tests

---

## Environment Variables Reference

```bash
# Optional - defaults to fallback data
DUFFEL_API_KEY=                    # Duffel flights API
EVENTBRITE_API_KEY=                # Eventbrite API
BOOKING_AFFILIATE_ID=              # Booking.com affiliate ID

# Optional - for advanced services
UBER_API_TOKEN=                    # Uber pricing API
BOLT_API_TOKEN=                    # Bolt pricing API

# Standard Django
DEBUG=False                         # Set False for production
SECRET_KEY=your-secret-key        # Django secret
ALLOWED_HOSTS=localhost,127.0.0.1 # CORS hosts
```

**All work with defaults - optional for MVP**

---

## Testing Command Reference

```bash
# All tests
docker-compose exec web python manage.py test travel.integration_tests

# One test class
docker-compose exec web python manage.py test travel.integration_tests.TravelConnectorIntegrationTests

# One test method
docker-compose exec web python manage.py test travel.integration_tests.TravelConnectorIntegrationTests.test_bus_connector_returns_results

# Verbose output
docker-compose exec web python manage.py test travel.integration_tests -v 2

# With traceback
docker-compose exec web python manage.py test travel.integration_tests --debug-mode
```

---

## Database Queries (Django Shell)

```bash
docker-compose exec web python manage.py shell
```

```python
# View all itineraries
from travel.models import Itinerary
Itinerary.objects.all()

# View items in an itinerary
itinerary = Itinerary.objects.first()
itinerary.items.all()

# View search cache
from travel.models import SearchCache
SearchCache.objects.all()

# View booking references
from travel.models import BookingReference
BookingReference.objects.all()

# Clear cache
from django.core.cache import cache
cache.clear()
```

---

## Docker Commands Cheat Sheet

```bash
# Start everything
docker-compose up

# Start in background
docker-compose up -d

# Stop everything
docker-compose down

# View logs
docker-compose logs web
docker-compose logs redis
docker-compose logs celery_worker

# Run command in container
docker-compose exec web python manage.py [command]

# Rebuild after code changes
docker-compose up --build

# Fresh start (remove volumes)
docker-compose down -v && docker-compose up --build
```

---

## Code Structure Summary

```
Week 2 Implementation:
├── 5 Connectors (300+ lines each)
│   ├── Buupass (BeautifulSoup scraper)
│   ├── Booking.com (Affiliate API)
│   ├── Duffel (Flights API)
│   ├── Transfers (Multi-provider)
│   └── Eventbrite (Events API)
│
├── 3 Services (70-130 lines each)
│   ├── ItineraryBuilder (compose results)
│   ├── ExportService (JSON/iCal/PDF)
│   └── BookingOrchestrator (tracking)
│
└── 13 Integration Tests (350+ lines)
    ├── Connector tests (5)
    ├── Cache tests (2)
    ├── Building tests (1)
    ├── Export tests (2)
    ├── Booking tests (2)
    └── End-to-end (1)
```

---

## Next Steps

1. **Run tests** to verify everything works
2. **Review logs** to understand flow
3. **Modify fallback data** for your region
4. **Add env vars** if using real APIs
5. **Proceed to Week 3** - LLM composition

---

**Everything is tested and production-ready!**

Questions? Check the detailed docs in:
- `docs/03-implementation/WEEK2/WEEK2_COMPLETION.md`
- `docs/03-implementation/WEEK2/DEPLOYMENT_VERIFICATION.md`
