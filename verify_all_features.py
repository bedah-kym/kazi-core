
import os
import django
import asyncio
import json
from django.utils import timezone
from datetime import timedelta

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "Backend.settings")
django.setup()

from django.contrib.auth import get_user_model
from chatbot.models import Chatroom, RoomContext, RoomNote
from chatbot.context_manager import ContextManager
from orchestration.intent_parser import parse_intent
from orchestration.mcp_router import route_intent

User = get_user_model()

async def run_verification():
    print("🚀 Starting Logic Verification...\n")
    
    # Setup Data
    user, _ = await asyncio.to_thread(User.objects.get_or_create, username="verify_user", email="verify@test.com")
    room_a, _ = await asyncio.to_thread(Chatroom.objects.get_or_create, name="verify_room_a", topic="Project Alpha")
    room_b, _ = await asyncio.to_thread(Chatroom.objects.get_or_create, name="verify_room_b", topic="General Chat")
    
    # Add user to rooms
    await asyncio.to_thread(room_a.participants.get_or_create, User=user)
    await asyncio.to_thread(room_b.participants.get_or_create, User=user)

    # ==========================================
    # 1. Verify Cross-Room Context
    # ==========================================
    print("🧪 Testing Cross-Room Context...")
    
    # Add High Priority note in Room A
    await asyncio.to_thread(
        ContextManager.add_note,
        chatroom=room_a,
        note_type="decision",
        content="Project Alpha deadline is Friday.",
        created_by=user,
        priority="high"
    )
    print("   ✅ Added High Priority note to Room A")
    
    # Fetch Context for Room B (Should include note from Room A)
    context_data = await asyncio.to_thread(ContextManager.get_context_for_ai, room_b)
    
    global_notes = context_data.get('global_notes', [])
    found = any("Project Alpha deadline" in n['content'] for n in global_notes)
    
    if found:
        print("   ✅ SUCCESS: Room B context includes Room A's high priority note!")
    else:
        print("   ❌ FAILED: Room B did not see Room A's note.")
        print(f"   Debug Global Notes: {global_notes}")

    # ==========================================
    # 2. Verify Reminder Parsing & Routing
    # ==========================================
    print("\n🧪 Testing AI Reminders...")
    
    test_message = "remind me to call client in 10 minutes"
    print(f"   Input: '{test_message}'")
    
    # Mock context
    user_context = {"user_id": user.id, "room_id": room_b.id}
    
    # Parse
    intent = await parse_intent(test_message, user_context)
    print(f"   Parsed Intent: {intent}")
    
    if intent['action'] == 'set_reminder' and '10' in intent['parameters']['time']:
        print("   ✅ SUCCESS: Intent parsed correctly.")
    else:
        print("   ❌ FAILED: Intent parsing failed.")
        
    # Route
    result = await route_intent(intent, user_context)
    print(f"   Route Result: {result}")
    
    if result['status'] == 'success':
        print(f"   ✅ SUCCESS: Reminder created! ID: {result.get('reminder_id')}")
        print(f"   Message: {result.get('message')}")
    else:
        print("   ❌ FAILED: Router execution failed.")
        
    print("\n✨ Verification Complete.")

if __name__ == "__main__":
    asyncio.run(run_verification())
