import logging
import json
from typing import Dict, List, Any, Optional
from orchestration.llm_client import get_llm_client, extract_json

logger = logging.getLogger(__name__)

class RecommendationService:
    """
    AI-powered engine to provide context-aware travel recommendations.
    Goes beyond generic lists by analyzing the current itinerary context.
    """

    def __init__(self):
        self.llm_client = get_llm_client()

    async def recommend_activities(self, 
                                 destination: str, 
                                 current_itinerary_items: List[Dict],
                                 interests: List[str] = None) -> List[Dict]:
        """
        Suggest complementary activities based on what's already booked.
        """
        context_summary = self._summarize_context(current_itinerary_items)
        
        prompt = f"""
        Suggest 5 unique activities for a trip to {destination}.
        
        Current Itinerary Context:
        {context_summary}
        
        User Interests: {', '.join(interests) if interests else 'General sightseeing'}
        
        Goal: Suggest activities that COMPLEMENT the existing schedule (e.g. if they have a morning tour, suggest an evening activity).
        Avoid duplicating existing items.
        
        Return JSON array:
        [
            {{
                "title": "Activity Name",
                "description": "Why it fits...",
                "price_estimate_ksh": 1000,
                "best_time_of_day": "Evening"
            }}
        ]
        """
        
        return await self._get_llm_suggestions(prompt)

    async def recommend_dining(self, destination: str, cuisine_pref: str = None) -> List[Dict]:
        """
        Suggest dining options.
        """
        prompt = f"""
        Suggest 5 top-rated dining spots in {destination}.
        {f"Preference: {cuisine_pref}" if cuisine_pref else "Mix of local and international cuisine."}
        
        Return JSON array:
        [
            {{
                "name": "Restaurant Name",
                "cuisine": "Swahili/Italian/etc",
                "price_range": "$$ - Moderate",
                "description": "Short vibe description"
            }}
        ]
        """
        return await self._get_llm_suggestions(prompt)
    
    async def get_hidden_gems(self, destination: str) -> List[Dict]:
        """
        Suggest non-touristy, 'hidden gem' locations.
        """
        prompt = f"""
        Identify 3 'hidden gem' locations in {destination} that most tourists miss.
        Focus on authentic local experiences, quiet spots, or unique cultural sites.
        
        Return JSON array:
        [
            {{
                "title": "Hidden Spot Name",
                "description": "What makes it special",
                "location_hint": "Near Old Town..."
            }}
        ]
        """
        return await self._get_llm_suggestions(prompt)

    async def _get_llm_suggestions(self, prompt: str) -> List[Dict]:
        """Helper to call LLM and parse list response"""
        try:
            response = await self.llm_client.generate_text(
                system_prompt="You are a knowledgeable local guide.",
                user_prompt=prompt,
                temperature=0.7,
                json_mode=True
            )
            data = extract_json(response)
            if isinstance(data, list): return data
            if isinstance(data, dict) and 'items' in data: return data['items'] # Handle wrapped responses
            return []
        except Exception as e:
            logger.error(f"Error getting recommendations: {e}")
            return []

    async def verify_activity_online(self, activity_name: str, user_id: int) -> Dict:
        """
        Verify an activity using online search, subject to rate limits.
        """
        from orchestration.mcp_router import route_intent
        
        logger.info(f"Verifying activity online: {activity_name}")
        
        intent = {
            "action": "search_info",
            "parameters": {"query": f"latest reviews and opening hours for {activity_name} in Kenya"}
        }
        context = {"user_id": user_id}
        
        result = await route_intent(intent, context)
        
        if result.get('status') == 'success':
            data = result.get('data', {})
            if data.get('error') == 'rate_limit_exceeded':
                 return {"verified": False, "reason": "Daily search limit reached."}
            
            return {
                "verified": True,
                "summary": data.get('summary'),
                "source": data.get('source')
            }
        else:
            return {"verified": False, "reason": "Search service unavailable."}

    def _summarize_context(self, items: List[Dict]) -> str:
        """Minimize context tokens"""
        if not items: return "Nothing booked yet."
        summary = []
        for item in items:
            title = item.get('title', 'Unknown')
            time = item.get('time', 'Anytime')
            summary.append(f"- {title} ({time})")
        return "\n".join(summary)
