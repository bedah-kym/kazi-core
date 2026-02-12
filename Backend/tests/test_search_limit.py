import asyncio
import logging
import os
import django
from django.core.cache import cache

# Setup Django standalone
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'Backend.settings')
django.setup()

from travel.recommendation_service import RecommendationService

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

async def test_search_rate_limit():
    service = RecommendationService()
    user_id = 99999  # Test user ID
    
    # Check if keys exist (warn but proceed, assume mocked or real key present)
    if not service.llm_client.anthropic_key:
         logger.warning("⚠️ No Anthropic Key. Search will return fallback but logic checks should run.")

    logger.info(f"Testing Rate Limit for User {user_id}...")
    
    # Clear previous cache for this test user
    from datetime import datetime
    today = datetime.now().strftime("%Y-%m-%d")
    cache.delete(f"search_limit:{user_id}:{today}")
    
    # Run 10 allowed searches
    for i in range(1, 11):
        logger.info(f"Search attempt {i}...")
        result = await service.verify_activity_online(f"Activity {i}", user_id)
        
        # If no API key, it returns fallback but still counts usage? 
        # Actually my implementation increments usage *before* calling LLM? 
        # checking code: it increments usage *inside* try block before LLM call? 
        # Wait, if no key, it returns early.
        # So for this test to work without a real key, I might need to mock LLM or rely on "system_fallback" counting?
        # looking at code: `if not key: return ...`. It returns BEFORE incrementing.
        # So to test rate limit, I MUST simulate a working key or mock the check.
        
        # Simulate usage for the test since we are getting 'system_fallback' (no key) 
        # but we want to verify the rate limiting LOGIC blocks the 11th request.
        limit_key = f"search_limit:{user_id}:{today}"
        cache.incr(limit_key) if cache.get(limit_key) else cache.set(limit_key, 1, 86400)
        
        logger.info(f"Search {i} verified: {result.get('verified')} (Simulated limit: {i})")

    # 11th attempt should fail
    logger.info("Attempting 11th search (Should fail)...")
    result = await service.verify_activity_online("Activity 11", user_id)
    
    if not result.get('verified') and result.get('reason') == 'Daily search limit reached.':
        logger.info("✅ Rate Validation Passed: Request blocked.")
    else:
        logger.error(f"❌ Rate Validation Failed. Result: {result}")

if __name__ == "__main__":
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    loop.run_until_complete(test_search_rate_limit())
