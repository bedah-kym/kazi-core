"""
LLM Client for Kazi Orchestration
Handles communication with LLM providers (Anthropic, DeepSeek, Hugging Face)
with fallback logic. Includes rate limiting and token budget enforcement.
"""
import os
import json
import time
import asyncio
import threading
import logging
import weakref
import httpx
import hashlib
from typing import Dict, List, Optional, Any
from asgiref.sync import sync_to_async
from django.conf import settings
from django.core.cache import cache
from django.utils import timezone

from orchestration.model_catalog import find_model

logger = logging.getLogger(__name__)

_DATE_MARKER = "Current date and time:"

# Which providers this client can speak to, and the settings attribute that
# holds each one's key. Order is the documented fallback order.
PROVIDER_ORDER = ("anthropic", "deepseek", "huggingface")
_PROVIDER_KEY_ATTR = {
    "anthropic": "anthropic_key",
    "deepseek": "deepseek_key",
    "huggingface": "hf_key",
}


def _inject_current_datetime(system_prompt: str, now=None) -> str:
    """Prepend the server's current local date/time to a system prompt.

    Models have no clock: without this, date math like "next Friday" is
    guessed from training data and lands in the wrong year. Guarded so
    retries and cached prompts never get the line twice.
    """
    if not system_prompt or _DATE_MARKER in system_prompt:
        return system_prompt
    now = now or timezone.localtime()
    stamp = f"{_DATE_MARKER} {now.strftime('%Y-%m-%d %H:%M %A %Z')}."
    return f"{stamp}\n{system_prompt}"


# --------------------------------------------------------------------------- #
#  Hourly work-token quota (T-F3b)                                            #
# --------------------------------------------------------------------------- #
#
# The quota counts *work tokens* — what the model generates plus the input it
# newly reads — not the whole prompt resent on every call. The agent loop
# measures its own work (agent_loop._work_tokens) and records it here; a
# single-shot call has nothing resent, so it records what it sent and received.
#
# Usage lives in fixed 5-minute buckets. A read sums the buckets that still
# overlap the last hour, so usage ages out even while a user keeps calling — a
# single counter with a refreshed TTL never expired for an active user.
QUOTA_WINDOW_SECONDS = 3600
QUOTA_BUCKET_SECONDS = 300


def _humanize_seconds(seconds: Any) -> str:
    """A rough, human duration for the stop message ("about 42 minutes")."""
    try:
        seconds = max(0, int(seconds))
    except (TypeError, ValueError):
        seconds = 0
    if seconds < 60:
        return "less than a minute"
    minutes = seconds // 60
    if minutes < 60:
        return f"about {minutes} minute{'s' if minutes != 1 else ''}"
    hours = minutes / 60
    if hours < 2:
        return "about an hour"
    return f"about {int(round(hours))} hours"


def _quota_message(retry_after_seconds: int, reason: str) -> str:
    """The refusal text a caller sees when the hourly quota blocks a call."""
    if reason == "unreadable":
        return (
            "The hourly token limit could not be checked just now, so this call "
            "was refused rather than risk overrunning it. Please try again shortly."
        )
    when = _humanize_seconds(retry_after_seconds)
    return (
        f"You've reached this hour's token limit. It frees up in {when}; "
        "please try again then."
    )


class TokenQuotaExceeded(Exception):
    """A call broke the hourly work-token quota (T-F3b).

    ``reason`` is ``"limit"`` when the hour's usage is known and full, or
    ``"unreadable"`` when usage could not be read (fail closed: a cache
    failure must never silently lift the limit). ``retry_after_seconds`` is a
    rough wait before enough of the hour rolls out for another call.
    """

    def __init__(self, retry_after_seconds: int = 0, *, reason: str = "limit", user_id=None):
        try:
            self.retry_after_seconds = max(0, int(retry_after_seconds or 0))
        except (TypeError, ValueError):
            self.retry_after_seconds = QUOTA_WINDOW_SECONDS
        self.reason = reason
        self.user_id = user_id
        super().__init__(_quota_message(self.retry_after_seconds, reason))


def _quota_bucket_key(user_id: int, bucket: int) -> str:
    return f"llm_tokens:{user_id}:{bucket}"


def _quota_buckets_for_window(user_id: int, now: float) -> List[Any]:
    """The ``(bucket_start_seconds, cache_key)`` buckets that overlap the last hour."""
    now = int(now)
    window_start = now - QUOTA_WINDOW_SECONDS
    first = window_start // QUOTA_BUCKET_SECONDS
    last = now // QUOTA_BUCKET_SECONDS
    buckets = []
    for bucket in range(first, last + 1):
        start = bucket * QUOTA_BUCKET_SECONDS
        # A bucket counts until its end leaves the window.
        if start + QUOTA_BUCKET_SECONDS > window_start:
            buckets.append((start, _quota_bucket_key(user_id, bucket)))
    return buckets


def _read_cache_many(keys: List[str]) -> Dict[str, Any]:
    """Read many cache keys, surfacing a dead cache even when the backend is
    configured to ignore exceptions.

    Production runs django-redis with ``IGNORE_EXCEPTIONS`` on, so an outage
    makes the wrapped ``RedisCache.get_many`` return ``{}`` — which a quota read
    would take for an empty hour and fail open. Reading through the underlying
    client raises instead. Backends with no such client (LocMem in dev and
    tests) already raise on their own, so they use the normal path (T-F3b).
    """
    client = getattr(cache, "client", None)
    if client is not None and hasattr(client, "get_many"):
        return client.get_many(keys)
    return cache.get_many(keys)


def _read_hourly_usage(user_id: int, now: Optional[float] = None):
    """The user's work tokens in the last rolling hour, and the buckets behind it.

    Returns ``(used, buckets)`` where ``buckets`` is a list of
    ``(start_seconds, tokens)`` sorted oldest first. Raises if the cache cannot
    be read, so a caller can fail closed rather than treat the hour as empty.
    """
    now = time.time() if now is None else float(now)
    window = _quota_buckets_for_window(user_id, now)
    keys = [key for _, key in window]
    values = _read_cache_many(keys) if keys else {}
    buckets = [
        (start, int(values.get(key) or 0))
        for start, key in window
    ]
    buckets.sort()
    return sum(tokens for _, tokens in buckets), buckets


def _seconds_until_under(limit: int, buckets: List[Any], now: float) -> int:
    """Rough seconds until enough of the hour rolls out to fit under ``limit``.

    Buckets are dropped oldest first (each leaves the window one hour after it
    started); the first drop that puts the hour under the limit is when a new
    call can start.
    """
    now = float(now)
    total = sum(tokens for _, tokens in buckets)
    if total < limit:
        return 0
    remaining = total
    for start, tokens in sorted(buckets):
        remaining -= tokens
        if remaining < limit:
            return max(0, int(round(start + QUOTA_WINDOW_SECONDS - now)))
    return QUOTA_WINDOW_SECONDS


def _static_first(system: Optional[str]) -> str:
    """Move the volatile date stamp after the static system text.

    `_inject_current_datetime` puts the stamp first. Anthropic gets an explicit
    `cache_control` breakpoint around the static part (see
    `_call_anthropic_create`); OpenAI-style endpoints match a cached prefix
    automatically, so there the stamp must come last or the static prefix
    changes every minute and prompt caching never hits.
    """
    if system and system.startswith(_DATE_MARKER):
        stamp, _, static = system.partition("\n")
        return f"{static}\n{stamp}"
    return system or ""


def _anthropic_system(system: str, use_prompt_cache: bool):
    """The Anthropic ``system`` field with the static text ahead of the date stamp.

    With prompt caching, the breakpoint sits on the static text and the stamp
    follows it as its own block, so the cached prefix does not change every
    minute. Used by the create and the streaming paths alike.
    """
    if not use_prompt_cache:
        return _static_first(system)
    if system.startswith(_DATE_MARKER):
        stamp, _, static = system.partition("\n")
        return [
            {"type": "text", "text": static, "cache_control": {"type": "ephemeral"}},
            {"type": "text", "text": stamp},
        ]
    return [{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}]


# --------------------------------------------------------------------------- #
#  Shared HTTP client                                                          #
#  One httpx.AsyncClient per running event loop, reused across calls. A client  #
#  is bound to the loop that created its transport, so keying by loop keeps      #
#  tests (each with its own loop) and the ASGI server (one long-lived loop)     #
#  both correct. Timeouts are passed per request.                               #
# --------------------------------------------------------------------------- #
_http_clients: "weakref.WeakKeyDictionary[Any, httpx.AsyncClient]" = weakref.WeakKeyDictionary()
_http_clients_lock = threading.Lock()


def _get_http_client() -> httpx.AsyncClient:
    """Return the shared AsyncClient for the running loop, creating it lazily."""
    loop = asyncio.get_running_loop()
    client = _http_clients.get(loop)
    if client is None or getattr(client, "is_closed", False):
        with _http_clients_lock:
            # A pooled connection holds its loop, so the weak key alone never
            # frees a client made on a short-lived loop (async_to_sync in a
            # Celery task, asyncio.run). Drop the ones whose loop has closed.
            for old_loop in [key for key in list(_http_clients.keys()) if key.is_closed()]:
                _http_clients.pop(old_loop, None)
            client = _http_clients.get(loop)
            if client is None or getattr(client, "is_closed", False):
                client = httpx.AsyncClient(timeout=120.0)
                _http_clients[loop] = client
    return client


async def aclose_llm_clients() -> None:
    """Close every shared HTTP client. Called on ASGI shutdown."""
    with _http_clients_lock:
        clients = list(_http_clients.values())
        _http_clients.clear()
    for client in clients:
        close = getattr(client, "aclose", None)
        if close is None:
            continue
        try:
            await close()
        except Exception:  # nosec B110 - best-effort close at shutdown
            pass


class LLMClient:
    """
    Unified client for LLM interactions.
    Prioritizes Anthropic (Claude) if available, then DeepSeek, then Hugging Face.
    """

    def __init__(self):
        self.anthropic_key = getattr(settings, 'ANTHROPIC_API_KEY', os.environ.get('ANTHROPIC_API_KEY'))
        self.hf_key = getattr(settings, 'HF_API_TOKEN', os.environ.get('HF_API_TOKEN'))
        self.deepseek_key = getattr(settings, 'DEEPSEEK_API_KEY', os.environ.get('DEEPSEEK_API_KEY'))
        self._agent_name = getattr(settings, 'KAZI_AGENT_NAME', 'Kazi')

        # Models
        self.claude_model = "claude-sonnet-4-6"
        self.hf_model = "meta-llama/Llama-3.1-8B-Instruct"  # router-friendly chat model
        self.deepseek_model = getattr(
            settings, 'DEEPSEEK_MODEL',
            os.environ.get('DEEPSEEK_MODEL', 'deepseek-chat'),
        )

        # Endpoints
        self.anthropic_url = "https://api.anthropic.com/v1/messages"
        self.hf_url = "https://router.huggingface.co/v1/chat/completions"
        # DeepSeek is OpenAI-compatible, so it reuses the OpenAI call paths.
        self.deepseek_url = getattr(
            settings, 'DEEPSEEK_BASE_URL',
            os.environ.get('DEEPSEEK_BASE_URL', 'https://api.deepseek.com/v1/chat/completions'),
        )

    def _openai_config(self, provider: str, model_name: Optional[str] = None):
        """Endpoint URL, API key, and model for an OpenAI-compatible provider."""
        if provider == "deepseek":
            return self.deepseek_url, self.deepseek_key, (model_name or self.deepseek_model)
        return self.hf_url, self.hf_key, (model_name or self.hf_model)

    def _configured_providers(self) -> List[str]:
        """Providers whose API key is set, in the documented fallback order."""
        return [p for p in PROVIDER_ORDER if getattr(self, _PROVIDER_KEY_ATTR[p], None)]

    def default_provider(self) -> Optional[str]:
        """The provider that answers a chat turn when the room states no preference.

        ``LLM_DEFAULT_PROVIDER`` names it. If that setting is unset (or names a
        provider with no key), the first configured provider in the documented
        order (anthropic, deepseek, huggingface) is used. Returns None when no
        provider key is configured.
        """
        configured = self._configured_providers()
        preferred = str(getattr(settings, "LLM_DEFAULT_PROVIDER", "") or "").strip().lower()
        if preferred:
            if preferred in configured:
                return preferred
            logger.warning(
                "LLM_DEFAULT_PROVIDER=%r has no configured API key; using %r instead.",
                preferred, configured[0] if configured else None,
            )
        return configured[0] if configured else None

    def _auto_provider_order(self) -> List[str]:
        """Default provider first, then the other configured providers.

        Only the caller-visible behavior of ``create_message``/``stream_message``
        with no explicit provider: the operator's default answers, the rest are
        fallbacks if it errors.
        """
        default = self.default_provider()
        order = [default] if default else []
        for provider in self._configured_providers():
            if provider not in order:
                order.append(provider)
        return order

    def _provider_order(self, model_role: str, provider_preference: Optional[str]) -> List[str]:
        role = (model_role or "default").lower()
        preference = (provider_preference or "").lower().strip()
        if preference:
            ordered = [preference, "deepseek", "anthropic", "huggingface"]
        elif role == "planner":
            ordered = [getattr(settings, "LLM_PLANNER_PROVIDER", "anthropic").lower(), "deepseek", "anthropic", "huggingface"]
        elif role == "executor":
            ordered = [getattr(settings, "LLM_EXECUTOR_PROVIDER", "huggingface").lower(), "deepseek", "huggingface", "anthropic"]
        elif role == "reviewer":
            ordered = [getattr(settings, "LLM_REVIEWER_PROVIDER", "anthropic").lower(), "deepseek", "anthropic", "huggingface"]
        else:
            default = self.default_provider()
            ordered = [default, "anthropic", "deepseek", "huggingface"] if default else ["anthropic", "deepseek", "huggingface"]
        # De-dup while preserving order
        seen = set()
        result: List[str] = []
        for item in ordered:
            if item in ("deepseek", "anthropic", "huggingface") and item not in seen:
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
            if role == "reviewer":
                return getattr(settings, "LLM_REVIEWER_MODEL", self.claude_model)
            return self.claude_model
        if provider == "deepseek":
            if role == "planner":
                return getattr(settings, "LLM_PLANNER_MODEL", self.deepseek_model)
            if role == "executor":
                return getattr(settings, "LLM_EXECUTOR_MODEL", self.deepseek_model)
            if role == "reviewer":
                return getattr(settings, "LLM_REVIEWER_MODEL", self.deepseek_model)
            return self.deepseek_model
        if role == "planner":
            return getattr(settings, "LLM_PLANNER_MODEL", self.hf_model)
        if role == "executor":
            return getattr(settings, "LLM_EXECUTOR_MODEL", self.hf_model)
        if role == "reviewer":
            return getattr(settings, "LLM_REVIEWER_MODEL", self.hf_model)
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
        provider: str = "huggingface",
    ) -> Dict[str, Any]:
        """Fallback for create_message via an OpenAI-compatible provider (HF or DeepSeek)."""
        prompt = self._messages_to_plain_text(messages)
        _agent = getattr(self, "_agent_name", "Kazi")
        fallback_system = system or f"You are {_agent}, a helpful AI assistant."
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
            model_name=model or self._model_for(provider, "executor"),
            provider=provider,
        )

        input_tokens = self._estimate_tokens(fallback_system) + self._estimate_tokens(prompt)
        output_tokens = self._estimate_tokens(response_text)

        return {
            "id": f"{provider}-fallback-message",
            "type": "message",
            "role": "assistant",
            "content": [{"type": "text", "text": response_text}],
            "stop_reason": "end_turn",
            "usage": {
                "input_tokens": input_tokens,
                "output_tokens": output_tokens,
            },
        }

    # ------------------------------------------------------------------ #
    #  OpenAI-compatible tool-calling adapter (DeepSeek)                   #
    #  Translates the Anthropic Messages contract (content blocks +       #
    #  tool_use / tool_result, stop_reason) to/from OpenAI chat tools so  #
    #  the agent loop keeps working unchanged on a non-Anthropic model.   #
    # ------------------------------------------------------------------ #
    def _anthropic_to_openai_messages(self, system: str, messages: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        out: List[Dict[str, Any]] = []
        if system:
            content = system if isinstance(system, str) else self._message_content_to_text(system)
            out.append({"role": "system", "content": _static_first(content)})
        for m in messages:
            role = m.get("role")
            content = m.get("content")
            if isinstance(content, str):
                out.append({"role": role, "content": content})
                continue
            if not isinstance(content, list):
                out.append({"role": role, "content": str(content or "")})
                continue
            if role == "assistant":
                text_parts: List[str] = []
                tool_calls: List[Dict[str, Any]] = []
                for b in content:
                    if not isinstance(b, dict):
                        text_parts.append(str(b))
                    elif b.get("type") == "text":
                        text_parts.append(b.get("text", ""))
                    elif b.get("type") == "tool_use":
                        tool_calls.append({
                            "id": b.get("id"),
                            "type": "function",
                            "function": {
                                "name": b.get("name"),
                                "arguments": json.dumps(b.get("input", {}), default=str),
                            },
                        })
                msg: Dict[str, Any] = {
                    "role": "assistant",
                    "content": "\n".join(p for p in text_parts if p) or None,
                }
                if tool_calls:
                    msg["tool_calls"] = tool_calls
                out.append(msg)
            elif role == "user":
                tool_results = [
                    b for b in content
                    if isinstance(b, dict) and b.get("type") == "tool_result"
                ]
                if tool_results:
                    for b in tool_results:
                        payload = b.get("content")
                        if not isinstance(payload, str):
                            payload = self._message_content_to_text(payload)
                        out.append({
                            "role": "tool",
                            "tool_call_id": b.get("tool_use_id"),
                            "content": payload,
                        })
                    leftover = self._message_content_to_text([
                        b for b in content
                        if isinstance(b, dict) and b.get("type") == "text"
                    ])
                    if leftover.strip():
                        out.append({"role": "user", "content": leftover})
                else:
                    out.append({"role": "user", "content": self._message_content_to_text(content)})
            else:
                out.append({"role": role, "content": self._message_content_to_text(content)})
        return out

    def _anthropic_tools_to_openai(self, tools: Optional[List[Dict[str, Any]]]) -> List[Dict[str, Any]]:
        out: List[Dict[str, Any]] = []
        for t in tools or []:
            if not isinstance(t, dict) or "input_schema" not in t:
                continue
            out.append({
                "type": "function",
                "function": {
                    "name": t.get("name"),
                    "description": t.get("description", ""),
                    "parameters": t.get("input_schema") or {"type": "object", "properties": {}},
                },
            })
        return out

    def _openai_response_to_anthropic(self, data: Dict[str, Any]) -> Dict[str, Any]:
        choice = (data.get("choices") or [{}])[0]
        message = choice.get("message", {}) or {}
        finish = choice.get("finish_reason")
        blocks: List[Dict[str, Any]] = []
        text = message.get("content")
        if text:
            blocks.append({"type": "text", "text": text})
        tool_calls = message.get("tool_calls") or []
        for idx, tc in enumerate(tool_calls):
            fn = tc.get("function", {}) or {}
            try:
                args = json.loads(fn.get("arguments") or "{}")
            except Exception:
                args = {}
            blocks.append({
                "type": "tool_use",
                "id": tc.get("id") or f"call_{idx}",
                "name": fn.get("name"),
                "input": args,
            })
        if tool_calls:
            stop_reason = "tool_use"
        elif finish == "length":
            stop_reason = "max_tokens"
        else:
            stop_reason = "end_turn"
        if not blocks:
            blocks = [{"type": "text", "text": ""}]
        usage = data.get("usage", {}) or {}
        return {
            "id": data.get("id", "openai-message"),
            "type": "message",
            "role": "assistant",
            "content": blocks,
            "stop_reason": stop_reason,
            "usage": {
                "input_tokens": usage.get("prompt_tokens", 0),
                "output_tokens": usage.get("completion_tokens", 0),
            },
        }

    async def _create_openai_message(
        self,
        *,
        provider: str,
        messages: List[Dict[str, Any]],
        system: str,
        tools: Optional[List[Dict[str, Any]]],
        temperature: float,
        max_tokens: int,
        user_id: Optional[int],
        model: Optional[str] = None,
    ) -> Dict[str, Any]:
        """create_message via an OpenAI-compatible endpoint, with tool calling."""
        url, key, model_name = self._openai_config(provider, model)
        body: Dict[str, Any] = {
            "model": model_name,
            "messages": self._anthropic_to_openai_messages(system, messages),
            "max_tokens": max_tokens,
            "temperature": temperature,
        }
        oai_tools = self._anthropic_tools_to_openai(tools)
        if oai_tools:
            body["tools"] = oai_tools

        client = _get_http_client()
        response = await client.post(
            url,
            headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
            json=body,
            timeout=120.0,
        )
        if response.status_code != 200:
            raise Exception(f"{provider} create_message Error {response.status_code}: {response.text}")
        data = response.json()

        result = self._openai_response_to_anthropic(data)
        return result

    async def _get_user_token_budget(
        self, user_id: Optional[int], now: Optional[float] = None,
    ) -> Dict[str, Any]:
        """
        Get the hourly work-token limit and the user's usage in the last hour.

        Staff/superuser accounts are exempt. ``used`` is None when usage could
        not be read, so the caller can fail closed instead of treating the hour
        as empty. Returns {"limit": N, "used": M | None, "exempt": bool}.
        """
        if not user_id:
            return {"limit": 999999, "used": 0, "exempt": False}

        limit = int(getattr(settings, "LLM_TOKEN_LIMIT_PER_USER_PER_HOUR", 200000))

        # Staff / superusers are exempt from the token quota (owner debugging, support).
        # Must run via sync_to_async: a sync ORM query inside the async agent
        # loop raises SynchronousOnlyOperation, and the bare except used to
        # swallow it — silently dropping the exemption for every staff user.
        try:
            from django.contrib.auth import get_user_model
            User = get_user_model()
            user = await sync_to_async(
                lambda: User.objects.filter(pk=user_id).only('is_staff', 'is_superuser').first()
            )()
            if user and (user.is_staff or user.is_superuser):
                return {"limit": 10_000_000, "used": 0, "exempt": True}
        except Exception:
            pass

        try:
            used, _ = _read_hourly_usage(user_id, now)
        except Exception:
            used = None
        return {"limit": limit, "used": used, "exempt": False}

    async def _check_token_quota(
        self, estimated_new_tokens: int, user_id: Optional[int],
    ) -> None:
        """Refuse a call that breaks the hourly work-token quota (T-F3b).

        ``estimated_new_tokens`` is the work about to be done: an estimate for a
        single-shot call, or 0 for an agent-loop call (whose work is only known
        after it runs and is recorded separately). Raises ``TokenQuotaExceeded``
        when the hour is already full or this call would overrun it. Fails
        closed: if usage cannot be read, the call is refused.
        """
        if not user_id:
            return  # No tracking for anonymous

        if not getattr(settings, "LLM_TOKEN_QUOTA_ENABLED", True):
            return  # Quota disabled (dev) — don't starve batch jobs

        budget = await self._get_user_token_budget(user_id)
        if budget.get("exempt"):
            return

        used = budget.get("used")
        limit = int(budget.get("limit") or 0)
        if used is None:
            logger.warning("Token quota unreadable for user %s; failing closed", user_id)
            raise TokenQuotaExceeded(
                QUOTA_WINDOW_SECONDS, reason="unreadable", user_id=user_id,
            )

        try:
            estimated_new_tokens = max(0, int(estimated_new_tokens or 0))
        except (TypeError, ValueError):
            estimated_new_tokens = 0

        if used >= limit or used + estimated_new_tokens > limit:
            now = time.time()
            try:
                _, buckets = _read_hourly_usage(user_id, now)
                retry = _seconds_until_under(limit, buckets, now)
            except Exception:
                retry = QUOTA_WINDOW_SECONDS
            logger.warning(
                "Token quota exceeded for user %s: used %s + %s >= limit %s",
                user_id, used, estimated_new_tokens, limit,
            )
            raise TokenQuotaExceeded(retry, reason="limit", user_id=user_id)

    async def check_hourly_quota(self, user_id: Optional[int]) -> Optional["TokenQuotaExceeded"]:
        """Whether a quota-bound call would be refused right now (T-F3b).

        Returns the ``TokenQuotaExceeded`` a zero-work call would raise — the
        hour is still full, or usage could not be read — or ``None`` when the
        hour has room. Used before telling a user their limit has freed, so the
        harness never claims a resume it has not checked.
        """
        try:
            await self._check_token_quota(0, user_id)
        except TokenQuotaExceeded as exc:
            return exc
        return None

    def _record_token_usage(self, tokens: int, user_id: Optional[int], now: Optional[float] = None) -> None:
        """Add work tokens to the user's rolling-hour quota.

        The add/incr pair is atomic on the backend, so concurrent recordings
        (two rooms, parallel delegations, several worker processes) never lose
        an increment the way a read-modify-write would. ``add`` creates the
        bucket with its TTL when it is absent; every bucket therefore has a
        bounded TTL and usage ages out of the rolling hour even while the user
        keeps calling. A fresh ``incr`` on some backends creates a key WITHOUT
        a TTL, so a ``touch`` after the increment re-arms it; a counter is never
        left immortal.
        """
        if not user_id or tokens <= 0:
            return

        now = time.time() if now is None else float(now)
        bucket = int(now) // QUOTA_BUCKET_SECONDS
        cache_key = _quota_bucket_key(user_id, bucket)
        ttl = QUOTA_WINDOW_SECONDS + QUOTA_BUCKET_SECONDS
        try:
            if cache.add(cache_key, tokens, ttl):
                return
            try:
                cache.incr(cache_key, tokens)
            except ValueError:
                # The bucket expired between add() and incr(); recreate it, and
                # with it the TTL that keeps the counter from living forever.
                if cache.add(cache_key, tokens, ttl):
                    return
                cache.incr(cache_key, tokens)
        except Exception:
            logger.warning("Could not record token usage for user %s", user_id)
            return
        try:
            cache.touch(cache_key, ttl)
        except Exception:
            pass

    def record_work_tokens(self, tokens: int, user_id: Optional[int]) -> None:
        """Record work tokens for a user against the hourly quota (T-F3b).

        The agent loop measures its own work (generated + newly-read input) and
        calls this after each model call, so the resent prompt prefix is never
        charged.
        """
        self._record_token_usage(tokens, user_id)

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
        # A single-shot call has nothing resent, so the whole prompt is new
        # work: estimate it before the request and refuse if it would overrun.
        estimated_tokens = self._estimate_tokens(system_prompt) + self._estimate_tokens(user_prompt)
        await self._check_token_quota(estimated_tokens, user_id)

        max_tokens = min(max_tokens, getattr(settings, 'LLM_MAX_TOKENS', 700))
        user_prompt = self._truncate(user_prompt)
        system_prompt = self._truncate(system_prompt, is_system=True)
        cache_key = None
        if self._should_cache(json_mode=json_mode, temperature=temperature):
            # Key from the timestamp-free prompt: the date line changes every
            # minute and would otherwise defeat the response cache for
            # otherwise-identical calls in adjacent minutes.
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
        system_prompt = _inject_current_datetime(system_prompt)
        provider_order = self._provider_order(model_role, provider_preference)
        last_error: Optional[Exception] = None
        for provider in provider_order:
            if provider == "deepseek" and self.deepseek_key:
                try:
                    model_name = self._model_for("deepseek", model_role)
                    response = await self._call_huggingface(
                        system_prompt, user_prompt, temperature, max_tokens,
                        json_mode, model_name, provider="deepseek",
                    )
                    self._record_token_usage(estimated_tokens + self._estimate_tokens(response), user_id)
                    self._store_cache(cache_key, response)
                    return response
                except Exception as e:
                    last_error = e
                    logger.error(f"DeepSeek API failed: {e}. Falling back.")
            if provider == "anthropic" and self.anthropic_key:
                try:
                    model_name = self._model_for("anthropic", model_role)
                    response = await self._call_claude(system_prompt, user_prompt, temperature, max_tokens, model_name)
                    self._record_token_usage(estimated_tokens + self._estimate_tokens(response), user_id)
                    self._store_cache(cache_key, response)
                    return response
                except Exception as e:
                    last_error = e
                    logger.warning(f"Claude API failed: {e}. Falling back.")
            if provider == "huggingface" and self.hf_key:
                try:
                    model_name = self._model_for("huggingface", model_role)
                    response = await self._call_huggingface(system_prompt, user_prompt, temperature, max_tokens, json_mode, model_name)
                    self._record_token_usage(estimated_tokens + self._estimate_tokens(response), user_id)
                    self._store_cache(cache_key, response)
                    return response
                except Exception as e:
                    last_error = e
                    logger.error(f"Hugging Face API failed: {e}")

        if last_error:
            raise Exception(f"All LLM providers failed. Last error: {last_error}")

        raise Exception("No valid API keys configured for Anthropic or Hugging Face.")

    async def generate_json(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        required_fields: Optional[List[str]] = None,
        temperature: float = 0.2,
        max_tokens: int = 600,
        user_id: Optional[int] = None,
        model_role: str = "default",
        provider_preference: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Generate a JSON object, retrying once with the validation error
        if the first attempt is malformed or missing required fields.

        This turns the old silent ``{}``-on-bad-JSON behavior into a
        one-shot self-repair loop, then degrades gracefully.
        """
        required_fields = required_fields or []

        def _ok(parsed: Any) -> bool:
            if not isinstance(parsed, dict):
                return False
            return all(parsed.get(f) not in (None, "", [], {}) for f in required_fields)

        response_text = await self.generate_text(
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            temperature=temperature,
            max_tokens=max_tokens,
            json_mode=True,
            user_id=user_id,
            model_role=model_role,
            provider_preference=provider_preference,
        )
        parsed = self.extract_json(response_text)
        if _ok(parsed):
            return parsed

        if not isinstance(parsed, dict):
            problems = "the response was not valid JSON"
        else:
            missing = [f for f in required_fields if parsed.get(f) in (None, "", [], {})]
            problems = "missing required fields: " + ", ".join(missing)

        repair_prompt = (
            user_prompt
            + f"\n\nYour previous response was invalid: {problems}. "
            + "Respond with a single valid JSON object only."
        )
        response_text = await self.generate_text(
            system_prompt=system_prompt,
            user_prompt=repair_prompt,
            temperature=temperature,
            max_tokens=max_tokens,
            json_mode=True,
            user_id=user_id,
            model_role=model_role,
            provider_preference=provider_preference,
        )
        repaired = self.extract_json(response_text)
        return repaired if _ok(repaired) else parsed

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
        system_prompt = _inject_current_datetime(system_prompt)
        user_prompt = self._truncate(user_prompt)
        system_prompt = self._truncate(system_prompt, is_system=True)

        provider_order = self._provider_order(model_role, provider_preference)
        last_error: Optional[Exception] = None
        for provider in provider_order:
            if provider == "deepseek" and self.deepseek_key:
                emitted = False
                try:
                    model_name = self._model_for("deepseek", model_role)
                    async for chunk in self._stream_huggingface(
                        system_prompt, user_prompt, temperature, max_tokens,
                        model_name, provider="deepseek",
                    ):
                        emitted = True
                        yield chunk
                    return
                except Exception as e:
                    # After the first delta reached the consumer, restarting on
                    # another provider would duplicate the partial answer.
                    if emitted:
                        raise
                    last_error = e
                    logger.error(f"DeepSeek API failed: {e}. Falling back.")
            if provider == "anthropic" and self.anthropic_key:
                emitted = False
                try:
                    model_name = self._model_for("anthropic", model_role)
                    async for chunk in self._call_claude_stream(system_prompt, user_prompt, temperature, max_tokens, model_name):
                        if chunk:
                            emitted = True
                            yield chunk
                    return
                except Exception as e:
                    if emitted:
                        raise
                    last_error = e
                    logger.warning(f"Claude API failed: {e}. Falling back.")
            if provider == "huggingface" and self.hf_key:
                emitted = False
                try:
                    model_name = self._model_for("huggingface", model_role)
                    async for chunk in self._stream_huggingface(system_prompt, user_prompt, temperature, max_tokens, model_name):
                        emitted = True
                        yield chunk
                    return
                except Exception as e:
                    if emitted:
                        raise
                    last_error = e
                    logger.error(f"Hugging Face API failed: {e}")

        if last_error:
            raise Exception(f"All LLM providers failed. Last error: {last_error}")

        raise Exception("No valid API keys configured for Anthropic or Hugging Face.")

    # ------------------------------------------------------------------ #
    #  Agent loop methods — full Messages API with tool_use support       #
    # ------------------------------------------------------------------ #

    async def _call_anthropic_create(
        self,
        *,
        messages: List[Dict[str, Any]],
        system: str,
        tools: Optional[List[Dict[str, Any]]],
        temperature: float,
        max_tokens: int,
        user_id: Optional[int],
        model: str,
        use_prompt_cache: bool,
    ) -> Dict[str, Any]:
        """POST a single turn to the Anthropic Messages API and return the body."""
        body: Dict[str, Any] = {
            "model": model,
            "max_tokens": max_tokens,
            "temperature": temperature,
            "messages": messages,
        }
        if system:
            body["system"] = _anthropic_system(system, use_prompt_cache)
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

        client = _get_http_client()
        response = await client.post(
            self.anthropic_url,
            headers={
                "x-api-key": self.anthropic_key,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            },
            json=body,
            timeout=120.0,
        )
        if response.status_code != 200:
            raise Exception(f"Anthropic Error {response.status_code}: {response.text}")

        data = response.json()

        return data

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
        provider: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Call the Anthropic Messages API with full tool_use support.

        Args:
            model: Override model (a catalog model for `provider`). A name that
                does not belong to the provider it is sent to is dropped and the
                provider's own default model is used instead.
            provider: If set ("anthropic" | "deepseek" | "huggingface"), route
                directly to that provider using `model`; when None, the operator's
                default provider answers, with the others as fallbacks.
            use_prompt_cache: If True, wraps the system prompt with cache_control
                for Anthropic prompt caching (~90% cost reduction on cached tokens).

        Returns the raw response dict with keys:
            content: List of content blocks (text and/or tool_use)
            stop_reason: "end_turn" | "tool_use" | "max_tokens"
            usage: {input_tokens, output_tokens, cache_creation_input_tokens, cache_read_input_tokens}
        """
        if not self.anthropic_key and not self.hf_key and not self.deepseek_key:
            raise Exception("No valid API keys configured for DeepSeek, Anthropic, or Hugging Face.")

        system = _inject_current_datetime(system)

        # An agent-loop call's work is generated + newly-read input, which the
        # loop measures after the call and records through record_work_tokens.
        # The whole prompt is resent every call, so charging it here would bill
        # the cached prefix over and over (T-F3b). Just refuse a call that
        # starts once the hour is already spent.
        await self._check_token_quota(0, user_id)

        if provider:
            if provider == "anthropic" and not self.anthropic_key:
                raise Exception("Anthropic API key is not configured.")
            return await self._create_with_provider(
                provider,
                model=self._model_for_provider(provider, model),
                messages=messages,
                system=system,
                tools=tools,
                temperature=temperature,
                max_tokens=max_tokens,
                user_id=user_id,
                use_prompt_cache=use_prompt_cache,
            )

        # Auto routing: the operator's default provider answers first; the other
        # configured providers are fallbacks only if it errors. A caller-supplied
        # model is forwarded only when it belongs to the provider that answers,
        # so a Claude model name never reaches DeepSeek/Hugging Face.
        order = self._auto_provider_order()
        if not order:
            raise Exception("No valid API keys configured for DeepSeek, Anthropic, or Hugging Face.")
        last_error: Optional[Exception] = None
        for candidate in order:
            try:
                return await self._create_with_provider(
                    candidate,
                    model=self._model_for_provider(candidate, model),
                    messages=messages,
                    system=system,
                    tools=tools,
                    temperature=temperature,
                    max_tokens=max_tokens,
                    user_id=user_id,
                    use_prompt_cache=use_prompt_cache,
                )
            except Exception as exc:
                last_error = exc
                logger.warning("%s create_message failed; trying fallback: %s", candidate, exc)
        raise last_error if last_error else Exception("No LLM provider available.")

    def _model_for_provider(self, provider: str, model: Optional[str]) -> Optional[str]:
        """Forward a caller-supplied model only when it belongs to `provider`.

        Keeps an Anthropic model name out of a DeepSeek/Hugging Face request; the
        provider resolves its own default model instead.
        """
        if model and find_model(provider, model) is not None:
            return model
        return None

    async def _create_with_provider(
        self,
        provider: str,
        *,
        model: Optional[str],
        messages: List[Dict[str, Any]],
        system: str,
        tools: Optional[List[Dict[str, Any]]],
        temperature: float,
        max_tokens: int,
        user_id: Optional[int],
        use_prompt_cache: bool,
    ) -> Dict[str, Any]:
        """Dispatch one create_message to a named provider."""
        if provider == "anthropic":
            if not self.anthropic_key:
                raise Exception("Anthropic API key is not configured.")
            return await self._call_anthropic_create(
                messages=messages,
                system=system,
                tools=tools,
                temperature=temperature,
                max_tokens=max_tokens,
                user_id=user_id,
                model=model or self._model_for("anthropic", "default"),
                use_prompt_cache=use_prompt_cache,
            )
        if provider == "deepseek":
            return await self._create_openai_message(
                provider="deepseek",
                messages=messages,
                system=system,
                tools=tools,
                temperature=temperature,
                max_tokens=max_tokens,
                user_id=user_id,
                model=model,
            )
        if provider == "huggingface":
            return await self._create_hf_fallback_message(
                messages=messages,
                system=system,
                temperature=temperature,
                max_tokens=max_tokens,
                user_id=user_id,
                model=model,
                tools=tools,
                provider="huggingface",
            )
        raise Exception(f"Unknown LLM provider: {provider}")

    async def _stream_openai_fallback(
        self,
        *,
        messages: List[Dict[str, Any]],
        system: str,
        tools: Optional[List[Dict[str, Any]]],
        temperature: float,
        max_tokens: int,
        user_id: Optional[int],
        provider: str,
        model: Optional[str] = None,
    ):
        """Text-only stream fallback via an OpenAI-compatible provider."""
        fallback_prompt = self._messages_to_plain_text(messages)
        _agent = getattr(self, "_agent_name", "Kazi")
        fallback_system = system or f"You are {_agent}, a helpful AI assistant."
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
            model_name=model or self._model_for(provider, "executor"),
            provider=provider,
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
        provider: Optional[str] = None,
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
        if not self.anthropic_key and not self.hf_key and not self.deepseek_key:
            raise Exception("No valid API keys configured for DeepSeek, Anthropic, or Hugging Face.")

        if provider == "deepseek":
            async for event in self._stream_openai_fallback(
                messages=messages,
                system=system,
                tools=tools,
                temperature=temperature,
                max_tokens=max_tokens,
                user_id=user_id,
                provider="deepseek",
                model=self._model_for_provider("deepseek", model),
            ):
                yield event
            return
        if provider == "huggingface":
            async for event in self._stream_openai_fallback(
                messages=messages,
                system=system,
                tools=tools,
                temperature=temperature,
                max_tokens=max_tokens,
                user_id=user_id,
                provider="huggingface",
                model=self._model_for_provider("huggingface", model),
            ):
                yield event
            return
        if provider == "anthropic":
            if not self.anthropic_key:
                raise Exception("Anthropic API key is not configured.")
            async for event in self._stream_anthropic_events(
                messages=messages,
                system=system,
                tools=tools,
                temperature=temperature,
                max_tokens=max_tokens,
                model=model,
                use_prompt_cache=use_prompt_cache,
            ):
                yield event
            return

        # Auto routing: the operator's default provider answers.
        default = self.default_provider()
        if default == "anthropic":
            emitted = False
            try:
                async for event in self._stream_anthropic_events(
                    messages=messages,
                    system=system,
                    tools=tools,
                    temperature=temperature,
                    max_tokens=max_tokens,
                    model=self._model_for_provider("anthropic", model),
                    use_prompt_cache=use_prompt_cache,
                ):
                    emitted = True
                    yield event
                return
            except Exception as exc:
                if not self.hf_key and not self.deepseek_key:
                    raise
                # After the first event reached the consumer, restarting on the
                # fallback would duplicate the partial answer (and replay tool
                # calls as text). Surface the error instead.
                if emitted:
                    raise
                logger.warning(
                    "Anthropic stream_message failed; using OpenAI-compatible fallback: %s",
                    exc,
                )

            # Fallback stream: text-only (no tool_use blocks)
            fallback_provider = "deepseek" if self.deepseek_key else "huggingface"
            async for event in self._stream_openai_fallback(
                messages=messages,
                system=system,
                tools=tools,
                temperature=temperature,
                max_tokens=max_tokens,
                user_id=user_id,
                provider=fallback_provider,
                model=None,
            ):
                yield event
            return

        if default in ("deepseek", "huggingface"):
            async for event in self._stream_openai_fallback(
                messages=messages,
                system=system,
                tools=tools,
                temperature=temperature,
                max_tokens=max_tokens,
                user_id=user_id,
                provider=default,
                model=self._model_for_provider(default, model),
            ):
                yield event
            return

        raise Exception("No valid API keys configured for DeepSeek, Anthropic, or Hugging Face.")

    async def _stream_anthropic_events(
        self,
        *,
        messages: List[Dict[str, Any]],
        system: str = "",
        tools: Optional[List[Dict[str, Any]]] = None,
        temperature: float = 0.7,
        max_tokens: int = 4096,
        model: Optional[str] = None,
        use_prompt_cache: bool = False,
    ):
        """
        Stream from the Anthropic Messages API, yielding event dicts:
            {"type": "text", "text": "..."}
            {"type": "tool_use_start", "id": "...", "name": "..."}
            {"type": "tool_use_input", "partial_json": "..."}
            {"type": "tool_use_end", "id": "...", "name": "...", "input": {...}}
            {"type": "message_done", "stop_reason": "...", "usage": {...}}
        Raises on any transport/API error — fallback policy lives in stream_message.
        """
        body: Dict[str, Any] = {
            "model": model or self.claude_model,
            "max_tokens": max_tokens,
            "temperature": temperature,
            "messages": messages,
            "stream": True,
        }
        if system:
            body["system"] = _anthropic_system(system, use_prompt_cache)
        if tools:
            if use_prompt_cache and tools:
                cached_tools = [dict(t) for t in tools]
                last = cached_tools[-1]
                if "input_schema" in last:
                    last["cache_control"] = {"type": "ephemeral"}
                body["tools"] = cached_tools
            else:
                body["tools"] = tools

        client = _get_http_client()
        async with client.stream(
            "POST",
            self.anthropic_url,
            headers={
                "x-api-key": self.anthropic_key,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            },
            json=body,
            timeout=120.0,
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
                except json.JSONDecodeError:
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

    # ------------------------------------------------------------------ #
    #  Original methods (kept for backward compatibility)                 #
    # ------------------------------------------------------------------ #

    async def _call_claude(self, system_prompt: str, user_prompt: str, temperature: float, max_tokens: int, model_name: Optional[str] = None) -> str:
        """Call Anthropic API"""
        client = _get_http_client()
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
                "system": _static_first(system_prompt),
                "messages": [
                    {"role": "user", "content": user_prompt}
                ]
            },
            timeout=30.0,
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
        client = _get_http_client()
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
                "system": _static_first(system_prompt),
                "messages": [
                    {"role": "user", "content": user_prompt}
                ],
                "stream": True
            },
            timeout=40.0,
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
                except (json.JSONDecodeError, KeyError, IndexError, TypeError):
                    continue

    async def _call_huggingface(
        self,
        system_prompt: str,
        user_prompt: str,
        temperature: float,
        max_tokens: int,
        json_mode: bool,
        model_name: Optional[str] = None,
        provider: str = "huggingface",
    ) -> str:
        """Call an OpenAI-compatible /v1/chat/completions API (Hugging Face or DeepSeek)."""
        url, key, model = self._openai_config(provider, model_name)

        # Build messages array in OpenAI/ChatML style. The volatile date stamp
        # goes last so the static system prefix is stable for prompt caching.
        messages = []
        if system_prompt:
            messages.append({"role": "system", "content": _static_first(system_prompt)})

        user_content = user_prompt
        if json_mode:
            # Nudge the model to answer with pure JSON
            user_content += "\n\nRespond with valid JSON only."

        messages.append({"role": "user", "content": user_content})

        client = _get_http_client()
        response = await client.post(
            url,
            headers={
                "Authorization": f"Bearer {key}",
                "Content-Type": "application/json",
            },
            json={
                "model": model,
                "messages": messages,
                "max_tokens": max_tokens,
                "temperature": temperature,
            },
            timeout=60.0,
        )

        if response.status_code != 200:
            raise Exception(f"{provider} Error {response.status_code}: {response.text}")

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

    async def _stream_huggingface(self, system_prompt: str, user_prompt: str, temperature: float, max_tokens: int, model_name: Optional[str] = None, provider: str = "huggingface"):
        """Stream from an OpenAI-compatible SSE endpoint (Hugging Face or DeepSeek)."""
        url, key, model = self._openai_config(provider, model_name)

        messages = []
        if system_prompt:
            messages.append({"role": "system", "content": _static_first(system_prompt)})
        messages.append({"role": "user", "content": user_prompt})

        client = _get_http_client()
        async with client.stream(
            "POST",
            url,
            headers={
                "Authorization": f"Bearer {key}",
                "Content-Type": "application/json",
            },
            json={
                "model": model,
                "messages": messages,
                "max_tokens": max_tokens,
                "temperature": temperature,
                "stream": True,
            },
            timeout=60.0,
        ) as response:
            if response.status_code != 200:
                error_text = await response.aread()
                raise Exception(f"{provider} Stream Error {response.status_code}: {error_text}")

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
                    except (json.JSONDecodeError, KeyError, IndexError, TypeError):
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
                json_str = clean_text[start:end + 1]
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

        logger.error("Failed to extract JSON from model output (%d chars)", len(text or ""))
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
