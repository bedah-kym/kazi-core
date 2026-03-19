"""
LLM Client for Mathia Orchestration
Handles communication with LLM providers (Anthropic, Hugging Face) with fallback logic.
Includes rate limiting and token budget enforcement for cost control.
"""
import os
import json
import threading
import logging
import httpx
import hashlib
from typing import Dict, List, Optional, Any, Union
from django.conf import settings
from django.core.cache import cache

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
        self.claude_model = "claude-sonnet-4-6"
        self.hf_model = "meta-llama/Llama-3.1-8B-Instruct"  # router-friendly chat model

        # Endpoints
        self.anthropic_url = "https://api.anthropic.com/v1/messages"
        self.hf_url = "https://router.huggingface.co/v1/chat/completions"

    def _provider_order(self, model_role: str, provider_preference: Optional[str]) -> List[str]:
        role = (model_role or "default").lower()
        preference = (provider_preference or "").lower().strip()
        if preference:
            ordered = [preference, "anthropic", "huggingface"]
        elif role == "planner":
            ordered = [getattr(settings, "LLM_PLANNER_PROVIDER", "anthropic").lower(), "anthropic", "huggingface"]
        elif role == "executor":
            ordered = [getattr(settings, "LLM_EXECUTOR_PROVIDER", "huggingface").lower(), "huggingface", "anthropic"]
        else:
            ordered = ["anthropic", "huggingface"]
        # De-dup while preserving order
        seen = set()
        result: List[str] = []
        for item in ordered:
            if item in ("anthropic", "huggingface") and item not in seen:
                result.append(item)
                seen.add(item)
        return result

    def _model_for(self, provider: str, model_role: str) -> str:
        role = (model_role or "default").lower()
        if provider == "anthropic":
            if role == "planner":
                return getattr(settings, "LLM_PLANNER_MODEL", self.claude_model)
            if role == "executor":
                return getattr(settings, "LLM_EXECUTOR_MODEL", self.claude_model)
            return self.claude_model
        if role == "planner":
            return getattr(settings, "LLM_PLANNER_MODEL", self.hf_model)
        if role == "executor":
            return getattr(settings, "LLM_EXECUTOR_MODEL", self.hf_model)
        return self.hf_model

    def _estimate_tokens(self, text: Optional[str]) -> int:
        """Rough token estimation: ~4 chars per token."""
        if not text:
            return 0
        return max(1, len(text) // 4)

    def _message_content_to_text(self, content: Any) -> str:
        """Flatten Anthropic-style message content blocks into plain text."""
        if isinstance(content, str):
            return content
        if not isinstance(content, list):
            return str(content or "")

        parts = []
        for block in content:
            if not isinstance(block, dict):
                parts.append(str(block))
                continue
            block_type = block.get("type")
            if block_type == "text":
                parts.append(str(block.get("text", "")))
            elif block_type == "tool_result":
                payload = block.get("content", "")
                if not isinstance(payload, str):
                    payload = json.dumps(payload, default=str)
                parts.append(f"[tool_result] {payload}")
            elif block_type == "tool_use":
                name = block.get("name", "tool")
                tool_input = block.get("input", {})
                parts.append(
                    f"[tool_use request] {name} {json.dumps(tool_input, default=str)}"
                )
            else:
                parts.append(str(block))
        return "\n".join(part for part in parts if part)

    def _messages_to_plain_text(self, messages: List[Dict[str, Any]]) -> str:
        """Convert structured chat history to a plain transcript for fallback models."""
        lines = []
        for msg in messages:
            role = str(msg.get("role", "user")).upper()
            text = self._message_content_to_text(msg.get("content", ""))
            if text:
                lines.append(f"{role}: {text}")
        return "\n\n".join(lines)

    async def _create_hf_fallback_message(
        self,
        *,
        messages: List[Dict[str, Any]],
        system: str,
        temperature: float,
        max_tokens: int,
        user_id: Optional[int],
        model: Optional[str] = None,
        tools: Optional[List[Dict[str, Any]]] = None,
    ) -> Dict[str, Any]:
        """Fallback for create_message when Anthropic is unavailable."""
        prompt = self._messages_to_plain_text(messages)
        fallback_system = system or "You are Mathia, a helpful AI assistant."
        if tools:
            fallback_system += (
                "\n\nTool execution is currently unavailable in this mode. "
                "Answer directly, and briefly mention any missing external actions."
            )

        response_text = await self._call_huggingface(
            fallback_system,
            prompt,
            temperature,
            max_tokens,
            False,
            model_name=model or self._model_for("huggingface", "executor"),
        )

        input_tokens = self._estimate_tokens(fallback_system) + self._estimate_tokens(prompt)
        output_tokens = self._estimate_tokens(response_text)
        self._record_token_usage(input_tokens + output_tokens, user_id)

        return {
            "id": "hf-fallback-message",
            "type": "message",
            "role": "assistant",
            "content": [{"type": "text", "text": response_text}],
            "stop_reason": "end_turn",
            "usage": {
                "input_tokens": input_tokens,
                "output_tokens": output_tokens,
            },
        }

    def _get_user_token_budget(self, user_id: Optional[int]) -> Dict[str, int]:
        """
        Get token budget and current usage for a user.
        Uses atomic integer counter in cache for thread-safe tracking.
        Returns {"limit": N, "used": M}
        """
        if not user_id:
            return {"limit": 999999, "used": 0}

        cache_key = f"llm_tokens:{user_id}"
        used = cache.get(cache_key, 0)
        limit = int(getattr(settings, "LLM_TOKEN_LIMIT_PER_USER_PER_HOUR", 50000))
        return {
            "limit": limit,
            "used": int(used),
        }

    async def _check_token_quota(self, estimated_tokens: int, user_id: Optional[int]) -> bool:
        """
        Check if user has enough token budget remaining.
        Returns True if under quota, False if exceeded.
        """
        if not user_id:
            return True  # No tracking for anonymous

        budget = self._get_user_token_budget(user_id)
        if budget["used"] + estimated_tokens > budget["limit"]:
            logger.warning(
                f"Token quota exceeded for user {user_id}: "
                f"used {budget['used']} + {estimated_tokens} > limit {budget['limit']}"
            )
            return False
        return True

    def _record_token_usage(self, tokens: int, user_id: Optional[int]) -> None:
        """Record token usage for rate limiting — atomic increment."""
        if not user_id or tokens <= 0:
            return

        cache_key = f"llm_tokens:{user_id}"
        ttl = 3600  # 1 hour
        try:
            cache.incr(cache_key, tokens)
        except ValueError:
            # Key doesn't exist yet — create with initial value
            cache.set(cache_key, tokens, ttl)

    async def generate_text(
        self,
        system_prompt: str,
        user_prompt: str,
        temperature: float = 0.7,
        max_tokens: int = 600,
        json_mode: bool = False,
        user_id: Optional[int] = None,
        room_id: Optional[int] = None,
        model_role: str = "default",
        provider_preference: Optional[str] = None,
    ) -> str:
        """
        Generate text using available LLM provider.

        Args:
            system_prompt: System context for the LLM
            user_prompt: User input
            temperature: Sampling temperature (0.0-1.0)
            max_tokens: Max tokens in response
            json_mode: If True, request JSON output
            user_id: User ID for cache isolation (prevent cache poisoning)
            room_id: Room ID for cache isolation

        Raises:
            Exception: If token quota exceeded or all LLM providers fail
        """
        # Estimate token cost BEFORE making request
        estimated_tokens = self._estimate_tokens(system_prompt) + self._estimate_tokens(user_prompt)
        if not await self._check_token_quota(estimated_tokens, user_id):
            raise Exception(
                f"LLM token quota exceeded for user {user_id}. "
                f"Please try again later or contact support."
            )

        max_tokens = min(max_tokens, getattr(settings, 'LLM_MAX_TOKENS', 700))
        user_prompt = self._truncate(user_prompt)
        system_prompt = self._truncate(system_prompt, is_system=True)
        cache_key = None
        if self._should_cache(json_mode=json_mode, temperature=temperature):
            cache_key = self._cache_key(
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                temperature=temperature,
                max_tokens=max_tokens,
                json_mode=json_mode,
                user_id=user_id,
                room_id=room_id,
                model_role=model_role,
                provider_preference=provider_preference,
            )
            cached = cache.get(cache_key)
            if cached:
                return cached
        provider_order = self._provider_order(model_role, provider_preference)
        last_error: Optional[Exception] = None
        for provider in provider_order:
            if provider == "anthropic" and self.anthropic_key:
                try:
                    model_name = self._model_for("anthropic", model_role)
                    response = await self._call_claude(system_prompt, user_prompt, temperature, max_tokens, model_name)
                    self._record_token_usage(estimated_tokens, user_id)
                    self._store_cache(cache_key, response)
                    return response
                except Exception as e:
                    last_error = e
                    logger.warning(f"Claude API failed: {e}. Falling back.")
            if provider == "huggingface" and self.hf_key:
                try:
                    model_name = self._model_for("huggingface", model_role)
                    response = await self._call_huggingface(system_prompt, user_prompt, temperature, max_tokens, json_mode, model_name)
                    self._record_token_usage(estimated_tokens, user_id)
                    self._store_cache(cache_key, response)
                    return response
                except Exception as e:
                    last_error = e
                    logger.error(f"Hugging Face API failed: {e}")

        if last_error:
            raise Exception(f"All LLM providers failed. Last error: {last_error}")

        raise Exception("No valid API keys configured for Anthropic or Hugging Face.")

    async def stream_text(
        self, 
        system_prompt: str, 
        user_prompt: str, 
        temperature: float = 0.7,
        max_tokens: int = 600,
        model_role: str = "default",
        provider_preference: Optional[str] = None,
    ):
        """
        Stream text generation. Yields chunks of text.
        """
        max_tokens = min(max_tokens, getattr(settings, 'LLM_MAX_TOKENS', 700))
        user_prompt = self._truncate(user_prompt)
        system_prompt = self._truncate(system_prompt, is_system=True)

        provider_order = self._provider_order(model_role, provider_preference)
        last_error: Optional[Exception] = None
        for provider in provider_order:
            if provider == "anthropic" and self.anthropic_key:
                try:
                    model_name = self._model_for("anthropic", model_role)
                    async for chunk in self._call_claude_stream(system_prompt, user_prompt, temperature, max_tokens, model_name):
                        if chunk:
                            yield chunk
                    return
                except Exception as e:
                    last_error = e
                    logger.warning(f"Claude API failed: {e}. Falling back.")
            if provider == "huggingface" and self.hf_key:
                try:
                    model_name = self._model_for("huggingface", model_role)
                    async for chunk in self._stream_huggingface(system_prompt, user_prompt, temperature, max_tokens, model_name):
                        yield chunk
                    return
                except Exception as e:
                    last_error = e
                    logger.error(f"Hugging Face API failed: {e}")

        if last_error:
            raise Exception(f"All LLM providers failed. Last error: {last_error}")

        raise Exception("No valid API keys configured for Anthropic or Hugging Face.")

    # ------------------------------------------------------------------ #
    #  Agent loop methods — full Messages API with tool_use support       #
    # ------------------------------------------------------------------ #

    async def create_message(
        self,
        *,
        messages: List[Dict[str, Any]],
        system: str = "",
        tools: Optional[List[Dict[str, Any]]] = None,
        temperature: float = 0.7,
        max_tokens: int = 4096,
        user_id: Optional[int] = None,
        model: Optional[str] = None,
        use_prompt_cache: bool = False,
    ) -> Dict[str, Any]:
        """
        Call the Anthropic Messages API with full tool_use support.

        Args:
            model: Override model (e.g. "claude-haiku-4-5-20251001" for simple tasks).
            use_prompt_cache: If True, wraps the system prompt with cache_control
                for Anthropic prompt caching (~90% cost reduction on cached tokens).

        Returns the raw response dict with keys:
            content: List of content blocks (text and/or tool_use)
            stop_reason: "end_turn" | "tool_use" | "max_tokens"
            usage: {input_tokens, output_tokens, cache_creation_input_tokens, cache_read_input_tokens}
        """
        if not self.anthropic_key and not self.hf_key:
            raise Exception("No valid API keys configured for Anthropic or Hugging Face.")

        estimated_tokens = sum(
            self._estimate_tokens(
                m.get("content") if isinstance(m.get("content"), str) else str(m.get("content", ""))
            )
            for m in messages
        ) + self._estimate_tokens(system)

        if not await self._check_token_quota(estimated_tokens, user_id):
            raise Exception(
                f"LLM token quota exceeded for user {user_id}. "
                f"Please try again later."
            )

        if self.anthropic_key:
            body: Dict[str, Any] = {
                "model": model or self.claude_model,
                "max_tokens": max_tokens,
                "temperature": temperature,
                "messages": messages,
            }
            if system:
                if use_prompt_cache:
                    # Structured system prompt with cache_control for prompt caching
                    body["system"] = [
                        {
                            "type": "text",
                            "text": system,
                            "cache_control": {"type": "ephemeral"},
                        }
                    ]
                else:
                    body["system"] = system
            if tools:
                if use_prompt_cache and tools:
                    # Mark the last tool with cache_control so the entire
                    # system prompt + tool definitions block is cached together.
                    cached_tools = [dict(t) for t in tools]
                    last = cached_tools[-1]
                    # Only add cache_control to regular tool defs (not server tools)
                    if "input_schema" in last:
                        last["cache_control"] = {"type": "ephemeral"}
                    body["tools"] = cached_tools
                else:
                    body["tools"] = tools

            try:
                async with httpx.AsyncClient(timeout=120.0) as client:
                    response = await client.post(
                        self.anthropic_url,
                        headers={
                            "x-api-key": self.anthropic_key,
                            "anthropic-version": "2023-06-01",
                            "content-type": "application/json",
                        },
                        json=body,
                    )
                    if response.status_code != 200:
                        raise Exception(f"Anthropic Error {response.status_code}: {response.text}")

                    data = response.json()

                output_tokens = data.get("usage", {}).get("output_tokens", 0)
                input_tokens = data.get("usage", {}).get("input_tokens", 0)
                self._record_token_usage(input_tokens + output_tokens, user_id)

                return data
            except Exception as exc:
                if not self.hf_key:
                    raise
                logger.warning(
                    "Anthropic create_message failed; using Hugging Face fallback: %s",
                    exc,
                )

        # Fallback path (no Anthropic key or Anthropic call failed)
        return await self._create_hf_fallback_message(
            messages=messages,
            system=system,
            temperature=temperature,
            max_tokens=max_tokens,
            user_id=user_id,
            model=model,
            tools=tools,
        )

    async def stream_message(
        self,
        *,
        messages: List[Dict[str, Any]],
        system: str = "",
        tools: Optional[List[Dict[str, Any]]] = None,
        temperature: float = 0.7,
        max_tokens: int = 4096,
        user_id: Optional[int] = None,
        model: Optional[str] = None,
        use_prompt_cache: bool = False,
    ):
        """
        Stream from Anthropic Messages API with tool_use support.

        Yields event dicts:
            {"type": "text", "text": "..."}
            {"type": "tool_use_start", "id": "...", "name": "..."}
            {"type": "tool_use_input", "partial_json": "..."}
            {"type": "tool_use_end", "id": "...", "name": "...", "input": {...}}
            {"type": "message_done", "stop_reason": "...", "usage": {...}}
        """
        if not self.anthropic_key and not self.hf_key:
            raise Exception("No valid API keys configured for Anthropic or Hugging Face.")

        if self.anthropic_key:
            body: Dict[str, Any] = {
                "model": model or self.claude_model,
                "max_tokens": max_tokens,
                "temperature": temperature,
                "messages": messages,
                "stream": True,
            }
            if system:
                if use_prompt_cache:
                    body["system"] = [
                        {
                            "type": "text",
                            "text": system,
                            "cache_control": {"type": "ephemeral"},
                        }
                    ]
                else:
                    body["system"] = system
            if tools:
                if use_prompt_cache and tools:
                    cached_tools = [dict(t) for t in tools]
                    last = cached_tools[-1]
                    if "input_schema" in last:
                        last["cache_control"] = {"type": "ephemeral"}
                    body["tools"] = cached_tools
                else:
                    body["tools"] = tools

            try:
                async with httpx.AsyncClient(timeout=120.0) as client:
                    async with client.stream(
                        "POST",
                        self.anthropic_url,
                        headers={
                            "x-api-key": self.anthropic_key,
                            "anthropic-version": "2023-06-01",
                            "content-type": "application/json",
                        },
                        json=body,
                    ) as response:
                        if response.status_code != 200:
                            error_body = await response.aread()
                            raise Exception(
                                f"Anthropic Stream Error {response.status_code}: {error_body}"
                            )

                        # Track active content blocks by index
                        active_blocks: Dict[int, Dict[str, Any]] = {}
                        accumulated_json: Dict[int, str] = {}

                        async for line in response.aiter_lines():
                            if not line or not line.startswith("data: "):
                                continue
                            payload = line[6:]
                            if payload.strip() == "[DONE]":
                                break

                            try:
                                event = json.loads(payload)
                            except Exception:
                                continue

                            event_type = event.get("type", "")

                            if event_type == "content_block_start":
                                idx = event.get("index", 0)
                                block = event.get("content_block", {})
                                active_blocks[idx] = block
                                if block.get("type") == "tool_use":
                                    accumulated_json[idx] = ""
                                    yield {
                                        "type": "tool_use_start",
                                        "id": block.get("id", ""),
                                        "name": block.get("name", ""),
                                    }

                            elif event_type == "content_block_delta":
                                idx = event.get("index", 0)
                                delta = event.get("delta", {})
                                delta_type = delta.get("type", "")

                                if delta_type == "text_delta":
                                    text = delta.get("text", "")
                                    if text:
                                        yield {"type": "text", "text": text}

                                elif delta_type == "input_json_delta":
                                    partial = delta.get("partial_json", "")
                                    if idx in accumulated_json:
                                        accumulated_json[idx] += partial
                                    yield {
                                        "type": "tool_use_input",
                                        "partial_json": partial,
                                    }

                            elif event_type == "content_block_stop":
                                idx = event.get("index", 0)
                                block = active_blocks.get(idx, {})
                                if block.get("type") == "tool_use":
                                    raw_json = accumulated_json.pop(idx, "{}")
                                    try:
                                        parsed_input = json.loads(raw_json)
                                    except Exception:
                                        parsed_input = {}
                                    yield {
                                        "type": "tool_use_end",
                                        "id": block.get("id", ""),
                                        "name": block.get("name", ""),
                                        "input": parsed_input,
                                    }

                            elif event_type == "message_delta":
                                delta = event.get("delta", {})
                                usage = event.get("usage", {})
                                yield {
                                    "type": "message_done",
                                    "stop_reason": delta.get("stop_reason", ""),
                                    "usage": usage,
                                }

                            elif event_type == "message_stop":
                                pass  # Already handled via message_delta
                return
            except Exception as exc:
                if not self.hf_key:
                    raise
                logger.warning(
                    "Anthropic stream_message failed; using Hugging Face fallback: %s",
                    exc,
                )

        # Fallback stream: text-only (no tool_use blocks)
        fallback_prompt = self._messages_to_plain_text(messages)
        fallback_system = system or "You are Mathia, a helpful AI assistant."
        if tools:
            fallback_system += (
                "\n\nTool execution is currently unavailable in this mode. "
                "Answer directly, and briefly mention any missing external actions."
            )
        collected_chunks: List[str] = []
        async for chunk in self._stream_huggingface(
            fallback_system,
            fallback_prompt,
            temperature,
            max_tokens,
            model_name=model or self._model_for("huggingface", "executor"),
        ):
            if chunk:
                collected_chunks.append(chunk)
                yield {"type": "text", "text": chunk}
        input_tokens = self._estimate_tokens(fallback_system) + self._estimate_tokens(fallback_prompt)
        output_tokens = self._estimate_tokens("".join(collected_chunks))
        self._record_token_usage(input_tokens + output_tokens, user_id)
        yield {
            "type": "message_done",
            "stop_reason": "end_turn",
            "usage": {
                "input_tokens": input_tokens,
                "output_tokens": output_tokens,
            },
        }

    # ------------------------------------------------------------------ #
    #  Original methods (kept for backward compatibility)                 #
    # ------------------------------------------------------------------ #

    async def _call_claude(self, system_prompt: str, user_prompt: str, temperature: float, max_tokens: int, model_name: Optional[str] = None) -> str:
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
                    "model": model_name or self.claude_model,
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

    async def _call_claude_stream(self, system_prompt: str, user_prompt: str, temperature: float, max_tokens: int, model_name: Optional[str] = None):
        """
        Stream from Anthropic Messages API.
        Yields small text deltas to reduce perceived latency and token waste.
        """
        async with httpx.AsyncClient(timeout=40.0) as client:
            async with client.stream(
                "POST",
                self.anthropic_url,
                headers={
                    "x-api-key": self.anthropic_key,
                    "anthropic-version": "2023-06-01",
                    "content-type": "application/json"
                },
                json={
                    "model": model_name or self.claude_model,
                    "max_tokens": max_tokens,
                    "temperature": temperature,
                    "system": system_prompt,
                    "messages": [
                        {"role": "user", "content": user_prompt}
                    ],
                    "stream": True
                }
            ) as response:
                if response.status_code != 200:
                    body = await response.aread()
                    raise Exception(f"Anthropic Stream Error {response.status_code}: {body}")

                async for line in response.aiter_lines():
                    if not line or not line.startswith("data: "):
                        continue
                    payload = line[6:]
                    if payload.strip() == "[DONE]":
                        break
                    try:
                        event = json.loads(payload)
                        # content_block_delta carries the text diff
                        if event.get("type") == "content_block_delta":
                            delta = event.get("delta", {})
                            text = delta.get("text")
                            if text:
                                yield text
                    except Exception:
                        continue

    async def _call_huggingface(
        self,
        system_prompt: str,
        user_prompt: str,
        temperature: float,
        max_tokens: int,
        json_mode: bool,
        model_name: Optional[str] = None,
    ) -> str:
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
                    "model": model_name or self.hf_model,
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

    async def _stream_huggingface(self, system_prompt: str, user_prompt: str, temperature: float, max_tokens: int, model_name: Optional[str] = None):
        """Stream from Hugging Face Router (OpenAI-compatible SSE)"""
        
        messages = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": user_prompt})

        async with httpx.AsyncClient(timeout=30.0) as client:
            async with client.stream(
                "POST",
                self.hf_url,
                headers={
                    "Authorization": f"Bearer {self.hf_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": model_name or self.hf_model,
                    "messages": messages,
                    "max_tokens": max_tokens,
                    "temperature": temperature,
                    "stream": True,
                },
            ) as response:
                if response.status_code != 200:
                    error_text = await response.aread()
                    raise Exception(f"HF Stream Error {response.status_code}: {error_text}")

                async for line in response.aiter_lines():
                    if line.startswith("data: "):
                        data_str = line[6:]
                        if data_str.strip() == "[DONE]":
                            break
                        try:
                            data = json.loads(data_str)
                            delta = data["choices"][0]["delta"]
                            if "content" in delta:
                                yield delta["content"]
                        except Exception:
                            continue

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
                # Primary attempt
                try:
                    return json.loads(json_str)
                except json.JSONDecodeError:
                    # Fallback: tolerate trailing commas/ellipsis using literal_eval
                    import ast
                    try:
                        py_obj = ast.literal_eval(
                            json_str.replace("null", "None")
                                    .replace("true", "True")
                                    .replace("false", "False")
                                    .replace("...", "")
                        )
                        if isinstance(py_obj, dict):
                            return py_obj
                    except Exception:
                        pass
        except Exception:
            pass
            
        logger.error(f"Failed to extract JSON from: {text[:100]}...")
        return {}

    def _should_cache(self, json_mode: bool, temperature: float) -> bool:
        if not getattr(settings, "LLM_CACHE_ENABLED", True):
            return False
        min_temp = float(getattr(settings, "LLM_CACHE_MIN_TEMP", 0.3))
        return json_mode or temperature <= min_temp

    def _cache_key(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        temperature: float,
        max_tokens: int,
        json_mode: bool,
        user_id: Optional[int] = None,
        room_id: Optional[int] = None,
        model_role: str = "default",
        provider_preference: Optional[str] = None,
    ) -> str:
        payload = json.dumps({
            "system": system_prompt,
            "user": user_prompt,
            "temp": temperature,
            "max_tokens": max_tokens,
            "json_mode": json_mode,
            "model": self.claude_model or self.hf_model,
            "role": model_role,
            "provider": provider_preference or "",
            "user_id": user_id,
            "room_id": room_id,
        }, sort_keys=True)
        digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()
        return f"llm_cache:{digest}"

    def _store_cache(self, cache_key: Optional[str], response: str) -> None:
        if not cache_key:
            return
        ttl = int(getattr(settings, "LLM_CACHE_TTL_SECONDS", 600))
        if ttl <= 0:
            return
        try:
            cache.set(cache_key, response, ttl)
        except Exception:
            return

    def _truncate(self, prompt: str, is_system: bool = False) -> str:
        """
        Clamp prompt length to avoid runaway token costs.
        """
        if not prompt:
            return ""
        limit = getattr(settings, 'LLM_PROMPT_CHAR_LIMIT', 4000)
        if len(prompt) <= limit:
            return prompt
        suffix = "\n\n[truncated for cost control]"
        if is_system:
            return prompt[:limit] + suffix
        # For user prompts keep the tail (often the question) intact
        head = prompt[: int(limit * 0.6)]
        tail = prompt[-int(limit * 0.3):]
        return head + "\n...\n" + tail + suffix

def extract_json(text: str) -> Dict:
    """
    Robust JSON extraction from LLM output.
    Handles markdown blocks, trailing text, etc.
    Wrapper for the class method or standalone logic.
    """
    # Simply delegate to a temporary instance or duplicate logic 
    # to keep it importable as a utility function.
    return LLMClient().extract_json(text)

# Singleton (thread-safe)
_client = None
_client_lock = threading.Lock()

def get_llm_client() -> LLMClient:
    """Get or create the global LLM client instance (thread-safe)."""
    global _client
    if _client is None:
        with _client_lock:
            if _client is None:
                _client = LLMClient()
    return _client
