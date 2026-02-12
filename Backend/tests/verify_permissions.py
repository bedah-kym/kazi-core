
import os
import django
import sys

# Setup Django environment
# Add the directory containing 'Backend' (package) to sys.path
sys.path.append(r'c:\Users\user\Desktop\Dev2\MATHIA-PROJECT\Backend')
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'Backend.settings')
os.environ['DJANGO_SECRET_KEY'] = 'django-insecure-test-key-12345'
django.setup()

from django.contrib.auth import get_user_model
from chatbot.models import Chatroom, Member

def verify_permissions():
    User = get_user_model()
    
    # Create a test user and room
    username = "test_perm_user_v2"
    email = "test_perm_v2@example.com"
    password = "password123"
    
    user, created = User.objects.get_or_create(username=username, email=email)
    if created:
        user.set_password(password)
        user.save()
        print(f"Created user {username}")
    else:
        print(f"Using existing user {username}")

    # Create Member
    member, _ = Member.objects.get_or_create(User=user)
    
    # Create Room
    room = Chatroom.objects.create()
    room.participants.add(member)
    print(f"Created room {room.id} and added member")

    # Verify Permission Logic
    # Code in views: chatroom.participants.filter(User=request.user).exists()
    
    has_perm = room.participants.filter(User=user).exists()
    print(f"Permission Check Result: {has_perm}")
    
    if has_perm:
        print("PASS: Logic works as expected.")
    else:
        print("FAIL: Permission check failed despite membership.")

    # Cleanup
    room.delete()
    # Don't delete user/member to avoid breaking other things if re-run

if __name__ == '__main__':
    try:
        verify_permissions()
    except Exception as e:
        print(f"Error: {e}")
        import traceback
        traceback.print_exc()
