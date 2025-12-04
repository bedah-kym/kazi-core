import os
import django
import asyncio
import sys
from unittest.mock import MagicMock, patch
from datetime import datetime



# Setup Django environment
# Setup Django environment
from pathlib import Path
BASE_DIR = Path(__file__).resolve().parent
sys.path.append(str(BASE_DIR))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'Backend.settings')
django.setup()

from django.contrib.auth import get_user_model
from orchestration.mcp_router import CalendarConnector
from users.models import CalendlyProfile

async def verify_calendly():
    print("Starting Calendly Connector Verification...")
    
    User = get_user_model()
    
    # 1. Create Mock User and Profile
    username = "test_calendly_user"
    try:
        user = await sync_to_async(User.objects.get)(username=username)
        print(f"Found existing user: {username}")
    except User.DoesNotExist:
        user = await sync_to_async(User.objects.create_user)(username=username, password="password123")
        print(f"Created user: {username}")

    # Ensure profile exists
    profile, created = await sync_to_async(CalendlyProfile.objects.get_or_create)(user=user)
    
    # Mock connection data
    profile.is_connected = True
    profile.calendly_user_uri = "https://api.calendly.com/users/mock_user_uri"
    profile.booking_link = "https://calendly.com/test_user"
    
    # Encrypt a fake token
    # We need to use the connect method or manually set encrypted fields if we want to test decryption,
    # but for this test we can mock the get_access_token method or just set it if we want to test the connector logic.
    # However, the connector calls profile.get_access_token().
    # Let's mock the profile object methods for the connector execution context if possible, 
    # or just mock the requests.get call which is what we really want to test (the connector logic).
    
    # Actually, let's mock the profile's get_access_token to return "mock_token"
    # We can't easily mock the method on the instance retrieved inside the connector without patching the model class
    # or patching the DB retrieval.
    
    # Easier approach: Patch `users.models.CalendlyProfile.get_access_token`
    
    connector = CalendarConnector()
    context = {"user_id": user.id}
    
    # Test 1: Check Availability (Mocked API)
    print("\nTest 1: Check Availability")
    with patch('users.models.CalendlyProfile.get_access_token', return_value="mock_access_token"):
        with patch('requests.get') as mock_get:
            # Mock successful response
            mock_response = MagicMock()
            mock_response.status_code = 200
            mock_response.json.return_value = {
                "collection": [
                    {
                        "name": "Meeting with Client",
                        "start_time": "2025-11-22T10:00:00Z",
                        "uri": "https://api.calendly.com/events/1"
                    },
                    {
                        "name": "Team Sync",
                        "start_time": "2025-11-22T14:00:00Z",
                        "uri": "https://api.calendly.com/events/2"
                    }
                ]
            }
            mock_get.return_value = mock_response
            
            result = await connector.execute({"action": "check_availability"}, context)
            
            if result["status"] == "success" and len(result["events"]) == 2:
                print("SUCCESS: Retrieved and formatted events correctly.")
                print(f"Events: {result['events']}")
            else:
                print(f"FAILURE: {result}")

    # Test 2: Schedule Meeting (Get Booking Link)
    print("\nTest 2: Schedule Meeting (Get Booking Link)")
    # We need to save the profile with booking link first
    profile.booking_link = "https://calendly.com/test_user"
    await sync_to_async(profile.save)()
    
    # For this test, we want to get the link for a target user. 
    # Let's use the same user as target for simplicity.
    
    result = await connector.execute({"action": "schedule_meeting", "target_user": f"@{username}"}, context)
    
    if result["status"] == "success" and result["booking_link"] == "https://calendly.com/test_user":
        print("SUCCESS: Retrieved booking link correctly.")
        print(f"Link: {result['booking_link']}")
    else:
        print(f"FAILURE: {result}")

    # Test 3: Schedule Meeting (Self - No target)
    print("\nTest 3: Schedule Meeting (Self)")
    result = await connector.execute({"action": "schedule_meeting"}, context)
    
    if result["status"] == "success" and result["booking_link"] == "https://calendly.com/test_user":
        print("SUCCESS: Retrieved self booking link correctly.")
        print(f"Link: {result['booking_link']}")
    else:
        print(f"FAILURE: {result}")

    # Cleanup
    # await sync_to_async(user.delete)() 
    # Keep user for debugging if needed, or delete. Let's keep it.

from asgiref.sync import sync_to_async

if __name__ == "__main__":
    asyncio.run(verify_calendly())
