"""
Data Synthesizer for Mathia Orchestration
Converts structured data from connectors into natural language responses
"""
import json
import logging
from typing import Dict, Any
import httpx

logger = logging.getLogger(__name__)


class DataSynthesizer:
    """
    Synthesizes natural language responses from structured data
    """
    
    def __init__(self):
        from .llm_client import get_llm_client
        self.llm = get_llm_client()
        
    async def synthesize(self, intent: Dict, result: Dict, use_llm: bool = True) -> str:
        """
        Convert result data into a user-friendly response
        """
        try:
            # 1. Basic formatting (fallback)
            basic_response = self._format_basic(intent, result)
            
            # 2. LLM Enhancement (if enabled)
            if use_llm:
                return await self._enhance_with_llm(intent, result, basic_response)
            
            return basic_response
            
        except Exception as e:
            logger.error(f"Synthesis error: {e}")
            return "I found the information but had trouble formatting it."

    def _format_basic(self, intent: Dict, result: Dict) -> str:
        """Basic template-based formatting"""
        action = intent.get("action")
        data = result.get("data", {})
        
        if action == "find_jobs":
            jobs = data.get("jobs", [])
            count = data.get("total", 0)
            return f"Found {count} jobs for '{data.get('query')}'. Top result: {jobs[0]['title'] if jobs else 'None'}"
            
        elif action == "check_payments":
            return f"Current balance: {data.get('currency')} {data.get('balance')}"
            
        elif action == "schedule_meeting":
            slots = data.get("slots", [])
            return f"Found {len(slots)} available slots. First available: {slots[0]['start'] if slots else 'None'}"
            
        elif action == "search_info":
            return data.get("summary", "Here is what I found.")
            
        return str(data)

    async def _enhance_with_llm(self, intent: Dict, result: Dict, basic_response: str) -> str:
        """Use LLM to make the response conversational"""
        try:
            system_prompt = """You are Mathia, a helpful personal assistant.
Convert the provided structured data into a natural, friendly response.
Keep it concise but informative.
Do not make up facts not present in the data.
If the data indicates an error, explain it clearly."""

            user_prompt = f"""
User Intent: {json.dumps(intent)}
System Data: {json.dumps(result)}
Basic Summary: {basic_response}

Please generate a natural response for the user.
"""
            response = await self.llm.generate_text(
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                temperature=0.7
            )
            
            return response
            
        except Exception as e:
            logger.error(f"LLM enhancement failed: {e}")
            return basic_response


_synthesizer = None

def get_synthesizer() -> DataSynthesizer:
    """Get or create the global synthesizer instance"""
    global _synthesizer
    if _synthesizer is None:
        _synthesizer = DataSynthesizer()
    return _synthesizer


async def synthesize_response(
    intent: Dict, 
    result: Dict, 
    use_llm: bool = False
) -> str:
    """
    Convenience function to synthesize a response
    
    Usage:
        from orchestration.data_synthesizer import synthesize_response
        
        response = await synthesize_response(intent, result, use_llm=True)
    """
    synth = get_synthesizer()
    return await synth.synthesize(intent, result, use_llm)