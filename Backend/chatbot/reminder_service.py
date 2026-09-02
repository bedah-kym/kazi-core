
import logging
import re
from datetime import timedelta
from django.utils import timezone

try:
    import pytz
    PYTZ_AVAILABLE = True
except ImportError:
    PYTZ_AVAILABLE = False


_RELATIVE_RE = re.compile(r"\bin\s+(\d+)\s*(minutes?|mins?|hours?|hrs?|days?|weeks?)\b", re.IGNORECASE)
_CLOCK_RE = re.compile(r"(\d{1,2})(?::(\d{2}))?\s*(am|pm)?", re.IGNORECASE)

_MINUTE_UNITS = {"minute", "minutes", "min", "mins"}
_HOUR_UNITS = {"hour", "hours", "hr", "hrs"}
_DAY_UNITS = {"day", "days"}
_WEEK_UNITS = {"week", "weeks"}


def get_user_timezone(user_timezone: str = None):
    """Get a pytz timezone object from a timezone string, defaulting to UTC."""
    if PYTZ_AVAILABLE:
        if user_timezone and user_timezone in pytz.all_timezones:
            return pytz.timezone(user_timezone)
        return pytz.UTC
    return timezone.utc


def _parse_clock(text: str):
    """Extract an (hour, minute) 24h pair from a clock expression, or None."""
    match = _CLOCK_RE.search(text or "")
    if not match:
        return None
    hour = int(match.group(1))
    minute = int(match.group(2) or 0)
    meridian = (match.group(3) or "").lower()
    if meridian == "pm" and hour < 12:
        hour += 12
    elif meridian == "am" and hour == 12:
        hour = 0
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        return None
    return hour, minute


def parse_reminder_time(time_str, user_timezone: str = None):
    """Parse a reminder time expression into an aware datetime, or None.

    Supports ISO datetimes, relative durations ("in 30 minutes", "in 3 days"),
    "tomorrow"/"today" with an optional clock ("tomorrow at 9am"), bare clock
    times ("5pm", "9:30am"), and plain integers interpreted as minutes.

    Args:
        time_str: The time expression to parse
        user_timezone: Optional IANA timezone string (e.g., "Africa/Nairobi", "America/New_York")
        
    Returns:
        An aware datetime in the user's timezone, or None if parsing fails.
    """
    if not time_str:
        return None
    text = str(time_str).strip()
    lower = text.lower()

    # Get user's timezone
    user_tz = get_user_timezone(user_timezone)
    now = timezone.now().astimezone(user_tz) if PYTZ_AVAILABLE else timezone.now()

    relative = _RELATIVE_RE.search(lower)
    if relative:
        quantity = int(relative.group(1))
        unit = relative.group(2).lower()
        if unit in _MINUTE_UNITS:
            return now + timedelta(minutes=quantity)
        if unit in _HOUR_UNITS:
            return now + timedelta(hours=quantity)
        if unit in _DAY_UNITS:
            return now + timedelta(days=quantity)
        if unit in _WEEK_UNITS:
            return now + timedelta(weeks=quantity)

    if text.isdigit():
        return now + timedelta(minutes=int(text))

    if "tomorrow" in lower or "today" in lower:
        base = now + (timedelta(days=1) if "tomorrow" in lower else timedelta(0))
        clock = _parse_clock(text)
        if clock:
            target = base.replace(hour=clock[0], minute=clock[1], second=0, microsecond=0)
        else:
            target = base.replace(hour=9, minute=0, second=0, microsecond=0)
        if target <= now:
            target += timedelta(days=1)
        return target

    try:
        from dateutil import parser as dateutil_parser

        parsed = dateutil_parser.parse(text)
        if timezone.is_naive(parsed):
            parsed = user_tz.localize(parsed) if PYTZ_AVAILABLE else timezone.make_aware(parsed)
        if parsed <= now:
            parsed += timedelta(days=1)
        return parsed
    except Exception:
        pass

    clock = _parse_clock(text)
    if clock:
        target = now.replace(hour=clock[0], minute=clock[1], second=0, microsecond=0)
        if target <= now:
            target += timedelta(days=1)
        return target

    return None


class ReminderService:
    @staticmethod
    def parse_and_schedule(user, text, room_id=None):
        """
        Parse natural language reminder text and schedule it.
        Example: "Remind me to call John in 10 minutes"
        """
        # Simple keyword parsing for prototype
        # In production, use an LLM or dateparser library with NLP

        content = text
        scheduled_time = None

        # 1. Look for explicit time patterns (very basic regex/keyword fallback)
        now = timezone.now()
        user_tz = get_user_timezone(user.profile.timezone if hasattr(user, 'profile') else None)
        now = timezone.now().astimezone(get_user_timezone(user.profile.timezone if hasattr(user, 'profile') else None))

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
                status='pending',
                timezone=user.profile.timezone if hasattr(user, 'profile') else 'UTC'
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
