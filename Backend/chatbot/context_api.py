"""
Context API Views
Endpoints for Room Context & Memory System
"""
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework import status
from django.shortcuts import get_object_or_404
from chatbot.models import Chatroom
from chatbot.context_manager import ContextManager


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def get_room_context(request, room_id):
    """
    GET /api/rooms/<room_id>/context/
    
    Returns AI-generated context, summary, and recent notes for a chatroom
    """
    try:
        chatroom = get_object_or_404(Chatroom, id=room_id)
        
        # Verify user has access to this room
        if not chatroom.participants.filter(User=request.user).exists():
            return Response(
                {"error": "You don't have access to this room"},
                status=status.HTTP_403_FORBIDDEN
            )
        
        # Get context using ContextManager
        context = ContextManager.get_context_for_ai(chatroom, lookback_hours=24)
        
        return Response(context, status=status.HTTP_200_OK)
        
    except Chatroom.DoesNotExist:
        return Response(
            {"error": "Chatroom not found"},
            status=status.HTTP_404_NOT_FOUND
        )
    except Exception as e:
        return Response(
            {"error": str(e)},
            status=status.HTTP_500_INTERNAL_SERVER_ERROR
        )


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def add_note(request, room_id):
    """
    POST /api/rooms/<room_id>/notes/
    
    Create a manual note in the room context
    
    Body:
    {
        "note_type": "decision" | "action_item" | "insight" | "reminder" | "written",
        "content": "Note content",
        "priority": "low" | "medium" | "high",
        "tags": ["tag1", "tag2"]
    }
    """
    try:
        chatroom = get_object_or_404(Chatroom, id=room_id)
        
        # Verify user has access to this room
        if not chatroom.participants.filter(User=request.user).exists():
            return Response(
                {"error": "You don't have access to this room"},
                status=status.HTTP_403_FORBIDDEN
            )
        
        note_type = request.data.get('note_type', 'written')
        content = request.data.get('content', '')
        priority = request.data.get('priority', 'medium')
        tags = request.data.get('tags', [])
        
        if not content:
            return Response(
                {"error": "Content is required"},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        # Create note using ContextManager
        note = ContextManager.add_note(
            chatroom=chatroom,
            note_type=note_type,
            content=content,
            created_by=request.user,
            tags=tags,
            priority=priority
        )
        
        return Response({
            "success": True,
            "note_id": note.id,
            "note_type": note.note_type,
            "content": note.content
        }, status=status.HTTP_201_CREATED)
        
    except Exception as e:
        return Response(
            {"error": str(e)},
            status=status.HTTP_500_INTERNAL_SERVER_ERROR
        )
