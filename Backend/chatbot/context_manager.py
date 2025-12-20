"""
Context Manager for Mathia AI
Handles storage, retrieval, and LLM-ready formatting of conversation context.
Supports Cross-Room context sharing for high-priority notes.
"""
import logging
import json
from django.utils import timezone
from .models import RoomContext, RoomNote, AIConversation

logger = logging.getLogger(__name__)

class ContextManager:
    """
    Manages the 'Memory' of the AI.
    - Retrieves relevant notes/summary for prompt injection.
    - Handles Cross-Room sharing (e.g. User's generic constraints apply to all rooms).
    """

    @staticmethod
    def get_context_for_ai(chatroom, lookback_hours=24):
        """
        Get structured context for the AI System Prompt.
        Includes:
        1. Room Summary & Active Topics
        2. Recent Notes (Decisions, Action Items)
        3. Cross-Room Logic (High Priority notes from other rooms for this user/workspace)
        """
        try:
            # 1. Get Local Room Context
            context_obj, created = RoomContext.objects.get_or_create(chatroom=chatroom)
            
            # 2. Get Recent Local Notes
            local_notes = RoomNote.objects.filter(
                room_context=context_obj
            ).order_by('-created_at')[:5] # Last 5 active notes
            
            # Format Local Data
            context_data = {
                "summary": context_obj.summary or "",
                "active_topics": context_obj.active_topics or [],
                "recent_notes": [ContextManager._format_note(n) for n in local_notes],
                "latest_daily_summary": ""
            }
            
            # 3. GLOBAL/CROSS-ROOM CONTEXT (The "Memory" across rooms)
            # Find high-priority or 'insight' notes from other rooms involving these participants
            # For simplicity in this MVP: We fetch notes created by the room creator in *any* room
            # that are marked 'high' priority or 'insight' type.
            
            # Determine "Key User" (usually room creator or owner)
            key_user = None
            """
            if chatroom.name.startswith('private_'):
                # Private room logic (if applicable)
                pass """
            
            # Use the first admin or just standard cross-room fetch based on workspace if we had it.
            # Here we'll search notes created by participants of this room in OTHER rooms.
            participants = chatroom.participants.all()
            users = [m.User for m in participants]
            
            global_notes = RoomNote.objects.filter(
                created_by__in=users,
                priority='high'
            ).exclude(room_context=context_obj).order_by('-created_at')[:3]
            
            if global_notes:
                context_data['global_notes'] = [ContextManager._format_note(n) for n in global_notes]
                
            return context_data
            
        except Exception as e:
            logger.error(f"Error getting context: {e}")
            return {"summary": "", "notes": []}

    @staticmethod
    def get_context_prompt(room_id):
        """
        Returns a formatted string to append to the System Prompt.
        """
        from .models import Chatroom
        try:
            room = Chatroom.objects.get(id=room_id)
            data = ContextManager.get_context_for_ai(room)
            
            prompt_parts = []
            
            # Add Summary
            if data['summary']:
                prompt_parts.append(f"ROOM CONTEXT:\n{data['summary']}")
            
            # Add Active Topics
            if data['active_topics']:
                prompt_parts.append(f"TOPICS: {', '.join(data['active_topics'])}")
            
            # Add Local Notes
            if data['recent_notes']:
                prompt_parts.append("IMPORTANT NOTES:")
                for note in data['recent_notes']:
                    prompt_parts.append(f"- [{note['type'].upper()}] {note['content']}")
            
            # Add Global/Cross-Room Notes
            if data.get('global_notes'):
                prompt_parts.append("RELEVANT MEMORY (From other chats):")
                for note in data['global_notes']:
                    prompt_parts.append(f"- [MEMORY] {note['content']}")
                    
            if not prompt_parts:
                return ""
                
            return "\n\n" + "\n".join(prompt_parts)
            
        except Exception as e:
            logger.error(f"Error building context prompt: {e}")
            return ""

    @staticmethod
    def add_note(chatroom, note_type, content, created_by, tags=None, priority='medium'):
        """
        Manually add a note to the context
        """
        context, _ = RoomContext.objects.get_or_create(chatroom=chatroom)
        
        note = RoomNote.objects.create(
            room_context=context,
            note_type=note_type,
            content=content,
            created_by=created_by,
            tags=tags or [],
            priority=priority
        )
        return note

    @staticmethod
    def _format_note(note):
        return {
            "id": note.id,
            "type": note.note_type,
            "content": note.content,
            "priority": note.priority,
            "created_at": note.created_at.isoformat(),
            "tags": note.tags
        }
