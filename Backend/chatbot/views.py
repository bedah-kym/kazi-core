from django.shortcuts import render,redirect,get_object_or_404
from django.urls import reverse
from django.contrib.auth.decorators import login_required
from django.utils.safestring import mark_safe
from .models import Chatroom, Message, Member, RoomReadState, Reminder
import json 
from django.http import JsonResponse
from django.contrib.auth import get_user_model
from django.db import transaction
from django.core.cache import cache
from django.utils import timezone

from django.conf import settings
import os
from django.views.decorators.http import require_GET, require_POST
from .notification_utils import get_unread_room_count



def _ensure_default_room(user):
    """
    Create a default general room for the user if none exists.
    Adds Mathia when available and seeds a welcome message.
    """
    User = get_user_model()
    with transaction.atomic():
        existing = Chatroom.objects.filter(participants__User=user).first()
        if existing:
            return existing

        user_member, _ = Member.objects.get_or_create(User=user)
        new_room = Chatroom.objects.create()
        new_room.participants.add(user_member)

        try:
            mathia_user = User.objects.get(username='mathia')
            mathia_member, _ = Member.objects.get_or_create(User=mathia_user)
            new_room.participants.add(mathia_member)

            msg = Message.objects.create(
                member=mathia_member,
                content="Welcome to your new General room! I'm here to help.",
                timestamp=timezone.now()
            )
            new_room.chats.add(msg)
        except User.DoesNotExist:
            # Bot user not provisioned yet; room still created for the user
            pass

        cache.delete(f"user_rooms:{user.id}")
        return new_room


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
        request, "chatbot/chatbase.html",
        {
            "room_name": mark_safe(json.dumps(room_name)),
            "room_id": room.id,
            "username": mark_safe(json.dumps(request.user.username)),
            "chatrooms": chatrooms_data,  # Passing processed data
            "room_member": current_room_name,
        }
    )


import uuid
import logging
from django.http import Http404

logger = logging.getLogger(__name__)

# File upload security configuration
MAX_FILE_SIZE = 50 * 1024 * 1024  # 50MB
ALLOWED_FILE_EXTENSIONS = {'.pdf', '.doc', '.docx', '.txt', '.jpg', '.jpeg', '.png', '.gif', '.mp3', '.wav'}

@login_required
def upload_file(request):
    """
    Securely upload files with validation and safe naming.
    
    Security features:
    - File type whitelist validation
    - File size limit enforcement
    - Secure random filename generation (prevents path traversal)
    - Checks upload directory exists/is writable
    """
    if request.method != 'POST':
        return JsonResponse({'error': 'Invalid request method'}, status=405)
    
    try:
        # Get uploaded file
        uploaded_file = request.FILES.get('file')
        if not uploaded_file:
            return JsonResponse({'error': 'No file provided'}, status=400)
        
        # Validate file size
        if uploaded_file.size > MAX_FILE_SIZE:
            return JsonResponse(
                {'error': f'File too large. Maximum size is {MAX_FILE_SIZE / (1024*1024):.0f}MB'}, 
                status=413
            )
        
        # Validate file extension (whitelist approach)
        original_filename = uploaded_file.name
        ext = os.path.splitext(original_filename)[1].lower()
        if ext not in ALLOWED_FILE_EXTENSIONS:
            return JsonResponse(
                {'error': f'File type not allowed. Allowed types: {", ".join(ALLOWED_FILE_EXTENSIONS)}'}, 
                status=400
            )
        
        # Generate safe random filename to prevent path traversal attacks
        safe_filename = f"{uuid.uuid4()}{ext}"
        
        # Determine upload directory (user-specific or type-specific)
        upload_dir = os.path.join(settings.MEDIA_ROOT, 'documents')
        os.makedirs(upload_dir, exist_ok=True)
        
        # Construct safe file path
        file_path = os.path.join(upload_dir, safe_filename)
        
        # Verify path is within MEDIA_ROOT (security check)
        resolved_path = os.path.abspath(file_path)
        resolved_media_root = os.path.abspath(settings.MEDIA_ROOT)
        if not resolved_path.startswith(resolved_media_root):
            logger.error(f"Path traversal attempt detected: {file_path}")
            return JsonResponse({'error': 'Invalid file path'}, status=400)
        
        # Save file in chunks (safer for large files)
        with open(file_path, 'wb+') as destination:
            for chunk in uploaded_file.chunks():
                destination.write(chunk)
        
        # Log successful upload
        logger.info(f"File uploaded successfully: user={request.user.id}, size={uploaded_file.size}, type={ext}")
        
        # Construct file URL
        file_url = os.path.join(settings.MEDIA_URL, 'documents', safe_filename)
        
        return JsonResponse({'fileUrl': file_url}, status=200)
        
    except Exception as e:
        logger.error(f"File upload error: user={request.user.id}, error={str(e)}")
        return JsonResponse({'error': 'File upload failed'}, status=500)


def welcomepage(request):
    if not request.user.is_authenticated:
        return redirect("users:login")

    # Try to find the user's first room; if none, create a default one atomically.
    first_room = Chatroom.objects.filter(participants__User=request.user).first()
    if not first_room:
        first_room = _ensure_default_room(request.user)

    return redirect(reverse("chatbot:bot-home", kwargs={"room_name": first_room.id}))

@login_required
def create_room(request):
    """
    API/View to create a new room.
    Support POST for creation, GET for showing a form if needed (we'll use modal/API mainly).
    Types: 'general' (with Mathia), 'private' (just user for now)
    """
    room_type = request.POST.get('room_type') or request.GET.get('room_type') or 'general'
    
    User = get_user_model()
    user_member, _ = Member.objects.get_or_create(User=request.user)

    # If the user already has a room and we're not forcing a new one, reuse it to avoid duplicates.
    if request.method == 'GET':
        existing = Chatroom.objects.filter(participants__User=request.user).first()
        if existing:
            return redirect(reverse("chatbot:bot-home", kwargs={"room_name": existing.id}))

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
                msg = Message.objects.create(
                    member=mathia_member,
                    content="Welcome to your new General room! I'm here to help.",
                    timestamp=timezone.now()
                )
                new_room.chats.add(msg)
                
            except User.DoesNotExist:
                pass
    
    cache.delete(f"user_rooms:{request.user.id}")
    return redirect(reverse("chatbot:bot-home", kwargs={"room_name": new_room.id}))


from django.core.exceptions import ValidationError
from django.core.validators import validate_email

@login_required
def invite_user(request):
    """
    API View to invite a user to a room via email.
    
    Security features:
    - Email format validation
    - Room ownership/membership check
    - Prevents self-invitations
    - Prevents duplicate adds
    - Proper error messages
    """
    if request.method != 'POST':
        return JsonResponse({'status': 'error', 'message': 'Invalid request method'}, status=405)
    
    try:
        room_id = request.POST.get('room_id', '').strip()
        email = request.POST.get('email', '').strip().lower()
        
        # Input validation
        if not room_id or not email:
            return JsonResponse({'status': 'error', 'message': 'Missing room_id or email'}, status=400)
        
        # Validate room_id is an integer
        try:
            room_id = int(room_id)
        except (ValueError, TypeError):
            return JsonResponse({'status': 'error', 'message': 'Invalid room_id'}, status=400)
        
        # Validate email format
        try:
            validate_email(email)
        except ValidationError:
            return JsonResponse({'status': 'error', 'message': 'Invalid email format'}, status=400)
        
        # Security Check: Ensure requester is in the room
        room = get_object_or_404(Chatroom, id=room_id, participants__User=request.user)
        
        # Prevent inviting yourself
        if email == request.user.email:
            return JsonResponse({'status': 'info', 'message': 'You cannot invite yourself to a room'}, status=400)
        
        User = get_user_model()
        try:
            invited_user = User.objects.get(email=email)
            
            # Check if user is already in the room
            if room.participants.filter(User=invited_user).exists():
                return JsonResponse({'status': 'info', 'message': 'User is already in the room'})
            
            # Add user to room
            invited_member, _ = Member.objects.get_or_create(User=invited_user)
            room.participants.add(invited_member)
            
            logger.info(f"User {request.user.id} invited {invited_user.id} to room {room_id}")
            return JsonResponse({'status': 'success', 'message': f'Added {invited_user.username} to the room'})
            
        except User.DoesNotExist:
            return JsonResponse({'status': 'error', 'message': 'User with this email does not exist'}, status=404)
        
    except Http404:
        return JsonResponse({'status': 'error', 'message': 'Room not found or you do not have access'}, status=403)
    except Exception as e:
        logger.error(f"Error inviting user: {str(e)}")
        return JsonResponse({'status': 'error', 'message': 'An error occurred'}, status=500)


@login_required
@require_GET
def notification_status(request):
    exclude_room_id = request.GET.get('exclude_room_id')
    try:
        exclude_room_id = int(exclude_room_id) if exclude_room_id else None
    except (TypeError, ValueError):
        exclude_room_id = None

    unread_rooms = get_unread_room_count(request.user, exclude_room_id=exclude_room_id)
    pending_reminders = Reminder.objects.filter(user=request.user, status='pending').count()

    return JsonResponse({
        "unread_rooms": unread_rooms,
        "pending_reminders": pending_reminders,
    })


@login_required
@require_POST
def mark_room_read(request, room_id):
    room = get_object_or_404(Chatroom, id=room_id, participants__User=request.user)
    now = timezone.now()
    RoomReadState.objects.update_or_create(
        user=request.user,
        room=room,
        defaults={"last_read_at": now},
    )
    return JsonResponse({"status": "ok", "last_read_at": now.isoformat()})


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
