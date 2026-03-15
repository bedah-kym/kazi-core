"""
Reference Resolver for intelligent request understanding.
Resolves vague pronouns, adjectives, and temporal references to actual data.

Examples:
  - "those flights" → last search results
  - "my team" → workspace members
  - "the cheapest one" → sort by price, pick min
  - "next week" → today + 7 days
  - "same dates as before" → last search dates
"""

from typing import Dict, List, Optional, Any, Tuple
from datetime import datetime, timedelta
import re
from difflib import SequenceMatcher

from asgiref.sync import sync_to_async
from django.utils import timezone
from django.contrib.auth import get_user_model
from django.core.cache import cache

User = get_user_model()


class ReferenceResolver:
    """Resolve vague references in user requests to concrete values."""

    def __init__(self, user_id: int, room_id: int, context: Dict[str, Any]):
        """
        Args:
            user_id: User making the request
            room_id: Room context
            context: Full context including history, memory, last results
        """
        self.user_id = user_id
        self.room_id = room_id
        self.context = context
        self.user = None
        self.workspace = None

    async def _ensure_user(self):
        """Ensure user and workspace are loaded (async-safe)."""
        if self.user is None:
            self.user = await User.objects.aget(pk=self.user_id)
            self.workspace = await sync_to_async(lambda: self.user.workspace)()

    async def resolve_references(self, user_message: str) -> Dict[str, Any]:
        """
        Analyze user message and resolve all vague references.

        Returns:
            {
                "original_message": "book those flights and email my team",
                "resolved_message": "book flights to Kenya on Mar 15 and email alice@..., bob@...",
                "replacements": {
                    "those flights": {"type": "result_reference", "value": {...}},
                    "my team": {"type": "contact_list", "value": [...]}
                }
            }
        """
        await self._ensure_user()
        replacements = {}

        # 1. Resolve anaphoric references (those, that, it, etc.)
        message = user_message
        anaphora_resolved, anaphora_dict = self._resolve_anaphora(message)
        replacements.update(anaphora_dict)
        message = anaphora_resolved

        # 2. Resolve possessive references (my team, my preferences, etc.)
        possessive_resolved, possessive_dict = await self._resolve_possessives(message)
        replacements.update(possessive_dict)
        message = possessive_resolved

        # 3. Resolve adjective-based references (cheapest, earliest, newest, etc.)
        adjective_resolved, adjective_dict = self._resolve_adjectives(message)
        replacements.update(adjective_dict)
        message = adjective_resolved

        # 4. Resolve temporal references (next week, tomorrow, same dates as last time)
        temporal_resolved, temporal_dict = self._resolve_temporal(message)
        replacements.update(temporal_dict)
        message = temporal_resolved

        return {
            "original_message": user_message,
            "resolved_message": message,
            "replacements": replacements,
        }

    def _resolve_anaphora(self, message: str) -> Tuple[str, Dict]:
        """
        Resolve anaphoric references: 'those', 'that', 'it', 'them', 'these', etc.
        Usually refers to last search results or previous context.

        Example:
        - "Book those flights" → last search_flights results
        - "Send that email" → last drafted email
        """
        replaced_message = message
        replacements = {}

        anaphora_patterns = [
            (r'\bthose\s+(\w+)', 'those'),
            (r'\bthat\s+(\w+)', 'that'),
            (r'\bthese\s+(\w+)', 'these'),
            (r'\bthe\s+(\w+)', 'the'),
        ]

        for pattern, ref_type in anaphora_patterns:
            matches = re.finditer(pattern, message, re.IGNORECASE)
            for match in matches:
                item = match.group(1)  # e.g., "flights", "hotel", "option"

                # Map plural/singular to context
                last_result = self._find_last_result_for_item(item)
                if last_result:
                    replacements[match.group(0)] = {
                        "type": "anaphoric_reference",
                        "refers_to": item,
                        "value": last_result,
                    }
                    # Replace in message with something more specific
                    replaced_message = replaced_message.replace(
                        match.group(0),
                        f"[{item} from {last_result.get('source', 'previous search')}]"
                    )

        return replaced_message, replacements

    async def _resolve_possessives(self, message: str) -> Tuple[str, Dict]:
        """
        Resolve possessive references: 'my team', 'my preferences', 'my contacts', etc.

        Example:
        - "Email my team" → workspace members / contact list
        - "Use my preferences" → user settings
        """
        replaced_message = message
        replacements = {}

        possessive_patterns = [
            (r'\bmy\s+(team|group|contacts|people)\b', 'contacts'),
            (r'\bmy\s+(preferences|settings|preferences)\b', 'preferences'),
            (r'\bmy\s+(last|previous)\s+(\w+)', 'previous'),
        ]

        for pattern, ref_type in possessive_patterns:
            matches = re.finditer(pattern, message, re.IGNORECASE)
            for match in matches:
                if 'contacts' in pattern or 'team' in match.group(0).lower():
                    # Get workspace members / contact list
                    contacts = await self._get_user_contacts()
                    replacements[match.group(0)] = {
                        "type": "contact_reference",
                        "value": contacts,
                    }
                    contact_str = ", ".join([c.get('name', c.get('email', '?')) for c in contacts[:3]])
                    if len(contacts) > 3:
                        contact_str += f" and {len(contacts)-3} others"
                    replaced_message = replaced_message.replace(
                        match.group(0),
                        f"[{contact_str}]"
                    )

                elif 'preference' in match.group(0).lower():
                    prefs = await self._get_user_preferences()
                    replacements[match.group(0)] = {
                        "type": "preference_reference",
                        "value": prefs,
                    }

        return replaced_message, replacements

    def _resolve_adjectives(self, message: str) -> Tuple[str, Dict]:
        """
        Resolve adjective-based references when a list exists in context.
        'cheapest flight', 'earliest option', 'newest email', etc.

        Example:
        - "Book the cheapest flight" → min(flights, key=price)
        - "Pick the green one" → color match
        """
        replaced_message = message
        replacements = {}

        adjective_patterns = [
            (r'\b(cheapest|lowest|lowest price)\s+(\w+)', 'min_price'),
            (r'\b(most expensive|highest|highest price)\s+(\w+)', 'max_price'),
            (r'\b(earliest|soonest|fastest)\s+(\w+)', 'min_time'),
            (r'\b(latest|slowest)\s+(\w+)', 'max_time'),
            (r'\b(newest|latest)\s+(\w+)', 'most_recent'),
            (r'\b(oldest|first)\s+(\w+)', 'oldest'),
        ]

        for pattern, sort_key in adjective_patterns:
            matches = re.finditer(pattern, message, re.IGNORECASE)
            for match in matches:
                adjective = match.group(1)
                item_type = match.group(2)  # e.g., "flight", "hotel"

                # Find last results for this item
                last_result = self._find_last_result_for_item(item_type)
                if last_result and isinstance(last_result, list) and len(last_result) > 0:
                    # Sort by adjective
                    sorted_result = self._sort_by_adjective(last_result, sort_key)
                    if sorted_result:
                        replacements[match.group(0)] = {
                            "type": "adjective_reference",
                            "adjective": adjective,
                            "item": item_type,
                            "value": sorted_result,
                        }
                        replaced_message = replaced_message.replace(
                            match.group(0),
                            f"[{adjective} {item_type}]"
                        )

        return replaced_message, replacements

    def _resolve_temporal(self, message: str) -> Tuple[str, Dict]:
        """
        Resolve temporal references: 'next week', 'tomorrow', 'same dates as last time', etc.

        Example:
        - "Next week" → today + 7 days
        - "Same dates as before" → last search dates
        - "This weekend" → next saturday + sunday
        """
        replaced_message = message
        replacements = {}

        now = timezone.now()
        user_tz = timezone.get_current_timezone()

        temporal_patterns = [
            (r'\bnext\s+(week|monday|tuesday|wednesday|thursday|friday|saturday|sunday|month)\b', 'next'),
            (r'\bthis\s+(week|weekend|month)\b', 'this'),
            (r'\b(today|tomorrow|yesterday)\b', 'relative'),
            (r'\bsame\s+(dates|days|times)\s+as\s+(last\w+|before|previous)\b', 'relative'),
        ]

        for pattern, ref_type in temporal_patterns:
            matches = re.finditer(pattern, message, re.IGNORECASE)
            for match in matches:
                resolved_date = self._parse_temporal_reference(match.group(0), now)
                if resolved_date:
                    replacements[match.group(0)] = {
                        "type": "temporal_reference",
                        "value": resolved_date,
                    }
                    replaced_message = replaced_message.replace(
                        match.group(0),
                        f"[{resolved_date.get('display', match.group(0))}]"
                    )

        return replaced_message, replacements

    # HELPER METHODS

    def _find_last_result_for_item(self, item: str) -> Optional[Dict]:
        """Find last search results for an item type (flights, hotels, etc.)."""
        # Look in context for last step result
        actions = ['search_flights', 'search_hotels', 'search_buses', 'search_transfers', 'search_events']

        for action in actions:
            for key, value in self.context.items():
                if action in key and isinstance(value, dict):
                    if value.get('status') == 'success':
                        return value.get('data', {})
        return None

    async def _get_user_contacts(self) -> List[Dict]:
        """Get user's contacts from Contact model first, then fall back to workspace members."""
        try:
            from chatbot.models import Contact, Chatroom
            from django.db.models import Q

            def _fetch():
                # 1. Try dedicated Contact model first
                qs = Contact.objects.filter(user_id=self.user_id)
                if self.room_id:
                    qs = qs.filter(Q(room__isnull=True) | Q(room_id=self.room_id))
                else:
                    qs = qs.filter(room__isnull=True)

                contacts = []
                for c in qs[:20]:
                    contacts.append({
                        'name': c.name,
                        'email': c.email or '',
                        'phone': c.phone or '',
                        'id': c.id,
                    })

                # 2. Fall back to workspace members if no dedicated contacts
                if not contacts:
                    try:
                        rooms = Chatroom.objects.filter(
                            participants__member__member__workspace=self.workspace
                        ).distinct()

                        for room in rooms:
                            for member in room.participants.all():
                                if member.member.id != self.user_id:
                                    contacts.append({
                                        'name': member.member.get_full_name() or member.member.username,
                                        'email': member.member.email,
                                        'id': member.member.id,
                                    })
                    except Exception:
                        pass

                return list({c.get('email', c.get('name', '')): c for c in contacts}.values())

            return await sync_to_async(_fetch)()
        except Exception:
            return []

    async def _get_user_preferences(self) -> Dict:
        """Get user's preferences."""
        try:
            from orchestration.workflow_planner import get_user_preferences
            return await sync_to_async(get_user_preferences)(self.user_id)
        except Exception:
            return {}

    def _sort_by_adjective(self, items: List, sort_key: str) -> Optional[Dict]:
        """Sort items by adjective (cheapest, earliest, etc.) and return top result."""
        if not items:
            return None

        if 'price' in sort_key:
            key_func = lambda x: x.get('price', float('inf'))
            reverse = 'max' in sort_key
            return sorted(items, key=key_func, reverse=reverse)[0]

        elif 'time' in sort_key:
            key_func = lambda x: x.get('departure', x.get('start_time', ''))
            reverse = 'max' in sort_key
            return sorted(items, key=key_func, reverse=reverse)[0]

        elif 'recent' in sort_key:
            return items[0] if items else None

        return items[0] if items else None

    def _parse_temporal_reference(self, temporal_ref: str, now: datetime) -> Optional[Dict]:
        """Parse temporal references into dates."""
        temporal_ref_lower = temporal_ref.lower()

        if 'today' in temporal_ref_lower:
            return {"date": now.date(), "display": "today"}
        elif 'tomorrow' in temporal_ref_lower:
            return {"date": (now + timedelta(days=1)).date(), "display": "tomorrow"}
        elif 'yesterday' in temporal_ref_lower:
            return {"date": (now - timedelta(days=1)).date(), "display": "yesterday"}
        elif 'next week' in temporal_ref_lower:
            return {
                "start": (now + timedelta(days=1)).date(),
                "end": (now + timedelta(days=7)).date(),
                "display": "next week"
            }
        elif 'this week' in temporal_ref_lower:
            return {
                "start": now.date(),
                "end": (now + timedelta(days=6)).date(),
                "display": "this week"
            }
        elif 'this weekend' in temporal_ref_lower:
            days_until_saturday = (5 - now.weekday()) % 7
            saturday = now + timedelta(days=days_until_saturday)
            sunday = saturday + timedelta(days=1)
            return {
                "start": saturday.date(),
                "end": sunday.date(),
                "display": "this weekend"
            }
        elif 'same dates as' in temporal_ref_lower or 'previous' in temporal_ref_lower:
            # Try to find last search dates from context
            for key, value in self.context.items():
                if 'search' in key and isinstance(value, dict):
                    if 'start_date' in value or 'date' in value:
                        return {
                            "date": value.get('date') or value.get('start_date'),
                            "display": "same dates as before"
                        }
            return None

        # Parse relative dates like "next monday", "next month"
        day_names = ['monday', 'tuesday', 'wednesday', 'thursday', 'friday', 'saturday', 'sunday']
        for i, day_name in enumerate(day_names):
            if day_name in temporal_ref_lower:
                days_ahead = i - now.weekday()
                if days_ahead <= 0:
                    days_ahead += 7
                return {
                    "date": (now + timedelta(days=days_ahead)).date(),
                    "display": f"next {day_name}"
                }

        return None


# Convenience function
async def resolve_request_references(user_id: int, room_id: int, message: str, context: Dict) -> Dict:
    """Resolve all vague references in a user request."""
    resolver = ReferenceResolver(user_id, room_id, context)
    return await resolver.resolve_references(message)


# Test cases (run with: pytest orchestration/tests/test_reference_resolver.py)
if __name__ == "__main__":
    print("Reference Resolver module loaded")
    print("Use: resolve_request_references(user_id, room_id, message, context)")
