import os

file_path = r'Backend\orchestration\mcp_router.py'

# The new execute method with token refresh logic
new_execute_method = """    async def execute(self, parameters: Dict, context: Dict) -> Dict:
        \"\"\"
        Execute Calendly actions:
        - check_availability: Get scheduled events
        - schedule_meeting: Get booking link
        \"\"\"
        from users.models import CalendlyProfile
        from django.contrib.auth import get_user_model
        import requests
        
        User = get_user_model()
        user_id = context.get("user_id")
        action = parameters.get("action", "check_availability") # Default action
        
        target_user_name = parameters.get("target_user")
        
        try:
            # Get current user
            try:
                user = await sync_to_async(User.objects.get)(pk=user_id)
            except User.DoesNotExist:
                return {"status": "error", "message": "User not found"}

            # Get profile
            try:
                profile = await sync_to_async(lambda: getattr(user, 'calendly', None))()
            except Exception:
                profile = None

            if not profile or not await sync_to_async(lambda: profile.is_connected)():
                return {
                    "status": "error", 
                    "message": "You are not connected to Calendly. Please connect first.",
                    "action_required": "connect_calendly"
                }

            # Handle "schedule_meeting"
            if action == "schedule_meeting":
                if target_user_name:
                    # Schedule with another user
                    target_username = target_user_name.lstrip('@')
                    try:
                        target_user = await sync_to_async(User.objects.get)(username=target_username)
                        target_profile = await sync_to_async(lambda: getattr(target_user, 'calendly', None))()
                        
                        if not target_profile or not await sync_to_async(lambda: target_profile.is_connected)():
                            return {
                                "status": "error",
                                "message": f"User @{target_username} has not connected their Calendly yet."
                            }
                        
                        booking_link = await sync_to_async(lambda: target_profile.booking_link)()
                        return {
                            "status": "success",
                            "type": "booking_link",
                            "booking_link": booking_link,
                            "message": f"Here is the booking link for @{target_username}"
                        }
                    except User.DoesNotExist:
                        return {
                            "status": "error", 
                            "message": f"User @{target_username} not found."
                        }
                else:
                    # Return own booking link
                    booking_link = await sync_to_async(lambda: profile.booking_link)()
                    if not booking_link:
                         return {
                            "status": "error",
                            "message": "You don't have a booking link configured. Please check your Calendly settings."
                        }
                    return {
                        "status": "success",
                        "type": "booking_link",
                        "booking_link": booking_link,
                        "message": "Here is your booking link."
                    }

            # Handle "check availability" / "list meetings"
            access_token = await sync_to_async(profile.get_access_token)()
            if not access_token:
                 return {
                    "status": "error", 
                    "message": "Could not retrieve access token. Please reconnect Calendly.",
                    "action_required": "connect_calendly"
                }
                
            # Fetch events
            headers = {'Authorization': f'Bearer {access_token}'}
            user_uri = await sync_to_async(lambda: profile.calendly_user_uri)()
            
            # Run request in thread to avoid blocking
            def fetch_events(token=None):
                req_headers = headers
                if token:
                    req_headers = {'Authorization': f'Bearer {token}'}
                    
                return requests.get(
                    'https://api.calendly.com/scheduled_events', 
                    headers=req_headers, 
                    params={'user': user_uri, 'status': 'active', 'sort': 'start_time:asc'}
                )
            
            response = await sync_to_async(fetch_events)()
            
            # Handle 401 - Token Expired
            if response.status_code == 401:
                logger.info("Calendly token expired. Attempting refresh...")
                new_token = await self._refresh_token(profile)
                
                if new_token:
                    # Retry with new token
                    response = await sync_to_async(fetch_events)(new_token)
                else:
                     return {
                        "status": "error", 
                        "message": "Calendly authorization failed. Please reconnect.",
                        "action_required": "connect_calendly"
                    }
            
            if response.status_code != 200:
                logger.error(f"Calendly API error: {response.text}")
                return {
                    "status": "error",
                    "message": "Failed to fetch Calendly events."
                }
                
            data = response.json()
            events = data.get('collection', [])
            
            # Format events
            formatted_events = []
            for event in events[:5]: # Top 5
                start_time = event.get('start_time')
                name = event.get('name')
                formatted_events.append({
                    "start": start_time,
                    "title": name,
                    "url": event.get('uri')
                })
                
            return {
                "status": "success",
                "type": "events",
                "events": formatted_events,
                "message": f"You have {len(formatted_events)} upcoming meetings." if formatted_events else "You have no upcoming meetings scheduled."
            }

        except Exception as e:
            logger.error(f"CalendarConnector error: {e}")
            return {
                "status": "error",
                "message": f"An error occurred: {str(e)}"
            }

    async def _refresh_token(self, profile):
        \"\"\"Refresh the Calendly access token\"\"\"
        from django.conf import settings
        import requests
        
        refresh_token = await sync_to_async(profile.get_refresh_token)()
        if not refresh_token:
            logger.error("No refresh token available")
            return None
            
        try:
            def do_refresh():
                return requests.post(
                    'https://auth.calendly.com/oauth/token',
                    data={
                        'grant_type': 'refresh_token',
                        'refresh_token': refresh_token,
                        'client_id': settings.CALENDLY_CLIENT_ID,
                        'client_secret': settings.CALENDLY_CLIENT_SECRET
                    }
                )
            
            response = await sync_to_async(do_refresh)()
            
            if response.status_code == 200:
                data = response.json()
                new_access = data.get('access_token')
                new_refresh = data.get('refresh_token')
                
                # Update profile
                def update_profile():
                    # We need to re-encrypt and save
                    from cryptography.fernet import Fernet
                    import base64, hashlib
                    
                    secret = (settings.SECRET_KEY or 'changeme').encode('utf-8')
                    hash = hashlib.sha256(secret).digest()
                    fernet_key = base64.urlsafe_b64encode(hash)
                    f = Fernet(fernet_key)
                    
                    profile.encrypted_access_token = f.encrypt(new_access.encode('utf-8')).decode('utf-8')
                    if new_refresh:
                        profile.encrypted_refresh_token = f.encrypt(new_refresh.encode('utf-8')).decode('utf-8')
                    profile.save()
                    return new_access
                
                return await sync_to_async(update_profile)()
            else:
                logger.error(f"Token refresh failed: {response.text}")
                return None
                
        except Exception as e:
            logger.error(f"Error refreshing token: {e}")
            return None
"""

with open(file_path, 'r', encoding='utf-8') as f:
    content = f.read()

# Find the CalendarConnector class
class_start = content.find("class CalendarConnector(BaseConnector):")
if class_start == -1:
    print("Could not find CalendarConnector class")
    exit(1)

# Find the execute method inside it
execute_start = content.find("    async def execute(self, parameters: Dict, context: Dict) -> Dict:", class_start)
if execute_start == -1:
    print("Could not find execute method")
    exit(1)

# Find the end of the class (start of StripeConnector)
next_class = content.find("class StripeConnector(BaseConnector):")
if next_class == -1:
    print("Could not find StripeConnector class")
    exit(1)

# Replace the content between execute_start and next_class with new method
# We need to be careful to preserve the StripeConnector class definition
# The new_execute_method includes indentation

# Check if there are blank lines before StripeConnector
end_pos = next_class
while content[end_pos-1].isspace():
    end_pos -= 1

new_content = content[:execute_start] + new_execute_method + "\n\n\n" + content[next_class:]

with open(file_path, 'w', encoding='utf-8') as f:
    f.write(new_content)

print("Successfully patched mcp_router.py with token refresh logic")
