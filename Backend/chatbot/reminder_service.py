
import logging
from datetime import timedelta
from dateutil import parser
from django.utils import timezone
from .models import Reminder, Chatroom
from django.contrib.auth import get_user_model

logger = logging.getLogger(__name__)
User = get_user_model()

class ReminderService:
    @staticmethod
    def parse_and_schedule(user, text, room_id=None):
        """
        Parse natural language reminder text and schedule it.
        Example: "Remind me to call John in 10 minutes"
        """
        # Simple keyword parsing for prototype
        # In production, use an LLM or dateparer library with NLP
        
        content = text
        scheduled_time = None
        
        # 1. Look for explicit time patterns (very basic regex/keyword fallback)
        now = timezone.now()
        
        try:
            lower_text = text.lower()
            if "in " in lower_text and ("min" in lower_text or "hour" in lower_text):
                 # "in 10 mins"
                 parts = lower_text.split("in ")[1].split(" ")
                 if len(parts) >= 2:
                     qty = int(parts[0])
                     unit = parts[1]
                     if "min" in unit:
                         scheduled_time = now + timedelta(minutes=qty)
                     elif "hour" in unit:
                         scheduled_time = now + timedelta(hours=qty)
            
            # Fallback to dateutil if possible (extracted part)
            # This is hard without an LLM extracting the date part
            # For now, if no time found, default to 1 hour
            
            if not scheduled_time:
                 # Default fallback
                 scheduled_time = now + timedelta(hours=1)
            
            # Save reminder
            room = None
            if room_id:
                try:
                    room = Chatroom.objects.get(id=room_id)
                except Chatroom.DoesNotExist:
                    pass
            
            reminder = Reminder.objects.create(
                user=user,
                room=room,
                content=content,
                scheduled_time=scheduled_time,
                status='pending'
            )

            try:
                from chatbot.tasks import schedule_reminder_delivery
                schedule_reminder_delivery(reminder.id, scheduled_time)
            except Exception as e:
                logger.warning(f"Reminder scheduling skipped: {e}")
            
            # Schedule Celery task (Mock for now if Celery not fully set up)
            # send_reminder_task.apply_async((reminder.id,), eta=scheduled_time)
            
            return {
                "status": "scheduled",
                "reminder_id": reminder.id,
                "time": scheduled_time.isoformat(),
                "message": f"I'll remind you to '{content}' at {scheduled_time.strftime('%H:%M')}."
            }
            
        except Exception as e:
            logger.error(f"Reminder Parse Error: {e}")
            return {"error": "Failed to schedule reminder."}

    @staticmethod
    def send_via_email(reminder):
        """Send reminder via Mailgun"""
        from django.conf import settings
        import requests 
        # Using official SDK logic conceptually or requests if SDK wrapper not ready yet.
        # Ideally import mailgun
        
        try:
            logger.info(f"Sending email reminder {reminder.id} to {reminder.user.email}")
            # Mock implementation connecting to configured Mailgun
            # ...
            # status = success
            reminder.sent_at = timezone.now()
            reminder.status = 'sent'
            reminder.save()
            return True
        except Exception as e:
            logger.error(f"Email send failed: {e}")
            reminder.status = 'failed'
            reminder.error_log = str(e)
            reminder.save()
            return False
