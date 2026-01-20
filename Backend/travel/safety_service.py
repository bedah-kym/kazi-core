import logging
import json
from typing import Dict, List, Any, Optional
from orchestration.llm_client import get_llm_client, extract_json

logger = logging.getLogger(__name__)

class SafetyScorer:
    """
    Assesses travel risk using a hybrid approach:
    1. Static Rules: Hardcoded high-risk zones.
    2. LLM Analysis: Logic checks for itinerary risks (e.g., arrival times).
    """

    def __init__(self):
        self.llm_client = get_llm_client()
        
        # 1. Static Knowledge Base (Mock/Rule-based)
        # In a real app, this would query an external API like Sitata or State Dept.
        self.risk_zones = {
            "border": "High",
            "slum": "High",
            "downtown_night": "Medium"
        }

    async def assess_location(self, location_name: str, country: str = "Kenya") -> Dict:
        """
        Get risk level and advisories for a specific location.
        """
        # Static Check
        risk_level = "Low"
        warnings = []
        
        loc_lower = location_name.lower()
        if "border" in loc_lower or "somalia" in loc_lower:
             risk_level = "High"
             warnings.append("Proximity to volatile border area.")
        elif "slum" in loc_lower or "kibera" in loc_lower:
             risk_level = "High"
             warnings.append("High crime rate area. Avoid reputable guides.")

        # LLM Context Check (if static didn't flag as High)
        if risk_level != "High":
            try:
                system_prompt = "You are a travel safety expert."
                user_prompt = f"""
                Assess the safety of visiting '{location_name}' in {country} for a tourist.
                Return JSON:
                {{
                    "risk_level": "Low|Medium|High",
                    "advisory": "Short safety tip (max 10 words)"
                }}
                """
                response = await self.llm_client.generate_text(system_prompt, user_prompt, temperature=0.3, json_mode=True)
                data = extract_json(response)
                
                if data:
                    risk_level = data.get("risk_level", risk_level)
                    if data.get("advisory"):
                        warnings.append(data.get("advisory"))
            except Exception as e:
                logger.warning(f"Safety LLM check failed: {e}")

        return {
            "location": location_name,
            "risk_level": risk_level,
            "warnings": warnings
        }

    async def assess_itinerary_logistics(self, items: List[Dict]) -> List[str]:
        """
        Scan itinerary items for logistical risks (late arrivals, tight connections).
        """
        alerts = []
        
        # Simple heuristic checks (can be expanded)
        for item in items:
            title = item.get('title', '').lower()
            time_str = item.get('time', '')
            
            # Late arrival check
            # Check for strings indicating late night hours
            time_lower = time_str.lower()
            if any(t in time_lower for t in ['23:', '00:', '01:', '02:', '03:', '04:', '11 pm', '12 am', '1 am', '2 am', '3 am', '4 am']) and \
               ('bus' in title or 'flight' in title):
                   alerts.append(f"Late arrival detected for {title} ({time_str}). Ensure safe transfer is booked.")

        return alerts
