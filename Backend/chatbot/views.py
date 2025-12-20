from django.shortcuts import render,redirect,get_object_or_404
from django.urls import reverse
from django.contrib.auth.decorators import login_required
from django.utils.safestring import mark_safe
from .models import Chatroom, Message, Member
import json 
from django.http import JsonResponse
from django.contrib.auth import get_user_model
from django.db import transaction

from django.conf import settings
import os



@login_required
def home(request, room_name):
    # Security Fix: Only show rooms where the user is a participant
    # OPTIMIZATION: Use prefetch_related to fetch participants and their users in one go
    chatrooms = Chatroom.objects.filter(
        participants__User=request.user
    ).prefetch_related('participants', 'participants__User')
    
    # Pre-process chatrooms to get the correct display name for the sidebar
    chatrooms_data = []
    for room in chatrooms:
        # Get all members of the room
        members = list(room.participants.all())
        
        # Determine the display name
        display_name = "Unknown Room"
        avatar_url = "https://bootdey.com/img/Content/avatar/avatar1.png" # Default
        
        # Check if it's a "General" room with Mathia
        mathia_member = next((m for m in members if m.User.username == 'mathia'), None)
        other_members = [m for m in members if m.User != request.user]
        
        if mathia_member and len(members) <= 2: 
            # It's likely a 1-on-1 with Mathia (General Room)
            display_name = "General (AI)"
            avatar_url = "https://bootdey.com/img/Content/avatar/avatar8.png" # Robot-ish avatar if available
        elif len(other_members) == 0:
             display_name = "Private Room (You)"
        elif len(other_members) == 1:
            display_name = other_members[0].User.username
        else:
            # Group chat - list first few names
            display_name = ", ".join([m.User.username for m in other_members[:2]])
            if len(other_members) > 2:
                display_name += f" +{len(other_members)-2}"
        
        chatrooms_data.append({
            'id': room.id,
            'name': display_name,
            'avatar': avatar_url
        })
    
    # Security Fix: Ensure user can only access a room they belong to
    room = get_object_or_404(Chatroom, id=room_name, participants__User=request.user)
    
    room_members = room.participants.all()
    # each room has two members the user and the other user we need to get the name of the other user to display
    other_member = room_members.exclude(User=request.user).first()
    current_room_name = other_member.User.username if other_member else "Unknown User"
    
    # Override current room name if it's Mathia
    if other_member and other_member.User.username == 'mathia':
        current_room_name = "General (AI)"

    return render(
        request,"chatbot/chatbase.html",
        {
        "room_name":mark_safe(json.dumps(room_name)),
        "username":mark_safe(json.dumps(request.user.username)),
        "chatrooms":chatrooms_data, # Passing processed data
        "room_member":current_room_name, 
        }
    )


def upload_file(request):
    if request.method == 'POST':
        try:
            # Retrieve the uploaded file from the request
            uploaded_file = request.FILES.get('file')

            if not uploaded_file:
                return JsonResponse({'error': 'No file provided'}, status=400)

            # Save the file to the uploads directory
            file_path = os.path.join(settings.MEDIA_ROOT, uploaded_file.name)
            with open(file_path, 'wb+') as destination:
                for chunk in uploaded_file.chunks():
                    destination.write(chunk)

            # Construct the file URL
            file_url = os.path.join(settings.MEDIA_URL, uploaded_file.name)

            # Return the file URL in the JSON response
            return JsonResponse({'fileUrl': file_url}, status=200)
        except Exception as e:
            return JsonResponse({'error': str(e)}, status=500)
    else:
        return JsonResponse({'error': 'Invalid request method'}, status=405)


def welcomepage(request):
    if request.user.is_authenticated:
        # Find the first room the user is in
        ignored_room_ids = [1, 2, 3, 4] # Legacy rooms to potentially ignore if needed, but safe to remove if data is clean
        
        # Try to find user's general room (with Mathia) or just first available
        first_room = Chatroom.objects.filter(participants__User=request.user).first()
        
        if first_room:
            return redirect(reverse("chatbot:bot-home", kwargs={"room_name": first_room.id}))
            
        # If no room exists, create one (fallback)
        return redirect('chatbot:create_room') 
        
    return redirect("users:login")

@login_required
def create_room(request):
    """
    API/View to create a new room.
    Support POST for creation, GET for showing a form if needed (we'll use modal/API mainly).
    Types: 'general' (with Mathia), 'private' (just user for now)
    """
    if request.method == 'POST':
        room_type = request.POST.get('room_type', 'general')
        
        User = get_user_model()
        user_member, _ = Member.objects.get_or_create(User=request.user)
        
        with transaction.atomic():
            new_room = Chatroom.objects.create()
            new_room.participants.add(user_member)
            
            if room_type == 'general':
                # Add Mathia
                try:
                    mathia_user = User.objects.get(username='mathia')
                    mathia_member, _ = Member.objects.get_or_create(User=mathia_user)
                    new_room.participants.add(mathia_member)
                    
                    # Welcome message
                    import django.utils.timezone
                    msg = Message.objects.create(
                        member=mathia_member,
                        content="Welcome to your new General room! I'm here to help.",
                        timestamp=django.utils.timezone.now()
                    )
                    new_room.chats.add(msg)
                    
                except User.DoesNotExist:
                    pass
            
            # For 'private', we just leave it with user (or add invited users later)
            
            return redirect(reverse("chatbot:bot-home", kwargs={"room_name": new_room.id}))
            
    # If GET, maybe redirect to home or show a simple error/form
    return redirect('chatbot:welcomepage')



@login_required
def invite_user(request):
    """
    API View to invite a user to a room via email.
    """
    if request.method == 'POST':
        room_id = request.POST.get('room_id')
        email = request.POST.get('email')
        
        if not room_id or not email:
             return JsonResponse({'status': 'error', 'message': 'Missing room_id or email'}, status=400)

        # Security Check: Ensure requester is in the room
        room = get_object_or_404(Chatroom, id=room_id, participants__User=request.user)
        
        User = get_user_model()
        try:
            invited_user = User.objects.get(email=email)
            invited_member, _ = Member.objects.get_or_create(User=invited_user)
            
            if room.participants.filter(User=invited_user).exists():
                return JsonResponse({'status': 'info', 'message': 'User is already in the room'})
            
            room.participants.add(invited_member)
            return JsonResponse({'status': 'success', 'message': f'Added {invited_user.username} to the room'})
            
        except User.DoesNotExist:
            return JsonResponse({'status': 'error', 'message': 'User with this email does not exist'}, status=404)
        except Exception as e:
             return JsonResponse({'status': 'error', 'message': str(e)}, status=500)

    return JsonResponse({'status': 'error', 'message': 'Invalid request method'}, status=405)


def get_last_10messages(chatid):
    chatroom = get_object_or_404(Chatroom,id=chatid)
    return chatroom.chats.order_by('-timestamp').all()


def get_current_chatroom(chatid):
    chatroom = get_object_or_404(Chatroom,id=chatid)
    return chatroom

def get_chatroom_participants(chatroom):
    return chatroom.participants.all()


"""def get_mathia_reply():#should return the dict message like in chatsocket
    
    content = MathiaReply.objects.last()
    message = content.message
    sender = content.sender
    command = content.command
    chatid = content.chatid
    return {
                'message': message,
                'from': sender,
                'command':command,
                "chatid": chatid
    }"""

def get_mathia_reply():
    """ this is a feature to connect a bot for a specific room using botlibre api so
    users can talk to this bot.
    """
    #should return the dict message like in chatsocket
    import requests
    text=Message.objects.last()
    text= text.content
    url= 'https://www.botlibre.com/rest/json/chat'
    r = requests.post(url,json=(
        {"application":"2127403001275571408", "instance":"165","message":text}
        ))
    reply=r.json()
    message = reply['message']
    sender = 'mathia'
    command = "new_message"
    chatid = 3
    return {
                'message': message,
                'from': sender,
                'command':command,
                "chatid": chatid
    }