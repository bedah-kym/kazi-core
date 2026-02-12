import logging
import json
from typing import Dict, List, Any
from orchestration.llm_client import get_llm_client, extract_json

logger = logging.getLogger(__name__)

class FeedbackCollector:
    """
    Manages the post-trip feedback loop.
    Generates conversational questions and parses answers into structured data.
    """

    def __init__(self):
        self.llm_client = get_llm_client()

    async def generate_post_trip_questions(self, itinerary_title: str, items: List[Dict]) -> str:
        """
        Generate a friendly message asking about specific parts of the trip.
        """
        # Create a summary of key activities to ask about
        highlights = [item.get('title', 'trip') for item in items[:3]] # Ask about top 3 items
        
        prompt = f"""
        The user just finished a trip to '{itinerary_title}'.
        Key activities were: {', '.join(highlights)}.
        
        Generate a friendly, conversational message asking how it went.
        Ask specifically about:
        1. Safety (did they feel safe?)
        2. Cost (was it expensive?)
        3. Any hidden gems they found?
        
        Keep it short (under 50 words). Sound like a friend, not a survey.
        """
        
        try:
            return await self.llm_client.generate_text(system_prompt="You are a friendly travel buddy.", user_prompt=prompt, temperature=0.7)
        except Exception as e:
            logger.error(f"Error generating feedback questions: {e}")
            return "Welcome back! I'd love to hear how your trip went. Any highlights or tips for other travelers?"

    async def process_user_reply(self, user_text: str) -> Dict:
        """
        Extract structured data from the user's free-text review.
        """
        prompt = f"""
        Analyze this travel review and extract structured data:
        Review: "{user_text}"
        
        Return JSON:
        {{
            "safety_rating": 1-5 (5=Very Safe, 1=Unsafe),
            "cost_rating": 1-5 (5=Great Value, 1=Expensive),
            "overall_rating": 1-5,
            "tags": ["list", "of", "short", "tags", "e.g. solo-friendly", "good-food"],
            "sentiment": "Positive|Neutral|Negative"
        }}
        If info is missing, infer from tone or use null/3.
        """
        
        try:
            response = await self.llm_client.generate_text(system_prompt="You are a data extraction bot.", user_prompt=prompt, temperature=0.0, json_mode=True)
            return extract_json(response)
        except Exception as e:
            logger.error(f"Error parsing feedback: {e}")
            return {}
