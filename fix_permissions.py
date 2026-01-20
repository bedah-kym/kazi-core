
import os
import django
import sys

# Setup Django environment
sys.path.append(r'c:\Users\user\Desktop\Dev2\MATHIA-PROJECT\Backend')
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'Backend.settings')
os.environ['DJANGO_SECRET_KEY'] = 'fix-script-key'
os.environ['DJANGO_CSRF_TRUSTED_ORIGINS'] = 'http://localhost'
django.setup()

from django.contrib.auth import get_user_model
from chatbot.models import Chatroom, Member

def diagnose():
    User = get_user_model()
    print(f"Total Users: {User.objects.count()}")
    for u in User.objects.all():
        member_count = Member.objects.filter(User=u).count()
        print(f"User: {u.username} (ID: {u.id}) - Members linked: {member_count}")
        if member_count > 1:
            print(f"  [WARNING] Duplicate Members found for {u.username}!")

    print(f"\nTotal Rooms: {Chatroom.objects.count()}")
    for room in Chatroom.objects.all():
        participants = room.participants.all()
        print(f"Room {room.id}: {participants.count()} participants")
        for p in participants:
            print(f"  - Member ID: {p.id}, User: {p.User.username}")
            
        if participants.count() == 0:
             print("  [ALERT] Room has NO participants.")
    
    print("\nDiagnosis Complete.")

if __name__ == '__main__':
    diagnose()
