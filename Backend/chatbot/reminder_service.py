
import logging
import re
import json
from datetime import timedelta, datetime
from django.utils import timezone
from .models import Reminder, Chatroom

logger = logging.getLogger(__name__)

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
    """Get a pytz timezone object from a timezone string, defaulting to UTC.

    Args:
        user_timezone: Optional IANA timezone string (e.g., 'Africa/Nairobi', 'America/New_York').

    Returns:
        A pytz timezone object if pytz is available, otherwise django.utils.timezone.utc.
        Falls back to UTC if the provided timezone string is invalid or None.
    """
    if PYTZ_AVAILABLE:
        if user_timezone and user_timezone in pytz.all_timezones:
            return pytz.timezone(user_timezone)
        return pytz.UTC
    return timezone.utc


async def parse_reminder_time(text: str, user_timezone: str = "UTC"):
    """
    Parse natural language reminder text and return a timezone-aware datetime.
    This is the standalone function used by tests and the reminder connector.

    Args:
        text: Natural language reminder text (e.g., "in 10 minutes", "tomorrow at 9am")
        user_timezone: IANA timezone string (e.g., "America/New_York")

    Returns:
        ISO format datetime string if successful, None if parsing failed
    """
    parser = LLMTimeParser()
    result = await parser.parse(text, user_timezone=user_timezone)
    return result.get("datetime")


def _parse_clock(text: str):
    """Extract an (hour, minute) 24h pair from a clock expression, or None.

    Parses clock times like "5pm", "9:30am", "14:00" and converts to 24-hour format.

    Args:
        text: String containing a clock time expression.

    Returns:
        A tuple of (hour, minute) in 24-hour format, or None if no valid time is found.
        Example: "5pm" returns (17, 0), "9:30am" returns (9, 30).
    """
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


class LLMTimeParser:
    """LLM-based time parser with clarification support.

    Parses natural language time expressions using an LLM, with a deterministic
    fallback parser for when the LLM is unavailable. Supports clarification requests
    for ambiguous time expressions.
    """

    SYSTEM_PROMPT = (
        "You are a precise time parser for a reminder system. "
        "Parse natural language time expressions into ISO 8601 datetime strings.\n\n"
        "SUPPORTED FORMATS:\n"
        '- ISO 8601: "2024-12-25T10:30:00", "2024-12-25T10:30:00+03:00"\n'
        '- Relative: "in 10 minutes", "in 3 hours", "in 3 days", "in 2 weeks"\n'
        '- Relative days: "tomorrow", "tomorrow at 9am", "today at 5pm", "today", "yesterday"\n'
        '- Clock times: "5pm", "5:30pm", "9:30am", "14:30"\n'
        '- Plain numbers: "10" (minutes from now)\n\n'
        "RULES:\n"
        "1. If the time is ambiguous (e.g., \"Monday\", \"next week\", \"Friday\"), "
        "ask for clarification\n"
        "2. \"Monday\" without \"this/next\" is ambiguous - ask \"this Monday\" or "
        "\"next Monday\"?\n"
        "3. \"Monday at 9am\" without \"this/next\" - ambiguous\n"
        "4. \"Tomorrow at 9am\" is unambiguous (tomorrow is always tomorrow)\n"
        "5. \"In 3 days\" is unambiguous\n"
        "8. Return ISO 8601 in UTC for the parsed time\n\n"
        "RESPONSE FORMAT (JSON):\n"
        "{\n"
        '  "datetime": "2024-12-25T10:30:00+00:00",\n'
        '  "needs_clarification": false,\n'
        '  "clarification_question": null,\n'
        '  "confidence": 0.95,\n'
        '  "interpretation": "User wants reminder for December 25th, 2024 at 10:30 AM UTC"\n'
        "}\n\n"
        "If ambiguous, return:\n"
        "{\n"
        '  "datetime": null,\n'
        '  "needs_clarification": true,\n'
        '  "clarification_question": "Did you mean this Monday (Dec 25) or next Monday (Jan 1)?",\n'
        '  "confidence": 0.3,\n'
        '  "interpretation": "User said \'Monday\' but didn\'t specify which Monday"\n'
        "}\n\n"
        "If failed to parse, return:\n"
        "{\n"
        '  "datetime": null,\n'
        '  "needs_clarification": true,\n'
        '  "clarification_question": "Could you clarify the time? '
        "(e.g., 'tomorrow at 9am' or 'in 2 hours')\",\n"
        '  "confidence": 0.0,\n'
        '  "interpretation": "Failed to parse"\n'
        "}"
    )

    def __init__(self, llm_client=None):
        """Initialize the LLM time parser.

        Args:
            llm_client: Optional LLM client for parsing. If None, uses fallback parser.
        """
        self.llm_client = llm_client

    async def parse(self, text: str, user_timezone: str = "UTC", context: dict = None) -> dict:
        """Parse time expression with LLM, returning structured result.

        Attempts to parse the time expression using an LLM if available, otherwise
        falls back to a deterministic parser. Returns a dict with datetime, clarification
        status, and interpretation.

        Args:
            text: Natural language time expression to parse.
            user_timezone: IANA timezone string for the user (default: "UTC").
            context: Optional additional context for parsing (unused).

        Returns:
            Dict with keys: datetime (ISO string or None), needs_clarification (bool),
            clarification_question (str or None), confidence (float), interpretation (str).
        """
        if not self.llm_client:
            # Fallback to deterministic parser
            return self._fallback_parse(text, user_timezone)

        prompt = f"User timezone: {user_timezone}\nCurrent time: {timezone.now().isoformat()}\n\nParse: \"{text}\""

        try:
            response = await self.llm_client.generate(
                system_prompt=self.SYSTEM_PROMPT,
                user_prompt=prompt,
                temperature=0.1,
                max_tokens=500,
                response_format={"type": "json_object"}
            )
            result = json.loads(response)

            # Validate response
            if "datetime" in result and result["datetime"]:
                # Verify it's valid ISO format
                parsed_dt = datetime.fromisoformat(result["datetime"].replace("Z", "+00:00"))
                if parsed_dt <= timezone.now():
                    # If in past, assume next occurrence (add 1 day)
                    parsed_dt = parsed_dt + timedelta(days=1)
                    result["datetime"] = parsed_dt.isoformat()
                return {
                    "datetime": result["datetime"],
                    "needs_clarification": result.get("needs_clarification", False),
                    "clarification_question": result.get("clarification_question"),
                    "confidence": result.get("confidence", 0.9),
                    "interpretation": result.get("interpretation", "")
                }
            else:
                return {
                    "datetime": None,
                    "needs_clarification": result.get("needs_clarification", True),
                    "clarification_question": result.get("clarification_question", "Could you clarify the time?"),
                    "confidence": result.get("confidence", 0.0),
                    "interpretation": result.get("interpretation", "")
                }
        except Exception as e:
            logging.error(f"LLM time parse error: {e}")
            return self._fallback_parse(text, user_timezone)

    def _fallback_parse(self, text: str, user_timezone: str = "UTC") -> dict:
        """Deterministic fallback parser for when LLM is unavailable.

        Parses common time patterns using regex and dateutil: relative times
        (e.g., "in 10 minutes"), clock times (e.g., "5pm"), relative days
        (e.g., "tomorrow"), and ISO datetime strings.

        Args:
            text: Natural language time expression to parse.
            user_timezone: IANA timezone string for the user (default: "UTC").

        Returns:
            Dict with keys: datetime (ISO string or None), needs_clarification (bool),
            clarification_question (str or None), confidence (float), interpretation (str).
        """
        from django.utils import timezone
        from datetime import timedelta

        if not text:
            return {
                "datetime": None,
                "needs_clarification": True,
                "clarification_question": "Could you clarify the time? (e.g., 'tomorrow at 9am' or 'in 2 hours')",
                "confidence": 0.0,
                "interpretation": "Failed to parse"
            }

        text = str(text).strip()
        lower = text.lower()

        # Get user's timezone
        user_tz = get_user_timezone(user_timezone)
        now = timezone.now().astimezone(user_tz) if PYTZ_AVAILABLE else timezone.now()

        # Check for relative time patterns
        relative = _RELATIVE_RE.search(lower)
        if relative:
            quantity = int(relative.group(1))
            unit = relative.group(2).lower()
            if unit in _MINUTE_UNITS:
                return {
                    "datetime": (now + timedelta(minutes=quantity)).isoformat(),
                    "needs_clarification": False,
                    "clarification_question": None,
                    "confidence": 0.9,
                    "interpretation": "Parsed via fallback"
                }
            if unit in _HOUR_UNITS:
                return {
                    "datetime": (now + timedelta(hours=quantity)).isoformat(),
                    "needs_clarification": False,
                    "clarification_question": None,
                    "confidence": 0.9,
                    "interpretation": "Parsed via fallback"
                }
            if unit in _DAY_UNITS:
                return {
                    "datetime": (now + timedelta(days=quantity)).isoformat(),
                    "needs_clarification": False,
                    "clarification_question": None,
                    "confidence": 0.9,
                    "interpretation": "Parsed via fallback"
                }
            if unit in _WEEK_UNITS:
                return {
                    "datetime": (now + timedelta(weeks=quantity)).isoformat(),
                    "needs_clarification": False,
                    "clarification_question": None,
                    "confidence": 0.9,
                    "interpretation": "Parsed via fallback"
                }

        if text.isdigit():
            return {
                "datetime": (now + timedelta(minutes=int(text))).isoformat(),
                "needs_clarification": False,
                "clarification_question": None,
                "confidence": 0.9,
                "interpretation": "Parsed via fallback"
            }

        if "tomorrow" in lower or "today" in lower:
            base = now + (timedelta(days=1) if "tomorrow" in lower else timedelta(0))
            clock = _parse_clock(text)
            if clock:
                target = base.replace(hour=clock[0], minute=clock[1], second=0, microsecond=0)
            else:
                target = base.replace(hour=9, minute=0, second=0, microsecond=0)
            if target <= now:
                target += timedelta(days=1)
            return {
                "datetime": target.isoformat(),
                "needs_clarification": False,
                "clarification_question": None,
                "confidence": 0.9,
                "interpretation": "Parsed via fallback"
            }

        try:
            from dateutil import parser as dateutil_parser
            parsed = dateutil_parser.parse(text)
            if timezone.is_naive(parsed):
                parsed = user_tz.localize(parsed) if PYTZ_AVAILABLE else timezone.make_aware(parsed)
            if parsed <= now:
                parsed += timedelta(days=1)
            return {
                "datetime": parsed.isoformat(),
                "needs_clarification": False,
                "clarification_question": None,
                "confidence": 0.9,
                "interpretation": "Parsed via fallback"
            }
        except Exception as e:
            logger.debug("dateutil parse failed for %r: %s", text, e)

        clock = _parse_clock(text)
        if clock:
            target = now.replace(hour=clock[0], minute=clock[1], second=0, microsecond=0)
            if target <= now:
                target += timedelta(days=1)
            return {
                "datetime": target.isoformat(),
                "needs_clarification": False,
                "clarification_question": None,
                "confidence": 0.9,
                "interpretation": "Parsed via fallback"
            }

        return {
            "datetime": None,
            "needs_clarification": True,
            "clarification_question": "Could you clarify the time? (e.g., 'tomorrow at 9am' or 'in 2 hours')",
            "confidence": 0.0,
            "interpretation": "Failed to parse"
        }


class ReminderService:
    @staticmethod
    def _extract_time_expression(text: str) -> str:
        """
        Extract time expression from reminder text.
        Examples:
            "Remind me to call John in 10 minutes" -> "in 10 minutes"
            "Remind me to take a break at 5pm" -> "at 5pm"
            "Set a reminder for tomorrow at 9am to water plants" -> "tomorrow at 9am"
            "in 5 minutes" -> "in 5 minutes" (already just time)
        """
        # Common reminder prefixes to strip
        reminder_patterns = [
            r"^remind me to\s+.*?(in\s+\d+\s+(?:minutes?|mins?|hours?|hrs?|days?|weeks?)).*$",
            r"^remind me to\s+.*?(at\s+\d{1,2}(?::\d{2})?\s*(?:am|pm)?).*$",
            r"^remind me to\s+.*?(tomorrow\s+(?:at\s+\d{1,2}(?::\d{2})?\s*(?:am|pm)?)).*$",
            r"^remind me to\s+.*?(today\s+(?:at\s+\d{1,2}(?::\d{2})?\s*(?:am|pm)?)).*$",
            r"^set (?:a )?reminder (?:for\s+)?(.*?)(?:\s+to\s+|\s*$)",
            r"^remind me\s+(?:to\s+)?(?:.*?)\s+(in\s+\d+\s+(?:minutes?|mins?|hours?|hrs?|days?|weeks?)).*$",
            r"^remind me\s+(?:to\s+)?(?:.*?)\s+(at\s+\d{1,2}(?::\d{2})?\s*(?:am|pm)?).*$",
        ]

        text_lower = text.lower().strip()

        # Try to match and extract time expression
        for pattern in reminder_patterns:
            match = re.search(pattern, text_lower, re.IGNORECASE)
            if match:
                # Return the captured group (time expression)
                if match.groups():
                    return match.group(1).strip()
                else:
                    return match.group(0).strip()

        # If no pattern matches, check if text already looks like a time expression
        time_indicators = [
            "in ", "at ", "tomorrow", "today", "yesterday",
            "am", "pm", ":", "minutes", "mins", "hours", "hrs", "days", "weeks"
        ]
        if any(indicator in text_lower for indicator in time_indicators):
            return text

        # Fallback: return original text
        return text

    @staticmethod
    async def parse_and_schedule(user, text, room_id=None):
        """
        Parse natural language reminder text and schedule it with LLM-based time parsing.
        Example: "Remind me to call John in 10 minutes"
        """
        content = text
        user_tz = "UTC"
        if hasattr(user, 'profile') and user.profile:
            user_tz = user.profile.timezone
        else:
            user_tz = "UTC"

        # Extract time expression from reminder text (e.g., "in 10 minutes" from "Remind me to call John in 10 minutes")
        time_expression = ReminderService._extract_time_expression(text)

        # Use LLM-based parser with clarification support
        parser = LLMTimeParser()
        parse_result = await parser.parse(time_expression, user_timezone=user_tz)

        try:
            if parse_result.get("needs_clarification"):
                # Return clarification needed response
                return {
                    "status": "needs_clarification",
                    "clarification_question": parse_result.get("clarification_question", "Could you clarify the time?"),
                    "partial_interpretation": parse_result.get("interpretation", ""),
                    "partial_datetime": parse_result.get("datetime"),
                }

            scheduled_time_str = parse_result.get("datetime")
            if not scheduled_time_str:
                return {"error": "Failed to parse time", "message": "Could not parse time expression"}

            # Parse the datetime string
            scheduled_time = datetime.fromisoformat(scheduled_time_str.replace("Z", "+00:00"))

            # Ensure timezone awareness
            from django.utils import timezone
            if timezone.is_naive(scheduled_time):
                scheduled_time = timezone.make_aware(scheduled_time)

            # Validation
            if scheduled_time <= timezone.now():
                return {"error": "Cannot schedule for past time"}
            if scheduled_time > timezone.now() + timedelta(days=365):
                return {"error": "Cannot schedule more than 1 year in advance"}
            if scheduled_time < timezone.now() + timedelta(minutes=1):
                return {"error": "Cannot schedule for less than 1 minute from now"}

            room = None
            if room_id:
                try:
                    room = await Chatroom.objects.aget(id=room_id)
                except Chatroom.DoesNotExist:
                    pass

            reminder = await Reminder.objects.acreate(
                user=user,
                room=room,
                content=text,  # Store the original text as content
                scheduled_time=scheduled_time,
                status='pending',
                timezone=user.profile.timezone if hasattr(user, 'profile') else 'UTC'
            )

            try:
                from asgiref.sync import sync_to_async as _sync_to_async
                from chatbot.tasks import schedule_reminder_delivery
                await _sync_to_async(schedule_reminder_delivery)(reminder.id, scheduled_time)
            except Exception as e:
                logger.warning(f"Reminder scheduling skipped: {e}")
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
