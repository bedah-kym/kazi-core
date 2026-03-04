"""
Data Synthesizer for Mathia Orchestration
Converts structured data from connectors into natural language responses
"""
import json
import logging
from typing import Dict, Any
import httpx

logger = logging.getLogger(__name__)

from orchestration.user_preferences import format_style_prompt


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
            action = intent.get("action")
            
            # 1. Basic formatting (fallback)
            basic_response = self._format_basic(intent, result)
            
            # 2. Skip LLM for media/formatted actions (to preserve markdown)
            # These return pre-formatted messages with special syntax (images, etc)
            if action in ["search_gif", "get_weather", "convert_currency"]:
                return basic_response
            
            # 3. LLM Enhancement (if enabled for other actions)
            if use_llm:
                return await self._enhance_with_llm(intent, result, basic_response)
            
            return basic_response
            
        except Exception as e:
            import traceback
            logger.error(f"Synthesis error: {e}\n{traceback.format_exc()}")
            return "I found the information but had trouble formatting it."

    async def synthesize_stream(self, intent: Dict, result: Dict, use_llm: bool = True):
        """
        Stream the synthesized response
        """
        try:
            # 1. Basic formatting (fallback)
            basic_response = self._format_basic(intent, result)
            
            # 2. LLM Enhancement (if enabled)
            if use_llm:
                async for chunk in self._enhance_with_llm_stream(intent, result, basic_response):
                    yield chunk
                return
            
            # If not using LLM, simulate stream or just yield once
            yield basic_response
            
        except Exception as e:
            import traceback
            logger.error(f"Synthesis stream error: {e}\n{traceback.format_exc()}")
            yield "I found the information but had trouble formatting it."

    def _format_basic(self, intent: Dict, result: Dict) -> str:
        """Basic template-based formatting"""
        action = intent.get("action")
        data = result.get("data", {})
        receipt = result.get("receipt") if isinstance(result, dict) else None
        params = intent.get("parameters") or {}

        def _with_receipt(text: str) -> str:
            if isinstance(receipt, dict) and receipt.get("summary"):
                receipt_line = f"Receipt: {receipt.get('summary')}"
                undo_hint = receipt.get("undo_hint") or ""
                if undo_hint:
                    receipt_line = f"{receipt_line}\n{undo_hint}"
                if text:
                    return f"{text}\n\n{receipt_line}"
                return receipt_line
            return text
        
        if action == "find_jobs":
            jobs = data.get("jobs", [])
            count = data.get("total", 0)
            return _with_receipt(f"Found {count} jobs for '{data.get('query')}'. Top result: {jobs[0]['title'] if jobs else 'None'}")
            
        elif action == "check_payments":
            return _with_receipt(f"Current balance: {data.get('currency')} {data.get('balance')}")
            
        elif action == "schedule_meeting":
            slots = data.get("slots", [])
            return _with_receipt(f"Found {len(slots)} available slots. First available: {slots[0]['start'] if slots else 'None'}")
            
        elif action == "search_info":
            return _with_receipt(data.get("summary", "Here is what I found."))

        elif action == "check_quotas":
            summary = "Your current usage limits:\n"
            if not isinstance(data, dict):
                return _with_receipt(f"Error: Quota data not correctly formatted. Received: {type(data)}")
                
            for key, q in data.items():
                if isinstance(q, dict):
                    name = q.get('name', key.capitalize())
                    used = q.get('used', 0)
                    limit = q.get('limit', 0)
                    unit = q.get('unit', '')
                    status = q.get('status', 'unknown')
                    summary += f"- {name}: {used}/{limit} {unit} (Status: {status})\n"
                else:
                    summary += f"- {key}: {str(q)}\n"
            return _with_receipt(summary)

        elif action in ("send_email",):
            to_addr = params.get("to") or "recipient"
            subject = params.get("subject")
            if subject:
                return _with_receipt(f"Email sent to {to_addr} (subject: {subject}).")
            return _with_receipt(f"Email sent to {to_addr}.")

        elif action in ("send_whatsapp", "send_message"):
            phone = params.get("phone_number") or "recipient"
            status = data.get("status") or data.get("message") or "sent"
            return _with_receipt(f"WhatsApp {status} to {phone}.")

        elif action == "set_reminder":
            return _with_receipt(data.get("message", "Reminder set."))

        elif action == "create_payment_link":
            link = data.get("payment_link")
            if link:
                return _with_receipt(f"Payment link ready: {link}")
            return _with_receipt(data.get("message", "Payment link created."))

        elif action == "create_invoice":
            invoice = data.get("invoice") if isinstance(data, dict) else {}
            link = invoice.get("payment_link") if isinstance(invoice, dict) else None
            if link:
                return _with_receipt(f"Invoice created. Payment link: {link}")
            return _with_receipt(data.get("message", "Invoice created."))

        elif action == "withdraw":
            return _with_receipt(data.get("message", "Withdrawal initiated."))

        elif action == "book_travel_item":
            return _with_receipt(data.get("message", "Booking initiated."))
         
        # NEW: Handle GIF with message AND full URL
        elif action == "search_gif":
            url = data.get("url", "")
            message = data.get("message", "Here's a GIF!")
            return _with_receipt(f"{message}\n![GIF]({url})")
            
        return _with_receipt(str(data))

    async def _enhance_with_llm(self, intent: Dict, result: Dict, basic_response: str) -> str:
        """Use LLM to make the response conversational"""
        try:
            system_prompt = """You are Mathia, a helpful personal assistant.
Convert the provided structured data into a natural, friendly response.
Keep it concise but informative.
Do not make up facts not present in the data.
If the data indicates an error, explain it clearly."""
            style_prompt = format_style_prompt(intent.get("preferences") if isinstance(intent, dict) else None)
            if style_prompt:
                system_prompt = f"{system_prompt}\n{style_prompt}"

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

    async def _enhance_with_llm_stream(self, intent: Dict, result: Dict, basic_response: str):
        """Use LLM to make the response conversational (streaming)"""
        try:
            system_prompt = """You are Mathia, a helpful personal assistant.
Convert the provided structured data into a natural, friendly response.
Keep it concise but informative.
Do not make up facts not present in the data.
If the data indicates an error, explain it clearly."""
            style_prompt = format_style_prompt(intent.get("preferences") if isinstance(intent, dict) else None)
            if style_prompt:
                system_prompt = f"{system_prompt}\n{style_prompt}"

            user_prompt = f"""
User Intent: {json.dumps(intent)}
System Data: {json.dumps(result)}
Basic Summary: {basic_response}

Please generate a natural response for the user.
"""
            async for chunk in self.llm.stream_text(
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                temperature=0.7
            ):
                yield chunk
            
        except Exception as e:
            logger.error(f"LLM enhancement stream failed: {e}")
            yield basic_response


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
    """
    synth = get_synthesizer()
    return await synth.synthesize(intent, result, use_llm)


async def synthesize_response_stream(
    intent: Dict, 
    result: Dict, 
    use_llm: bool = False
):
    """
    Convenience function to stream a synthesized response
    """
    synth = get_synthesizer()
    async for chunk in synth.synthesize_stream(intent, result, use_llm):
        yield chunk
