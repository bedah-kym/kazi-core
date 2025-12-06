"""
Context Manager for 3-Tier Memory System
Handles AI memory, context retrieval, and compression
"""
import logging
from datetime import datetime, timedelta
from django.utils import timezone
from django.db.models import Q
from .models import RoomContext, RoomNote, DailySummary, Chatroom, Message

logger = logging.getLogger(__name__)

class ContextManager:
    """
    Manages room context across 3 tiers:
    - Tier 1: Hot (recent messages + active notes)
    - Tier 2: Warm (important notes/decisions)
    - Tier 3: Cold (daily summaries)
    """
    
    @staticmethod
    def get_or_create_context(chatroom):
        """Get or create RoomContext for a chatroom"""
        context, created = RoomContext.objects.get_or_create(
            chatroom=chatroom,
            defaults={
                'summary': '',
                'participants': [],
                'entities': {},
                'active_topics': []
            }
        )
        return context
    
    @staticmethod
    def get_context_for_ai(chatroom, lookback_hours=24):
        """
        Retrieve context for AI prompts
        Returns: Dict with summary, recent notes, and key entities
        """
        context = ContextManager.get_or_create_context(chatroom)
        
        # Get recent active notes (Tier 2)
        cutoff = timezone.now() - timedelta(hours=lookback_hours)
        recent_notes = RoomNote.objects.filter(
            room_context=context,
            created_at__gte=cutoff,
            is_completed=False
        ).order_by('-priority', '-created_at')[:10]
        
        # Get latest daily summary if available (Tier 3)
        latest_summary = context.daily_summaries.first()
        
        return {
            'summary': context.summary or "No summary yet",
            'participants': context.participants,
            'entities': context.entities,
            'active_topics': context.active_topics,
            'recent_notes': [
                {
                    'type': note.note_type,
                    'content': note.content,
                    'priority': note.priority,
                    'tags': note.tags
                }
                for note in recent_notes
            ],
            'latest_daily_summary': latest_summary.summary if latest_summary else None,
            'message_count': context.message_count
        }
    
    @staticmethod
    def add_note(chatroom, note_type, content, created_by=None, tags=None, priority='medium'):
        """
        Add a note to room context
        Can be user-created or AI-extracted
        """
        context = ContextManager.get_or_create_context(chatroom)
        
        note = RoomNote.objects.create(
            room_context=context,
            note_type=note_type,
            content=content,
            created_by=created_by,
            is_ai_generated=(created_by is None),
            priority=priority,
            tags=tags or []
        )
        
        return note
    
    @staticmethod
    async def compress_context(chatroom):
        """
        Compress recent messages into context summary
        Called periodically (e.g., every 100 messages or daily)
        """
        from orchestration.llm_client import get_llm_client
        
        context = ContextManager.get_or_create_context(chatroom)
        
        # Get recent messages (last 50)
        from chatbot.consumers import decrypt_message
        recent_messages = chatroom.chats.order_by('-timestamp')[:50]
        
        # Decrypt and format messages for LLM
        message_text = []
        for msg in reversed(list(recent_messages)):
            try:
                decrypted = decrypt_message(msg.content, chatroom.encryption_key)
                sender = msg.member.User.username if msg.member else "System"
                message_text.append(f"{sender}: {decrypted}")
            except Exception as e:
                logger.error(f"Error decrypting message: {e}")
                continue
        
        if not message_text:
            return context
        
        # Use LLM to generate summary + extract entities
        llm = get_llm_client()
        
        prompt = f"""
Analyze this conversation and provide:
1. A concise summary (2-3 sentences)
2. Key participants mentioned
3. Important entities (people, companies, projects)
4. Active topics being discussed

Conversation:
{chr(10).join(message_text[-30:])}

Respond in JSON format:
{{
    "summary": "...",
    "participants": ["name1", "name2"],
    "entities": {{"people": [...], "companies": [...], "projects": [...]}},
    "topics": ["topic1", "topic2"]
}}
"""
        
        try:
            response = await llm.generate_text(
                system_prompt="You are a context analysis assistant. Extract key information from conversations.",
                user_prompt=prompt,
                temperature=0.3
            )
            
            # Parse LLM response (basic JSON extraction)
            import json
            # Try to find JSON in response
            start_idx = response.find('{')
            end_idx = response.rfind('}') + 1
            if start_idx != -1 and end_idx > start_idx:
                data = json.loads(response[start_idx:end_idx])
                
                context.summary = data.get('summary', context.summary)
                context.participants = data.get('participants', context.participants)
                context.entities = data.get('entities', context.entities)
                context.active_topics = data.get('topics', context.active_topics)
                context.last_compressed_at = timezone.now()
                context.message_count = chatroom.chats.count()
                context.save()
                
        except Exception as e:
            logger.error(f"Context compression failed: {e}")
        
        return context
    
    @staticmethod
    def create_daily_summary(chatroom, date=None):
        """
        Create daily summary for a chatroom
        Typically run as a Celery task at end of day
        """
        if date is None:
            date = timezone.now().date()
        
        context = ContextManager.get_or_create_context(chatroom)
        
        # Get messages from that day
        start_of_day = datetime.combine(date, datetime.min.time())
        end_of_day = datetime.combine(date, datetime.max.time())
        
        daily_messages = chatroom.chats.filter(
            timestamp__gte=start_of_day,
            timestamp__lte=end_of_day
        )
        
        message_count = daily_messages.count()
        
        if message_count == 0:
            return None  # No activity today
        
        # Count unique participants
        participant_count = daily_messages.values('member').distinct().count()
        
        # Count notes created today
        notes_today = context.notes.filter(
            created_at__date=date
        ).count()
        
        # Use context summary or generate new one
        summary_text = context.summary or f"{message_count} messages exchanged"
        
        # Create or update daily summary
        daily_summary, created = DailySummary.objects.update_or_create(
            room_context=context,
            date=date,
            defaults={
                'summary': summary_text,
                'message_count': message_count,
                'participant_count': participant_count,
                'notes_created': notes_today,
                'highlights': []  # Can be populated by AI later
            }
        )
        
        return daily_summary
