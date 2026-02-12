import asyncio
import logging
import os
import django
from django.conf import settings

# Setup Django standalone
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'Backend.settings')
django.setup()

from travel.safety_service import SafetyScorer
from travel.feedback_service import FeedbackCollector

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

async def test_safety_scorer():
    logger.info("--- Testing SafetyScorer ---")
    scorer = SafetyScorer()
    
    # 1. Test High Risk (Static)
    res = await scorer.assess_location("Somalia Border")
    logger.info(f"Location: {res['location']}, Risk: {res['risk_level']}, Warn: {res['warnings']}")
    if res['risk_level'] != 'High': logger.error("Expected High risk for border")

    # 2. Test Low Risk + LLM Advisory
    res = await scorer.assess_location("Diani Beach")
    logger.info(f"Location: {res['location']}, Risk: {res['risk_level']}, Warn: {res['warnings']}")
    
    # 3. Test Logistics
    items = [
        {'title': 'Late Bus', 'time': '23:30'},
        {'title': 'Morning Tour', 'time': '09:00'}
    ]
    alerts = await scorer.assess_itinerary_logistics(items)
    logger.info(f"Logistics Alerts: {alerts}")
    if not alerts: logger.warning("Expected alert for late bus")

async def test_feedback_collector():
    logger.info("\n--- Testing FeedbackCollector ---")
    collector = FeedbackCollector()
    
    # 1. Generate Questions
    q = await collector.generate_post_trip_questions("Mombasa Getaway", [{'title': 'Snorkeling'}, {'title': 'Fort Jesus'}])
    logger.info(f"Generated Question: {q}")
    
    # 2. Process User Reply
    user_reply = "The snorkeling was amazing but the hotel was too expensive. Felt minimal safety issues."
    data = await collector.process_user_reply(user_reply)
    logger.info(f"Extracted Data: {data}")
    
    if data.get('cost_rating') and data.get('cost_rating') <= 3:
         logger.info("✅ Correctly identified cost issue")
    else:
         logger.warning("❌ Failed to identify cost issue")

if __name__ == "__main__":
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    loop.run_until_complete(test_safety_scorer())
    loop.run_until_complete(test_feedback_collector())
