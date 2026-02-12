import asyncio
import logging
import os
import django
from django.conf import settings

# Setup Django standalone
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'Backend.settings')
django.setup()

from travel.practical_service import VisaService, WeatherService

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

async def test_practical_info():
    logger.info("--- Testing VisaService ---")
    visa = VisaService()
    
    # Check if dataset exists
    if not os.path.exists(visa.dataset_path):
        logger.error(f"❌ Dataset not found at: {visa.dataset_path}")
        return

    # Test Cases (adjust based on known data in CSV)
    # Kenya (KE) is destination
    
    # 1. Afghanistan (AF) -> Usually Visa Required (0)
    res = visa.check_requirements('AF', 'KE')
    logger.info(f"Passport AF -> KE: {res}")
    
    # 2. Uganda (UG) -> Usually Visa Free (East Africa)
    # Note: Dataset might encode this as -1 or specific days. 
    res = visa.check_requirements('UG', 'KE')
    logger.info(f"Passport UG -> KE: {res}")
    
    # 3. UK (GB) -> Usually E-Visa or VOA?
    res = visa.check_requirements('GB', 'KE')
    logger.info(f"Passport GB -> KE: {res}")

    logger.info("\n--- Testing WeatherService ---")
    weather = WeatherService()
    
    # Mock OpenWeatherMap call (function uses route_intent -> system fallback/API)
    # Since we can't easily guarantee API key here, we check for graceful response.
    res = await weather.get_trip_forecast("Mombasa")
    logger.info(f"Weather Result: {res}")

if __name__ == "__main__":
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    loop.run_until_complete(test_practical_info())
