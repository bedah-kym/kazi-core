"""
Intent Parser for Mathia Orchestration System
Converts natural language commands into structured JSON intents
"""
import json
import logging
from typing import Dict, Optional

logger = logging.getLogger(__name__)


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
        "general_chat"
    ]
    
    SYSTEM_PROMPT = """You are an intent classifier for Mathia, a personal assistant.
    
Your job: Parse user messages into structured JSON.

Supported actions:
- find_jobs: User wants to search for freelance work
- schedule_meeting: User wants to book time on calendar
- check_payments: User asks about payments/invoices
- search_info: User wants to look up information
- general_chat: Casual conversation

Return ONLY valid JSON in this format:
{
  "action": "find_jobs",
  "confidence": 0.95,
  "parameters": {
    "query": "Python",
    "budget_min": 200,
    "budget_max": 1000
  },
  "raw_query": "original user message"
}

Rules:
- Always include "action", "confidence" (0-1), "parameters", "raw_query"
- Extract relevant parameters from the message
- If unclear, use "general_chat" with low confidence
- For find_jobs: extract skills, budget range, urgency
- For schedule_meeting: extract preferred times, duration
- Be concise. No explanations outside JSON.
"""

    def __init__(self):
        from .llm_client import get_llm_client
        self.llm = get_llm_client()
        
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
            return self._validate_intent(intent, message)
            
        except Exception as e:
            logger.error(f"Intent parsing failed: {e}")
            # Fallback to general chat
            return {
                "action": "general_chat",
                "confidence": 0.3,
                "parameters": {},
                "raw_query": message,
                "error": str(e)
            }
    
    def _build_user_prompt(self, message: str, context: Optional[Dict]) -> str:
        """Build the user prompt with context"""
        prompt = f'User message: "{message}"'
        
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
                "raw_query": original_message
            }
        
        if "confidence" not in intent:
            intent["confidence"] = 0.5
            
        if "parameters" not in intent:
            intent["parameters"] = {}
            
        if "raw_query" not in intent:
            intent["raw_query"] = original_message
        
        # Clamp confidence
        try:
            intent["confidence"] = max(0.0, min(1.0, float(intent["confidence"])))
        except (ValueError, TypeError):
            intent["confidence"] = 0.5
        
        # Validate action is supported
        if intent["action"] not in self.SUPPORTED_ACTIONS:
            logger.warning(f"Unknown action: {intent['action']}, defaulting to general_chat")
            intent["action"] = "general_chat"
            intent["confidence"] *= 0.5
        
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