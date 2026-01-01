import logging
import csv
import os
from typing import Dict, List, Optional
from django.conf import settings
from orchestration.mcp_router import route_intent

logger = logging.getLogger(__name__)

class VisaService:
    """
    Checks visa requirements using a local CSV dataset (Passport Index).
    Data source: ilyankou/passport-index-dataset
    """
    
    def __init__(self):
        self.dataset_path = os.path.join(settings.BASE_DIR, 'travel', 'passport-index-tidy.csv')
        self.data = {} # {(passport_code, destination_code): requirement_code}
        self.loaded = False

    def _load_data(self):
        if self.loaded: return
        
        try:
            with open(self.dataset_path, 'r', encoding='utf-8') as f:
                reader = csv.DictReader(f)
                for row in reader:
                    # passport, destination, requirement
                    # requirement: -1 (V.O.A), 0 (Visa Required), other (Visa Free days)
                    p_code = row.get('Passport')
                    d_code = row.get('Destination')
                    req = row.get('Requirement')
                    
                    if p_code and d_code:
                        self.data[(p_code, d_code)] = req
            self.loaded = True
        except Exception as e:
            logger.error(f"Failed to load visa dataset: {e}")

    def check_requirements(self, passport_country_code: str, destination_code: str) -> str:
        """
        Check visa requirement.
        Codes: ISO-2 (e.g., 'US', 'KE', 'GB')
        """
        self._load_data()
        
        key = (passport_country_code.upper(), destination_code.upper())
        req = self.data.get(key)
        
        if req is None:
            return "Unknown (Check Embassy)"
            
        if req == '-1':
            return "Visa on Arrival"
        elif req == '0':
             return "Visa Required"
        elif req.isdigit() and int(req) > 0:
             return f"Visa Free ({req} days)"
        elif "eta" in req.lower():
             return "eTA / E-Visa"
        else:
             return f"See Details ({req})"

class WeatherService:
    """
    Fetches weather forecasts for itinerary dates using OpenWeatherMap.
    """
    
    async def get_trip_forecast(self, destination: str) -> Dict:
        """
        Get current weather/forecast for the destination.
        """
        intent = {
            "action": "get_weather",
            "parameters": {"city": destination}
        }
        # Call existing connector via router
        result = await route_intent(intent, {"user_id": 0}) # system call
        
        if result.get('status') == 'success':
            return result.get('data', {})
        return {"error": "Weather unavailable"}
