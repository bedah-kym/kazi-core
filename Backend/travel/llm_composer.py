import logging
import json
from typing import Dict, List, Any, Optional
from orchestration.llm_client import get_llm_client, extract_json

logger = logging.getLogger(__name__)

class LLMComposer:
    """
    Intelligent Itinerary Composer using LLM.
    Selects the best combination of travel options based on constraints and logic.
    """

    def __init__(self):
        self.llm_client = get_llm_client()

    async def compose_itinerary(self, 
                              trip_details: Dict[str, Any], 
                              search_results: Dict[str, List[Dict]]) -> List[Dict]:
        """
        Compose an itinerary by selecting the best items from search results.
        
        Args:
            trip_details: {
                'origin': str,
                'destination': str,
                'start_date': str,
                'end_date': str,
                'budget_level': str (optional),
                'interests': List[str] (optional)
            }
            search_results: Dictionary of lists (buses, hotels, flights, etc.)
            
        Returns:
            List of selected items with AI reasoning.
        """
        # 1. Minify data to fit context window
        minified_results = self._minify_results(search_results)
        
        # 2. Construct Prompt
        system_prompt = self._get_system_prompt()
        user_prompt = self._get_user_prompt(trip_details, minified_results)
        
        try:
            # 3. Call LLM
            response_text = await self.llm_client.generate_text(
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                temperature=0.4, # Lower temperature for logic
                json_mode=True
            )
            
            # 4. Parse Response
            selection_plan = extract_json(response_text)
            
            if not selection_plan or not isinstance(selection_plan, dict):
                logger.warning("LLM returned invalid JSON. Falling back to Top-N strategy.")
                return []

            # 5. Rehydrate items (match IDs back to full objects)
            final_items = self._rehydrate_items(selection_plan.get('selected_items', []), search_results)
            
            return final_items

        except Exception as e:
            logger.error(f"Error in LLM composition: {e}")
            return []

    def _get_system_prompt(self) -> str:
        return """You are an expert travel agent. Your goal is to create the *perfect* itinerary from a list of options.
        
Rules:
1. Logic: Ensure transport arrival times work with check-in times.
2. Value: Balance price and quality.
3. Cohesion: Pick activities that fit the user's trip duration.
4. Output: Return ONLY valid JSON.
"""

    def _get_user_prompt(self, trip: Dict, options: Dict) -> str:
        return f"""Plan a trip from {trip['origin']} to {trip['destination']} 
Dates: {trip['start_date']} to {trip['end_date']}

Available Options (JSON):
{json.dumps(options, indent=2)}

Select the best combination:
1. One transport option (Bus or Flight)
2. One accommodation option
3. At least 2 activities/events (if available)

JSON Response Format:
{{
  "selected_items": [
    {{
      "type": "bus|flight|hotel|event",
      "id": "unique_id_from_options", 
      "reason": "Why you chose this (short sentence)"
    }}
  ]
}}
"""

    def _minify_results(self, results: Dict[str, List[Dict]]) -> Dict[str, List[Dict]]:
        """
        Strip non-essential fields to save tokens.
        Adds a temporary 'id' if missing for matching back later.
        """
        minified = {}
        
        for category, items in results.items():
            minified[category] = []
            for idx, item in enumerate(items):
                # Ensure item has an ID for matching
                if 'id' not in item:
                    item['id'] = f"{category}_{idx}"
                
                # Extract only key info
                mini_item = {
                    'id': item['id'],
                    'price': item.get('price_ksh', 0),
                    'rating': item.get('rating', 'N/A'),
                    'name': item.get('company') or item.get('name') or item.get('airline') or item.get('title', 'Unknown'),
                }
                
                # Add time info if available
                if 'departure_time' in item: mini_item['time'] = f"{item['departure_time']} - {item.get('arrival_time')}"
                if 'start_time' in item: mini_item['time'] = item['start_time']
                
                minified[category].append(mini_item)
                
        return minified

    def _rehydrate_items(self, selected_minifield_items: List[Dict], original_results: Dict) -> List[Dict]:
        """
        Match the LLM's selected IDs back to the full original objects.
        Adds the 'ai_reasoning' field to the final object.
        """
        final_list = []
        
        # Create a lookup map
        lookup_map = {}
        for category, items in original_results.items():
            for item in items:
                if 'id' in item:
                    lookup_map[item['id']] = item

        for selected in selected_minifield_items:
            item_id = selected.get('id')
            reason = selected.get('reason', 'Best option available')
            
            if item_id in lookup_map:
                full_item = lookup_map[item_id].copy()
                full_item['ai_reasoning'] = reason
                # normalize item_type for the itinerary builder
                full_item['_category'] = selected.get('type') 
                final_list.append(full_item)
            else:
                logger.warning(f"LLM selected ID {item_id} which was not found in original results.")
        
        return final_list
