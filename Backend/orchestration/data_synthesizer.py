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
    Takes raw connector results and synthesizes natural language responses
    """
    
    def __init__(self):
        self.api_url = "https://api.anthropic.com/v1/messages"
        self.formatters = {
            "find_jobs": self._format_jobs,
            "schedule_meeting": self._format_calendar,
            "check_payments": self._format_payments,
            "search_info": self._format_search,
        }
    
    async def synthesize(
        self, 
        intent: Dict, 
        result: Dict, 
        use_llm: bool = False
    ) -> str:
        """
        Synthesize a natural language response
        
        Args:
            intent: Original parsed intent
            result: Data from MCP router
            use_llm: If True, use Claude API for natural response
            
        Returns:
            Natural language string
        """
        try:
            action = intent.get("action")
            
            # Use custom formatter if available
            formatter = self.formatters.get(action)
            if formatter:
                formatted = formatter(result["data"], intent)
                
                # Optionally enhance with LLM
                if use_llm:
                    return await self._enhance_with_llm(
                        intent["raw_query"],
                        formatted,
                        action
                    )
                
                return formatted
            
            # Fallback to JSON dump
            return f"Results:\n```json\n{json.dumps(result['data'], indent=2)}\n```"
            
        except Exception as e:
            logger.error(f"Synthesis error: {e}")
            return f"Got results but couldn't format them properly: {str(e)}"
    
    def _format_jobs(self, data: Dict, intent: Dict) -> str:
        """Format job listings into readable text"""
        jobs = data.get("jobs", [])
        query = data.get("query", "")
        total = data.get("total", 0)
        
        if not jobs:
            return f"🔍 No {query} jobs found matching your criteria."
        
        response = f"🎯 Found {total} {query} jobs:\n\n"
        
        for i, job in enumerate(jobs[:5], 1):  # Show top 5
            response += f"**{i}. {job['title']}**\n"
            response += f"   💰 Budget: {job['budget']}\n"
            response += f"   📅 Posted: {job['posted']}\n"
            response += f"   💬 {job['proposals']} proposals\n"
            response += f"   ⭐ Client rating: {job['client_rating']}/5\n"
            response += f"   🔗 {job['url']}\n\n"
        
        if total > 5:
            response += f"_...and {total - 5} more jobs_\n\n"
        
        response += "💡 **Next steps:**\n"
        response += "• Review the listings above\n"
        response += "• Want me to draft a proposal for any of these?\n"
        response += "• Say `@mathia tell me more about job 1`"
        
        return response
    
    def _format_calendar(self, data: Dict, intent: Dict) -> str:
        """Format calendar availability"""
        slots = data.get("slots", [])
        booking_url = data.get("booking_url", "")
        
        if not slots:
            return "📅 No available slots found."
        
        response = "📅 **Available meeting times:**\n\n"
        
        from datetime import datetime
        for i, slot in enumerate(slots, 1):
            start = datetime.fromisoformat(slot["start"])
            end = datetime.fromisoformat(slot["end"])
            
            response += f"**{i}.** {start.strftime('%A, %B %d')}\n"
            response += f"   🕐 {start.strftime('%I:%M %p')} - {end.strftime('%I:%M %p')}\n\n"
        
        response += f"🔗 Book here: {booking_url}\n\n"
        response += "💡 Say `@mathia book slot 1` to schedule"
        
        return response
    
    def _format_payments(self, data: Dict, intent: Dict) -> str:
        """Format payment information"""
        balance = data.get("balance", 0)
        currency = data.get("currency", "USD")
        payments = data.get("recent_payments", [])
        
        response = f"💰 **Account Balance:** ${balance:.2f} {currency}\n\n"
        response += "📊 **Recent Payments:**\n\n"
        
        for payment in payments:
            status_emoji = "✅" if payment["status"] == "completed" else "⏳"
            response += f"{status_emoji} **${payment['amount']:.2f}** - {payment['description']}\n"
            response += f"   Date: {payment['date']} | Status: {payment['status']}\n\n"
        
        return response
    
    def _format_search(self, data: Dict, intent: Dict) -> str:
        """Format search results"""
        results = data.get("results", [])
        query = data.get("query", "")
        
        if not results:
            return f"🔍 No results found for '{query}'"
        
        response = f"🔍 **Search results for '{query}':**\n\n"
        
        for i, result in enumerate(results, 1):
            response += f"**{i}. {result['title']}**\n"
            response += f"   {result['snippet']}\n"
            response += f"   🔗 {result['url']}\n\n"
        
        return response
    
    async def _enhance_with_llm(
        self, 
        original_query: str, 
        formatted_data: str,
        action: str
    ) -> str:
        """Use Claude to make response more natural and conversational"""
        
        prompt = f"""You are Mathia, a helpful personal assistant.

User asked: "{original_query}"

You performed action: {action}

Here's the structured data you found:
{formatted_data}

Task: Rewrite this into a natural, conversational response. Keep it:
- Friendly and concise
- Action-oriented (suggest next steps)
- Under 300 words

Response:"""

        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.post(
                    self.api_url,
                    headers={"Content-Type": "application/json"},
                    json={
                        "model": "claude-sonnet-4-20250514",
                        "max_tokens": 500,
                        "messages": [
                            {"role": "user", "content": prompt}
                        ]
                    }
                )
                
                data = response.json()
                
                # Extract text
                text = ""
                for block in data.get("content", []):
                    if block.get("type") == "text":
                        text += block.get("text", "")
                
                return text.strip()
                
        except Exception as e:
            logger.error(f"LLM enhancement failed: {e}")
            # Fallback to formatted data
            return formatted_data


# ============================================
# SINGLETON AND CONVENIENCE FUNCTIONS
# ============================================

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