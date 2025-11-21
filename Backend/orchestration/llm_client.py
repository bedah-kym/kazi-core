"""
LLM Client for Mathia Orchestration
Handles communication with LLM providers (Anthropic, Hugging Face) with fallback logic.
"""
import os
import json
import logging
import httpx
from typing import Dict, List, Optional, Any, Union
from django.conf import settings

logger = logging.getLogger(__name__)

class LLMClient:
    """
    Unified client for LLM interactions.
    Prioritizes Anthropic (Claude) if available, falls back to Hugging Face.
    """
    
    def __init__(self):
        self.anthropic_key = getattr(settings, 'ANTHROPIC_API_KEY', os.environ.get('ANTHROPIC_API_KEY'))
        self.hf_key = getattr(settings, 'HF_API_TOKEN', os.environ.get('HF_API_TOKEN'))
        
        # Models
        self.claude_model = "claude-3-sonnet-20240229" 
        self.hf_model = "meta-llama/Llama-3.1-8B-Instruct"  # router-friendly chat model

        # Endpoints
        self.anthropic_url = "https://api.anthropic.com/v1/messages"
        self.hf_url = "https://router.huggingface.co/v1/chat/completions"

    async def generate_text(
        self, 
        system_prompt: str, 
        user_prompt: str, 
        temperature: float = 0.7,
        max_tokens: int = 1000,
        json_mode: bool = False
    ) -> str:
        """
        Generate text using available LLM provider.
        """
        # Try Claude first
        if self.anthropic_key:
            try:
                return await self._call_claude(system_prompt, user_prompt, temperature, max_tokens)
            except Exception as e:
                logger.warning(f"Claude API failed: {e}. Falling back to Hugging Face.")
        
        # Fallback to Hugging Face
        if self.hf_key:
            try:
                return await self._call_huggingface(system_prompt, user_prompt, temperature, max_tokens, json_mode)
            except Exception as e:
                logger.error(f"Hugging Face API failed: {e}")
                raise Exception(f"All LLM providers failed. Last error: {e}")
        
        raise Exception("No valid API keys configured for Anthropic or Hugging Face.")

    async def _call_claude(self, system_prompt: str, user_prompt: str, temperature: float, max_tokens: int) -> str:
        """Call Anthropic API"""
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(
                self.anthropic_url,
                headers={
                    "x-api-key": self.anthropic_key,
                    "anthropic-version": "2023-06-01",
                    "content-type": "application/json"
                },
                json={
                    "model": self.claude_model,
                    "max_tokens": max_tokens,
                    "temperature": temperature,
                    "system": system_prompt,
                    "messages": [
                        {"role": "user", "content": user_prompt}
                    ]
                }
            )
            
            if response.status_code != 200:
                raise Exception(f"Anthropic Error {response.status_code}: {response.text}")
                
            data = response.json()
            return data["content"][0]["text"]

    async def _call_huggingface(self, system_prompt: str, user_prompt: str, temperature: float, max_tokens: int, json_mode: bool) -> str:
        """Call Hugging Face Router (OpenAI-compatible /v1/chat/completions API)"""

        # Build messages array in OpenAI/ChatML style
        messages = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})

        user_content = user_prompt
        if json_mode:
            # Nudge the model to answer with pure JSON
            user_content += "\n\nRespond with valid JSON only."

        messages.append({"role": "user", "content": user_content})

        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(
                self.hf_url,
                headers={
                    "Authorization": f"Bearer {self.hf_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": self.hf_model,
                    "messages": messages,
                    "max_tokens": max_tokens,
                    "temperature": temperature,
                },
            )

            if response.status_code != 200:
                raise Exception(f"HF Error {response.status_code}: {response.text}")

            data = response.json()

            # HF's OpenAI-compatible response format:
            # {
            #   "choices": [
            #       {
            #           "message": {
            #               "role": "assistant",
            #               "content": "..."
            #           },
            #           ...
            #       }
            #   ],
            #   ...
            # }
            try:
                return (
                    data["choices"][0]["message"]["content"]
                    .strip()
                )
            except Exception:
                # Fallback: just return the raw data stringified
                return str(data)

    def extract_json(self, text: str) -> Dict:
        """
        Robust JSON extraction from LLM output.
        Handles markdown blocks, trailing text, etc.
        """
        try:
            # 1. Try direct parse
            return json.loads(text)
        except json.JSONDecodeError:
            pass
            
        # 2. Strip markdown code blocks
        clean_text = text.strip()
        if "```json" in clean_text:
            parts = clean_text.split("```json")
            if len(parts) > 1:
                clean_text = parts[1].split("```")[0]
        elif "```" in clean_text:
            parts = clean_text.split("```")
            if len(parts) > 1:
                clean_text = parts[1]
                
        # 3. Try finding first { and last }
        try:
            start = clean_text.find("{")
            end = clean_text.rfind("}")
            if start != -1 and end != -1:
                json_str = clean_text[start:end+1]
                return json.loads(json_str)
        except Exception:
            pass
            
        logger.error(f"Failed to extract JSON from: {text[:100]}...")
        return {}

# Singleton
_client = None

def get_llm_client() -> LLMClient:
    global _client
    
    if _client is None:
        _client = LLMClient()
    return _client
