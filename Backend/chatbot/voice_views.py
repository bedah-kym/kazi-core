import os
import logging
from django.conf import settings
from django.http import Http404
from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework import status
from .history import DECRYPT_FAILED, decrypt_stored
from .models import VOICE_MESSAGE_PLACEHOLDER, VOICE_STATUS_MARKER, Chatroom, Message, Member
from .tasks import transcribe_voice_note

logger = logging.getLogger(__name__)


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def upload_voice_note(request, room_id):
    """
    Endpoint for uploading a recorded voice note.
    Saves the file and triggers transcription.
    """
    try:
        chatroom = get_object_or_404(Chatroom, id=room_id)

        # Security: check if user is in room
        if not Chatroom.objects.filter(id=room_id, participants__User=request.user).exists():
            return Response({"error": "Access denied"}, status=status.HTTP_403_FORBIDDEN)

        if 'audio' not in request.FILES:
            return Response({"error": "No audio file provided"}, status=status.HTTP_400_BAD_REQUEST)

        audio_file = request.FILES['audio']

        # Create directory if missing (path uses the DB-resolved integer id)
        voice_dir = os.path.join(settings.MEDIA_ROOT, 'voice_notes', str(chatroom.id))
        os.makedirs(voice_dir, exist_ok=True)

        # Save file (server-generated name; no user-controlled path components)
        file_name = f"voice_{request.user.id}_{timezone.now().strftime('%Y%m%d_%H%M%S')}.webm"
        file_path = os.path.join(voice_dir, file_name)

        with open(file_path, 'wb+') as destination:
            for chunk in audio_file.chunks():
                destination.write(chunk)

        # Construct relative URL for the audio
        audio_url = os.path.join('voice_notes', str(chatroom.id), file_name)

        # Create a pending voice message
        member, _ = Member.objects.get_or_create(User=request.user)
        message = Message.objects.create(
            member=member,
            content=VOICE_MESSAGE_PLACEHOLDER,
            timestamp=timezone.now(),
            is_voice=True,
            audio_url=audio_url,
            voice_transcript=VOICE_STATUS_MARKER
        )
        chatroom.chats.add(message)

        # Trigger transcription task
        transcribe_voice_note.delay(message.id)

        return Response({
            "success": True,
            "message_id": message.id,
            "audio_url": audio_url,
            "status": VOICE_STATUS_MARKER
        }, status=status.HTTP_201_CREATED)

    except Exception as e:
        logger.error(f"Error uploading voice note: {e}")
        return Response({"error": "An unexpected error occurred. Please try again."}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def get_voice_status(request, message_id):
    """
    Check transcription status of a voice message.
    """
    room = Chatroom.objects.filter(
        participants__User=request.user,
        chats__id=message_id,
    ).first()
    if room is None:
        raise Http404

    message = get_object_or_404(Message, id=message_id)
    transcript = ""
    if message.is_voice:
        content, _payload = decrypt_stored(room, message)
        if content and content != DECRYPT_FAILED and content != VOICE_MESSAGE_PLACEHOLDER:
            transcript = content
        else:
            transcript = VOICE_STATUS_MARKER

    return Response({
        "message_id": message.id,
        "is_voice": message.is_voice,
        "transcript": transcript,
        "audio_url": message.audio_url
    })
