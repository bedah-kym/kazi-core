"""
Intent Parser for Mathia Orchestration System
Converts natural language commands into structured JSON intents
"""
import json
import logging
import re
from typing import Dict, Optional, Any

from workflows.capabilities import SYSTEM_CAPABILITIES

logger = logging.getLogger(__name__)

_LOW_CONFIDENCE_THRESHOLD = 0.45
_ACTION_SCHEMA_ALIASES = {
    "send_whatsapp": "send_message",
}


class IntentParser:
    """
    Parses user messages and extracts structured intent using LLMClient
    """
    
    # Define supported actions
    SUPPORTED_ACTIONS = [
        "find_jobs",
        "schedule_meeting", 
        "check_payments",
        "search_info",
        "general_chat",
        "get_weather",
        "search_gif",
        "convert_currency",
        "set_reminder",
        "send_email",
        "send_whatsapp",
        "create_invoice",
        "create_workflow",
        # Travel planner actions
        "search_buses",
        "search_hotels",
        "search_flights",
        "search_transfers",
        "search_events",
        "create_itinerary",
        "view_itinerary",
        "add_to_itinerary",
        "book_travel_item",
        "check_quotas",
    ]
    
    SYSTEM_PROMPT = """You are an intent classifier for Mathia, a personal assistant with travel planning.
    
Your job: Parse user messages into structured JSON.

Supported actions:
- find_jobs: User wants to search for freelance work on platforms like Upwork
- schedule_meeting: User wants to CHECK CALENDAR AVAILABILITY or SCHEDULE A MEETING
  Examples: "is my calendar free?", "what meetings do I have?", "check my availability"
- check_payments: User asks about payments, invoices, or financial transactions
- search_info: User wants to look up information on the web
- get_weather: User asks about weather in a city
- search_gif: User wants a GIF
- convert_currency: User wants currency conversion
- set_reminder: User wants to set a reminder (extract content, time, priority)
- send_email: User wants to send an email via their connected Gmail account
  Examples: "email alex@example.com saying hi", "send an email to ops with subject X", "mail me the report"
- send_whatsapp: User wants to send a WhatsApp message via system account
  Examples: "send a whatsapp to +2547xxxx saying hello", "whatsapp my team the update"
- create_workflow: User wants to create or edit an automated workflow\n  Examples: "create a workflow to email me when a payment completes", "automate reminders every Friday"\n- check_quotas: User asks about their usage limits, remaining searches, message count, or upload status.
  Examples: "show my quotas", "how many searches left?", "what are my limits?", "usage status"
- general_chat: Casual conversation, greetings, or unclear requests

TRAVEL PLANNER ACTIONS:
- search_buses: User wants to find bus tickets
  Examples: "buses from Nairobi to Mombasa", "find a bus for tomorrow", "intercity buses on Dec 25"
  Extract: origin, destination, travel_date, return_date (optional), passengers (default 1)
- search_hotels: User wants to find hotels
  Examples: "hotels in Mombasa", "find accommodation in Nairobi for 3 nights", "5-star hotel in Diani"
  Extract: location, check_in_date, check_out_date, guests (default 1), budget_ksh (optional)
- search_flights: User wants to find flights
  Examples: "flights from Nairobi to London", "airfare to Zanzibar on Dec 25", "round-trip flights"
  Extract: origin, destination, departure_date, return_date (optional), passengers (default 1)
- search_transfers: User wants airport/ground transfers
  Examples: "taxi from airport to hotel", "transfer from Nairobi airport", "car rental in Mombasa"
  Extract: origin, destination, travel_date, passengers (default 1)
- search_events: User wants to find events/activities
  Examples: "events in Nairobi", "concerts next month", "things to do in Mombasa on Dec 25"
  Extract: location, event_date (optional), category (music, sports, cultural, etc., optional)
- create_itinerary: User wants to create a new trip itinerary
  Examples: "plan my trip to Mombasa", "create an itinerary for Kenya", "I'm going to Africa, help me plan"
  Extract: destination, start_date, end_date, budget_ksh (optional), description (optional)
- view_itinerary: User wants to see their existing itinerary
  Examples: "show my trip", "my itinerary", "what have I planned?"
  Extract: (none required, user_id inferred from context)
- add_to_itinerary: User wants to add a booking to an existing itinerary
  Examples: "add this hotel to my trip", "save this flight to my itinerary"
  Extract: itinerary_id, item_type (bus|hotel|flight|transfer|event), item_id, provider
- book_travel_item: User ready to book (redirects to provider)
  Examples: "book this hotel", "complete the flight booking", "reserve the bus"
  Extract: item_type, item_id, provider

Return ONLY valid JSON in this format:
{
  "action": "search_hotels",
  "confidence": 0.95,
  "parameters": {
    "location": "Nairobi",
    "check_in_date": "2025-12-25",
    "check_out_date": "2025-12-28",
    "guests": 2
  },
  "missing_slots": [],
  "clarifying_question": "",
  "raw_query": "original user message"
}

Rules:
- Always include "action", "confidence" (0-1), "parameters", "missing_slots", "clarifying_question", "raw_query"
- Extract relevant parameters from the message
- If unclear, use "general_chat" with low confidence
- For travel searches: extract location/origin/destination, dates, passengers/guests, budget if mentioned
- Dates should be extracted as YYYY-MM-DD format if possible, or raw string if user said "tomorrow", "next week", etc.
- If required details are missing, list the param names in "missing_slots" and ask for only one missing detail in "clarifying_question"
- Be concise. No explanations outside JSON.
"""

    def __init__(self):
        from .llm_client import get_llm_client
        self.llm = get_llm_client()
        self._action_index = self._build_action_index(SYSTEM_CAPABILITIES)

    def _build_action_index(self, capabilities: Dict) -> Dict[str, Dict[str, Dict]]:
        index: Dict[str, Dict[str, Dict]] = {}
        for service in capabilities.get("integrations", []):
            service_name = service.get("service")
            if not service_name:
                continue
            for action in service.get("actions", []):
                action_name = action.get("name")
                if not action_name:
                    continue
                if action_name not in index:
                    index[action_name] = {
                        "service": service_name,
                        "params": action.get("params") or {},
                    }
        return index

    def _missing_param_message(self, param: str) -> str:
        prompts = {
            "departure_date": "What departure date should I use? (YYYY-MM-DD)",
            "travel_date": "What travel date should I use? (YYYY-MM-DD)",
            "check_in_date": "What is the check-in date? (YYYY-MM-DD)",
            "check_out_date": "What is the check-out date? (YYYY-MM-DD)",
            "origin": "What is the origin city or airport code?",
            "destination": "What is the destination city or airport code?",
            "location": "Which city should I search in?",
            "item_id": "Which option should I book? You can say things like 'book flight 1'.",
            "to": "Which email address should I send this to?",
            "text": "What should the email say?",
            "message": "What should the message say?",
            "content": "What should the reminder say?",
            "time": "When should I set the reminder?",
            "phone_number": "Which phone number should I use?",
        }
        return prompts.get(param, f"I need the {param} to proceed.")

    def _compute_missing_slots(self, intent: Dict) -> Dict[str, Any]:
        action = str(intent.get("action") or "").strip()
        if not action or action == "general_chat":
            return {"missing_slots": [], "clarifying_question": ""}
        lookup_action = _ACTION_SCHEMA_ALIASES.get(action, action)
        action_def = self._action_index.get(lookup_action)
        if not action_def:
            return {"missing_slots": [], "clarifying_question": ""}
        params = intent.get("parameters") or {}
        if not isinstance(params, dict):
            params = {}
        missing = []
        for param_name, spec in (action_def.get("params") or {}).items():
            if spec.get("required") and not params.get(param_name):
                missing.append(param_name)
        if not missing:
            return {"missing_slots": [], "clarifying_question": ""}
        question = self._missing_param_message(str(missing[0]))
        return {"missing_slots": missing, "clarifying_question": question}

    def _rule_based_email_intent(self, message: str) -> Optional[Dict]:
        if not message:
            return None
        if not re.search(r"\b(send|email|mail)\b", message, flags=re.IGNORECASE):
            return None
        email_match = re.search(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}", message)
        if not email_match:
            return None
        to_email = email_match.group(0)

        subject = None
        subject_match = re.search(r'\bsubject\b[:\-]?\s*"?([^"\n]+)', message, flags=re.IGNORECASE)
        if subject_match:
            subject = subject_match.group(1).strip().strip('"').strip()

        body = None
        body_match = re.search(r'\b(?:saying|message|msg|text|body)\b[:\-]?\s*"?(.+)', message, flags=re.IGNORECASE)
        if body_match:
            body = body_match.group(1).strip().strip('"').strip()
        if not body:
            quoted = re.search(r'"([^"\n]+)"', message)
            if quoted:
                body = quoted.group(1).strip()

        params = {"to": to_email}
        if subject:
            params["subject"] = subject
        if body:
            params["text"] = body

        return {
            "action": "send_email",
            "confidence": 0.85,
            "parameters": params,
            "raw_query": message,
        }
        
    async def parse(self, message: str, user_context: Optional[Dict] = None) -> Dict:
        """
        Parse a natural language message into structured intent
        """
        try:
            # Build context-aware prompt
            user_prompt = self._build_user_prompt(message, user_context)
            
            # Call LLM
            response_text = await self.llm.generate_text(
                system_prompt=self.SYSTEM_PROMPT,
                user_prompt=user_prompt,
                temperature=0.1, # Low temp for deterministic JSON
                json_mode=True
            )
            
            # Parse response
            intent = self.llm.extract_json(response_text)
            
            # Validate and return
            intent = self._validate_intent(intent, message)
            intent = self._postprocess_intent(intent)

            if intent.get("confidence", 0.0) < _LOW_CONFIDENCE_THRESHOLD or intent.get("action") == "general_chat":
                rule_based = self._rule_based_email_intent(message)
                if rule_based:
                    intent = self._postprocess_intent(rule_based)
            return intent
            
        except Exception as e:
            logger.error(f"Intent parsing failed: {e}")
            rule_based = self._rule_based_email_intent(message)
            if rule_based:
                return self._postprocess_intent(rule_based)
            # Fallback to general chat
            return {
                "action": "general_chat",
                "confidence": 0.3,
                "parameters": {},
                "missing_slots": [],
                "clarifying_question": "",
                "raw_query": message,
                "error": str(e)
            }
    
    def _build_user_prompt(self, message: str, context: Optional[Dict]) -> str:
        """Build the user prompt with context"""
        from django.utils import timezone
        
        prompt = f'Current Time: {timezone.now().isoformat()}\n'
        
        if context and context.get('history'):
            prompt += f"CONVERSATION HISTORY (Most recent last):\n{context['history']}\n\n"
            
        prompt += f'User message: "{message}"'
        
        if context:
            prompt += f'\n\nUser context: {json.dumps(context)}'
            
        return prompt
    
    def _validate_intent(self, intent: Dict, original_message: str) -> Dict:
        """Validate and normalize the parsed intent"""
        # Ensure required fields
        if not intent or "action" not in intent:
            logger.warning(f"Invalid intent structure: {intent}")
            return {
                "action": "general_chat",
                "confidence": 0.0,
                "parameters": {},
                "missing_slots": [],
                "clarifying_question": "",
                "raw_query": original_message
            }
        
        if "confidence" not in intent:
            intent["confidence"] = 0.5
            
        if "parameters" not in intent:
            intent["parameters"] = {}
            
        if "raw_query" not in intent:
            intent["raw_query"] = original_message

        if "missing_slots" not in intent:
            intent["missing_slots"] = []
        if "clarifying_question" not in intent:
            intent["clarifying_question"] = ""
        
        # Clamp confidence
        try:
            intent["confidence"] = max(0.0, min(1.0, float(intent["confidence"])))
        except (ValueError, TypeError):
            intent["confidence"] = 0.5

        if intent.get("action") == "send_message":
            intent["action"] = "send_whatsapp"
        
        # Validate action is supported
        if intent["action"] not in self.SUPPORTED_ACTIONS:
            logger.warning(f"Unknown action: {intent['action']}, defaulting to general_chat")
            intent["action"] = "general_chat"
            intent["confidence"] *= 0.5
        
        return intent

    def _postprocess_intent(self, intent: Dict) -> Dict:
        if not isinstance(intent, dict):
            return {
                "action": "general_chat",
                "confidence": 0.0,
                "parameters": {},
                "missing_slots": [],
                "clarifying_question": "",
                "raw_query": "",
            }
        missing_slots = intent.get("missing_slots")
        if not isinstance(missing_slots, list):
            missing_slots = []
        clarifying_question = str(intent.get("clarifying_question") or "").strip()

        computed = self._compute_missing_slots(intent)
        if not missing_slots:
            missing_slots = computed.get("missing_slots") or []
        if not clarifying_question and missing_slots:
            clarifying_question = computed.get("clarifying_question") or ""

        intent["missing_slots"] = missing_slots
        intent["clarifying_question"] = clarifying_question
        return intent


# Singleton instance
_parser = None

def get_intent_parser() -> IntentParser:
    """Get or create the global intent parser instance"""
    global _parser
    if _parser is None:
        _parser = IntentParser()
    return _parser


# Convenience function for use in consumers
async def parse_intent(message: str, user_context: Optional[Dict] = None) -> Dict:
    """
    Parse a message into structured intent
    """
    parser = get_intent_parser()
    return await parser.parse(message, user_context)
