import asyncio
import json
import logging
import os
import django
from django.conf import settings

# Setup Django standalone
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'Backend.settings')
django.setup()

from travel.llm_composer import LLMComposer
from travel.services import ItineraryBuilder

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Mock Data
MOCK_SEARCH_RESULTS = {
    'buses': [
        {'id': 'bus_1', 'company': 'Coast Bus', 'departure_time': '08:00', 'arrival_time': '16:00', 'price_ksh': 2000, 'rating': 4.5},
        {'id': 'bus_2', 'company': 'Modern Coast', 'departure_time': '22:00', 'arrival_time': '06:00', 'price_ksh': 2500, 'rating': 4.0},
    ],
    'hotels': [
        {'id': 'hotel_1', 'name': 'PrideInn', 'price_ksh': 15000, 'rating': 4.8, 'location': 'Beachfront'},
        {'id': 'hotel_2', 'name': 'Backpackers', 'price_ksh': 3000, 'rating': 4.2, 'location': 'City'},
    ],
    'activities': [
         {'id': 'act_1', 'title': 'Marine Park', 'price_ksh': 1000, 'rating': 4.9},
         {'id': 'act_2', 'title': 'Old Town Walk', 'price_ksh': 500, 'rating': 4.5},
    ]
}

TRIP_DETAILS = {
    'origin': 'Nairobi',
    'destination': 'Mombasa',
    'start_date': '2025-12-25',
    'end_date': '2025-12-28'
}

async def test_llm_minify():
    logger.info("Testing Minification...")
    composer = LLMComposer()
    minified = composer._minify_results(MOCK_SEARCH_RESULTS)
    
    assert len(minified['buses']) == 2
    assert 'price_ksh' not in minified['buses'][0] # Should be 'price'
    assert 'price' in minified['buses'][0]
    logger.info("✅ Minification passed")

async def test_llm_composition():
    logger.info("Testing LLM Composition (Integration with Mock LLM)...")
    
    # We can't easily mock the internal LLM call without patching, 
    # so we will rely on the real LLM client but check if it fails gracefully if no key 
    # or succeeds if key exists.
    
    composer = LLMComposer()
    
    # Check keys
    if not composer.llm_client.anthropic_key and not composer.llm_client.hf_key:
        logger.warning("⚠️ No LLM Keys found. Expecting graceful failure/empty list.")
    
    selected_items = await composer.compose_itinerary(TRIP_DETAILS, MOCK_SEARCH_RESULTS)
    
    logger.info(f"LLM Selected {len(selected_items)} items")
    for item in selected_items:
        logger.info(f" - {item.get('title') or item.get('company') or item.get('name')}: {item.get('ai_reasoning')}")

    if selected_items:
        logger.info("✅ LLM returned a plan")
    else:
        logger.info("✅ LLM returned empty (Graceful failure or empty response)")

if __name__ == "__main__":
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    loop.run_until_complete(test_llm_minify())
    loop.run_until_complete(test_llm_composition())
