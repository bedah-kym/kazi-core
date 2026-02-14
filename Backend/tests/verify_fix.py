import os
import django
from django.conf import settings

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'Backend.settings')
django.setup()

from django.contrib.auth import get_user_model
from chatbot.models import Chatroom, Member

User = get_user_model()
username = 'test_user_fix_verify'
email = 'test_verify@example.com'
password = 'Password123!'

# Clean up if exists
try:
    u = User.objects.get(username=username)
    u.delete()
    print("Cleaned up existing test user.")
except User.DoesNotExist:
    pass

print(f"Creating user {username}...")
user = User.objects.create_user(username=username, email=email, password=password)

print("Checking for room creation...")
member = Member.objects.filter(User=user).first()
if not member:
    print("FAIL: Member not created for user.")
else:
    print(f"SUCCESS: Member found: {member}")
    rooms = Chatroom.objects.filter(participants=member)
    if rooms.exists():
        print(f"SUCCESS: Room created! Count: {rooms.count()}")
        print(f"Room ID: {rooms.first().id}")
        participants = rooms.first().participants.all()
        print(f"Participants: {[p.User.username for p in participants]}")
    else:
        print("FAIL: No room created for user.")

# Check for warnings/deprecation settings
import warnings
print("Checking settings...")
# Basic check if it loaded without error
print("Settings loaded successfully.")
