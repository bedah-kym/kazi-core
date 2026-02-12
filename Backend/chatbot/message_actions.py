"""
Message Actions API Views
Endpoints for pinning messages, replying, retrying,and document uploads
"""
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework import status
from django.shortcuts import get_object_or_404
from django.utils import timezone
from datetime import timedelta
from django.core.files.storage import default_storage
from django.core.files.base import ContentFile
from chatbot.models import Chatroom, Message, RoomContext, RoomNote, DocumentUpload
from chatbot.context_manager import ContextManager


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def pin_message_to_notes(request, room_id, message_id):
    """
    POST /api/rooms/<room_id>/messages/<message_id>/pin/
    
    Pin a message to room notes for AI context
    """
    try:
        chatroom = get_object_or_404(Chatroom, id=room_id)
        
        # Verify user has access to this room
        if not Chatroom.objects.filter(id=room_id, participants__User=request.user).exists():
            return Response(
                {"error": "You don't have access to this room"},
                status=status.HTTP_403_FORBIDDEN
            )
        
        # Get the message
        message = get_object_or_404(Message, id=message_id)
        
        # Get decrypted content from request body (frontend sends decrypted content)
        message_content = request.data.get('message_content', '')
        
        if not message_content:
            return Response(
                {"error": "Message content is required"},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        # Get or create room context
        room_context, _ = RoomContext.objects.get_or_create(chatroom=chatroom)
        
        # Check if already pinned
        existing_note = RoomNote.objects.filter(
            room_context=room_context,
            source_message_id=message_id
        ).first()
        
        if existing_note:
            return Response(
                {"error": "Message already pinned"},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        # Create note with decrypted content
        note = RoomNote.objects.create(
            room_context=room_context,
            note_type='reference',
            content=f"Pinned message: {message_content[:100]}...",
            source_message_id=message_id,
            source_message_content=message_content,  # Store decrypted content
            created_by=request.user,
            is_ai_generated=False,
            priority='medium'
        )
        
        return Response({
            "success": True,
            "note_id": note.id,
            "message": "Message pinned to notes successfully"
        }, status=status.HTTP_201_CREATED)
        
    except Exception as e:
        return Response(
            {"error": str(e)},
            status=status.HTTP_500_INTERNAL_SERVER_ERROR
        )


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def reply_to_message(request, room_id, message_id):
    """
    POST /api/rooms/<room_id>/messages/<message_id>/reply/
    
    Create a reply referencing an older message
    Returns the referenced message content to include in new message
    """
    try:
        chatroom = get_object_or_404(Chatroom, id=room_id)
        
        # Verify user has access
        if not Chatroom.objects.filter(id=room_id, participants__User=request.user).exists():
            return Response(
                {"error": "You don't have access to this room"},
                status=status.HTTP_403_FORBIDDEN
            )
        
        # Get decrypted content from request body (frontend sends decrypted content)
        message_content = request.data.get('message_content', '')
        
        if not message_content:
            return Response(
                {"error": "Message content is required"},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        # Get the message being replied to
        original_message = get_object_or_404(Message, id=message_id)
        member_name = original_message.member.User.username if original_message.member else "Unknown"
        
        # Return the decrypted message content for frontend to include in reply
        return Response({
            "success": True,
            "reply_prefix": f"Replying to {member_name}: \"{message_content[:50]}...\"\n\n"
        }, status=status.HTTP_200_OK)
        
    except Exception as e:
        return Response(
            {"error": str(e)},
            status=status.HTTP_500_INTERNAL_SERVER_ERROR
        )


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def retry_ai_message(request, room_id, message_id):
    """
    POST /api/rooms/<room_id>/messages/<message_id>/retry/
    
    Retry a failed AI message
    This should trigger WebSocket to regenerate response
    """
    try:
        chatroom = get_object_or_404(Chatroom, id=room_id)
        
        # Verify user has access
        if not Chatroom.objects.filter(id=room_id, participants__User=request.user).exists():
            return Response(
                {"error": "You don't have access to this room"},
                status=status.HTTP_403_FORBIDDEN
            )
        
        # Note: Actual retry logic should be handled via WebSocket in consumers.py
        # This endpoint just validates and triggers the retry
        
        return Response({
            "success": True,
            "message": "Retry request received. AI will regenerate response.",
            "room_id": room_id,
            "message_id": message_id
        }, status=status.HTTP_200_OK)
        
    except Exception as e:
        return Response(
            {"error": str(e)},
            status=status.HTTP_500_INTERNAL_SERVER_ERROR
        )


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def upload_document_to_ai(request, room_id):
    """
    POST /api/rooms/<room_id>/documents/upload/
    
    Upload document (PDF or image) for AI processing
    Implements 10-hour quota system
    """
    try:
        chatroom = get_object_or_404(Chatroom, id=room_id)
        
        # Verify user has access
        if not Chatroom.objects.filter(id=room_id, participants__User=request.user).exists():
            return Response(
                {"error": "You don't have access to this room"},
                status=status.HTTP_403_FORBIDDEN
            )
        
        # Get user's workspace plan
        try:
            plan = request.user.workspace.plan
        except AttributeError:
            plan = 'free'
        
        #Define quotas
        quotas = {
            'free': 10,
            'pro': 100,
            'agency': 10000
        }
        quota_limit = quotas.get(plan, 10)
        
        # Check quota (10-hour window)
        ten_hours_ago = timezone.now() - timedelta(hours=10)
        recent_uploads = DocumentUpload.objects.filter(
            user=request.user,
            uploaded_at__gte=ten_hours_ago
        ).count()
        
        if recent_uploads >= quota_limit:
            # Calculate when quota resets
            first_upload = DocumentUpload.objects.filter(
                user=request.user,
                uploaded_at__gte=ten_hours_ago
            ).order_by('uploaded_at').first()
            
            if first_upload:
                reset_time = first_upload.uploaded_at + timedelta(hours=10)
                hours_until_reset = (reset_time - timezone.now()).total_seconds() / 3600
                
                return Response({
                    "error": "Upload limit reached",
                    "quota_limit": quota_limit,
                    "resets_in_hours": round(hours_until_reset, 1)
                }, status=status.HTTP_429_TOO_MANY_REQUESTS)
        
        # Get uploaded file
        if 'file' not in request.FILES:
            return Response(
                {"error": "No file provided"},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        uploaded_file = request.FILES['file']
        
        # Validate file type
        file_name = uploaded_file.name.lower()
        if file_name.endswith(('.pdf',)):
            file_type = 'pdf'
        elif file_name.endswith(('.jpg', '.jpeg', '.png', '.gif', '.webp')):
            file_type = 'image'
        else:
            return Response(
                {"error": "Only PDFs and images are supported"},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        # Validate file size
        max_sizes = {
            'pdf': 10 * 1024 * 1024,  # 10MB
            'image': 5 * 1024 * 1024  # 5MB
        }
        
        if uploaded_file.size > max_sizes[file_type]:
            max_mb = max_sizes[file_type] / (1024 * 1024)
            return Response(
                {"error": f"{file_type.upper()} files must be under {max_mb}MB"},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        # Save file correctly using Django's storage system
        file_path = f"documents/{room_id}/{uploaded_file.name}"
        stored_path = default_storage.save(file_path, ContentFile(uploaded_file.read()))
        
        # Create upload record with pending status
        doc = DocumentUpload.objects.create(
            user=request.user,
            chatroom=chatroom,
            file_type=file_type,
            file_path=stored_path,
            file_size=uploaded_file.size,
            status='pending',
            quota_window_start=timezone.now()
        )
        
        # Trigger background task for extraction
        from .tasks import process_document_task
        process_document_task.delay(doc.id)
        
        return Response({
            "success": True,
            "document_id": doc.id,
            "file_name": uploaded_file.name,
            "file_type": file_type,
            "status": "pending",
            "message": "Document uploaded and queued for processing",
            "remaining_uploads": quota_limit - (recent_uploads + 1)
        }, status=status.HTTP_201_CREATED)
        
    except Exception as e:
        return Response(
            {"error": str(e)},
            status=status.HTTP_500_INTERNAL_SERVER_ERROR
        )


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def get_upload_quota(request, room_id):
    """
    GET /api/rooms/<room_id>/documents/quota/
    
    Get current upload quota status
    """
    try:
        # Get user's workspace plan
        try:
            plan = request.user.workspace.plan
        except AttributeError:
            plan = 'free'
        
        quotas = {
            'free': 10,
            'pro': 100,
            'agency': 10000
        }
        quota_limit = quotas.get(plan, 10)
        
        # Count recent uploads
        ten_hours_ago = timezone.now() - timedelta(hours=10)
        recent_uploads = DocumentUpload.objects.filter(
            user=request.user,
            uploaded_at__gte=ten_hours_ago
        ).count()
        
        remaining = max(0, quota_limit - recent_uploads)
        
        # Calculate reset time
        first_upload = DocumentUpload.objects.filter(
            user=request.user,
            uploaded_at__gte=ten_hours_ago
        ).order_by('uploaded_at').first()
        
        resets_at = None
        if first_upload:
            resets_at = (first_upload.uploaded_at + timedelta(hours=10)).isoformat()
        
        return Response({
            "plan": plan,
            "total": quota_limit,
            "used": recent_uploads,
            "remaining": remaining,
            "resets_at": resets_at
        }, status=status.HTTP_200_OK)
        
    except Exception as e:
        return Response(
            {"error": str(e)},
            status=status.HTTP_500_INTERNAL_SERVER_ERROR
        )
