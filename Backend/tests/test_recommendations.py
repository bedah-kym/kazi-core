import asyncio
import logging
import os
import django

# Setup Django standalone
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'Backend.settings')
django.setup()

from travel.recommendation_service import RecommendationService

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

MOCK_CONTEXT = [
    {'title': 'Beach Resort Stay', 'time': '2025-12-25 14:00:00'},
    {'title': 'Snorkeling Tour', 'time': '2025-12-26 09:00:00'}
]

async def test_recommendations():
    service = RecommendationService()
    
    # Check keys
    if not service.llm_client.anthropic_key and not service.llm_client.hf_key:
        logger.warning("⚠️ No LLM Keys found. Recommendations might be empty.")

    logger.info("1. Testing Activity Recommendations (Context Aware)...")
    activities = await service.recommend_activities("Mombasa", MOCK_CONTEXT, interests=["History", "Food"])
    logger.info(f"Got {len(activities)} activities")
    for act in activities:
        logger.info(f" - {act.get('title')}: {act.get('description')}")

    logger.info("\n2. Testing Dining Recommendations...")
    dining = await service.recommend_dining("Mombasa", cuisine_pref="Swahili")
    logger.info(f"Got {len(dining)} restaurants")
    for rest in dining:
        logger.info(f" - {rest.get('name')} ({rest.get('price_range')}): {rest.get('description')}")

    logger.info("\n3. Testing Hidden Gems...")
    gems = await service.get_hidden_gems("Mombasa")
    logger.info(f"Got {len(gems)} hidden gems")
    for gem in gems:
        logger.info(f" - {gem.get('title')}: {gem.get('description')}")

if __name__ == "__main__":
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    loop.run_until_complete(test_recommendations())
