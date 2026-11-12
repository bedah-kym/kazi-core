"""Native OpenAI Responses adapter.

OpenAI's current reasoning models need the Responses API for tool calling
(F4). This module speaks that API in Kazi's Anthropic-shaped message contract
so the agent loop is unchanged: text and ``tool_use`` blocks come back, plus
one opaque ``provider_state`` block holding the reasoning items the next call
must send back.

Nothing here is an OpenAI-style Chat Completions path. Those live in
``llm_client`` for DeepSeek and Hugging Face and must stay separate.
"""
from __future__ import annotations

import json
from typing import Any, AsyncIterator, Dict, List, Optional

PROVIDER_STATE_TYPE = "provider_state"
OPENAI_PROVIDER = "openai"


class OpenAIResponsesError(Exception):
    """A Responses body or stream ended in a way the loop must not call done."""


class OpenAIAPIError(OpenAIResponsesError):
    """An HTTP error carrying only the status and OpenAI's error type/code.

    The raw body can name the organisation or a masked key tail, so it never
    travels in the exception text.
    """

    def __init__(self, status: Any, code: str):
        self.status = status
        self.code = code
        super().__init__(f"OpenAI Error {status}: {code}")


def error_from_body(status: Any, body: Any) -> OpenAIAPIError:
    """Build a safe HTTP error from an OpenAI error body."""
    text = body.decode("utf-8", errors="replace") if isinstance(body, bytes) else str(body or "")
    try:
        data = json.loads(text)
    except (json.JSONDecodeError, TypeError):
        data = {}
    error = data.get("error") if isinstance(data, dict) else {}
    if not isinstance(error, dict):
        error = {}
    parts = [str(part) for part in (error.get("type"), error.get("code")) if part]
    return OpenAIAPIError(status, "/".join(parts) or "error")


def _is_provider_state(block: Any) -> bool:
    return isinstance(block, dict) and block.get("type") == PROVIDER_STATE_TYPE


def strip_provider_state_blocks(messages: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Drop OpenAI ``provider_state`` blocks before a non-OpenAI provider sees them.

    The block carries encrypted reasoning that only OpenAI accepts. A switch
    away from OpenAI loses the reasoning, which is the documented trade-off;
    the rest of the message is kept.
    """
    cleaned: List[Dict[str, Any]] = []
    for message in messages or []:
        content = message.get("content")
        if not isinstance(content, list):
            cleaned.append(message)
            continue
        kept = [block for block in content if not _is_provider_state(block)]
        if not kept:
            continue
        if len(kept) == len(content):
            cleaned.append(message)
            continue
        replacement = dict(message)
        replacement["content"] = kept
        cleaned.append(replacement)
    return cleaned


def _as_text(value: Any) -> str:
    if isinstance(value, str):
        return value
    if value is None:
        return ""
    try:
        return json.dumps(value, default=str)
    except (TypeError, ValueError):
        return str(value)


def _parse_arguments(raw: Any) -> Dict[str, Any]:
    if isinstance(raw, dict):
        return raw
    try:
        parsed = json.loads(raw or "{}")
    except (json.JSONDecodeError, TypeError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _content_type(role: str) -> str:
    return "output_text" if role == "assistant" else "input_text"


def _strict_schema(schema: Any) -> bool:
    """True only for a schema OpenAI's strict tool mode accepts, at every level."""
    if not isinstance(schema, dict):
        return False
    schema_type = schema.get("type")
    if schema_type == "object":
        if schema.get("additionalProperties") is not False:
            return False
        properties = schema.get("properties")
        required = schema.get("required")
        if not isinstance(properties, dict) or not isinstance(required, list):
            return False
        if set(required) != set(properties):
            return False
        return all(_strict_schema(value) for value in properties.values())
    if schema_type == "array":
        return _strict_schema(schema.get("items"))
    return schema_type in {"string", "number", "integer", "boolean", "null"}


def tools_to_responses(tools: Optional[List[Dict[str, Any]]]) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for tool in tools or []:
        if not isinstance(tool, dict) or "input_schema" not in tool:
            continue
        schema = tool.get("input_schema") or {"type": "object", "properties": {}}
        out.append({
            "type": "function",
            "name": tool.get("name"),
            "description": tool.get("description", ""),
            "parameters": schema,
            "strict": _strict_schema(schema),
        })
    return out


def _drop_dangling_reasoning(items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Drop trailing reasoning items; the API rejects reasoning with nothing after it."""
    end = len(items)
    while end > 0 and items[end - 1].get("type") == "reasoning":
        end -= 1
    return items[:end]


def _assistant_items(
    content: List[Any], target_model: Optional[str],
) -> List[Dict[str, Any]]:
    state = next(
        (block for block in content if isinstance(block, dict) and _is_provider_state(block)),
        None,
    )
    raw_items = state.get("items") if isinstance(state, dict) else None
    if (
        isinstance(raw_items, list)
        and target_model
        and state.get("model") == target_model
    ):
        return _drop_dangling_reasoning(
            [item for item in raw_items if isinstance(item, dict)]
        )

    items: List[Dict[str, Any]] = []
    pending_text: List[str] = []

    def flush_text() -> None:
        text = "".join(pending_text)
        pending_text.clear()
        if text:
            items.append({
                "role": "assistant",
                "content": [{"type": "output_text", "text": text}],
            })

    for block in content:
        if not isinstance(block, dict):
            pending_text.append(str(block))
            continue
        block_type = block.get("type")
        if block_type == "text":
            pending_text.append(str(block.get("text", "")))
        elif block_type == "tool_use":
            flush_text()
            items.append({
                "type": "function_call",
                "call_id": str(block.get("id") or ""),
                "name": block.get("name"),
                "arguments": json.dumps(block.get("input", {}), default=str),
            })
        elif _is_provider_state(block):
            continue
    flush_text()
    return items


def _user_items(content: List[Any]) -> List[Dict[str, Any]]:
    items: List[Dict[str, Any]] = []
    pending_text: List[str] = []

    def flush_text() -> None:
        text = "".join(pending_text)
        pending_text.clear()
        if text:
            items.append({
                "role": "user",
                "content": [{"type": "input_text", "text": text}],
            })

    for block in content:
        if not isinstance(block, dict):
            pending_text.append(str(block))
            continue
        block_type = block.get("type")
        if block_type == "text":
            pending_text.append(str(block.get("text", "")))
        elif block_type == "tool_result":
            flush_text()
            items.append({
                "type": "function_call_output",
                "call_id": str(block.get("tool_use_id") or ""),
                "output": _as_text(block.get("content")),
            })
    flush_text()
    return items


def messages_to_input_items(
    messages: List[Dict[str, Any]], target_model: Optional[str] = None,
) -> List[Dict[str, Any]]:
    items: List[Dict[str, Any]] = []
    for message in messages or []:
        role = str(message.get("role") or "user")
        content = message.get("content")
        if isinstance(content, str):
            if content:
                items.append({
                    "role": role,
                    "content": [{"type": _content_type(role), "text": content}],
                })
            continue
        if not isinstance(content, list):
            text = _as_text(content)
            if text:
                items.append({
                    "role": role,
                    "content": [{"type": _content_type(role), "text": text}],
                })
            continue
        if role == "assistant":
            items.extend(_assistant_items(content, target_model))
        elif role == "user":
            items.extend(_user_items(content))
        else:
            text = "".join(
                str(block.get("text", ""))
                for block in content
                if isinstance(block, dict) and block.get("type") == "text"
            )
            if text:
                items.append({
                    "role": role,
                    "content": [{"type": _content_type(role), "text": text}],
                })
    return items


def build_responses_request(
    *,
    model: str,
    instructions: str,
    messages: List[Dict[str, Any]],
    tools: Optional[List[Dict[str, Any]]],
    temperature: float,
    max_tokens: int,
    reasoning_effort: str = "low",
) -> Dict[str, Any]:
    """Build one Responses API request body from Kazi's message contract."""
    effort = str(reasoning_effort or "low").strip().lower() or "low"
    body: Dict[str, Any] = {
        "model": model,
        "input": messages_to_input_items(messages, target_model=model),
        "store": False,
        "include": ["reasoning.encrypted_content"],
        "max_output_tokens": max_tokens,
        "reasoning": {"effort": effort},
    }
    if instructions:
        body["instructions"] = instructions
    if effort == "none":
        body["temperature"] = temperature
    tool_defs = tools_to_responses(tools)
    if tool_defs:
        body["tools"] = tool_defs
    return body


def _normalise_usage(usage: Optional[Dict[str, Any]]) -> Dict[str, int]:
    usage = usage or {}
    total_input = int(usage.get("input_tokens") or 0)
    details = usage.get("input_tokens_details") or {}
    cached = int(details.get("cached_tokens") or 0)
    written = int(details.get("cache_write_tokens") or 0)
    return {
        "input_tokens": max(0, total_input - cached - written),
        "output_tokens": int(usage.get("output_tokens") or 0),
        "cache_read_input_tokens": cached,
        "cache_creation_input_tokens": written,
    }


def _stop_reason(status: Any, incomplete_reason: Any, refusal: bool, has_tool_calls: bool) -> str:
    if status == "incomplete":
        if incomplete_reason == "max_output_tokens":
            return "max_tokens"
        if incomplete_reason == "content_filter":
            return "refusal"
        raise OpenAIResponsesError(
            f"OpenAI response ended incomplete ({incomplete_reason or 'unknown reason'})."
        )
    if status != "completed":
        raise OpenAIResponsesError(
            f"OpenAI response ended with status {status!r}."
        )
    if refusal:
        return "refusal"
    if has_tool_calls:
        return "tool_use"
    return "end_turn"


def parse_responses_body(
    data: Dict[str, Any], requested_model: Optional[str] = None,
) -> Dict[str, Any]:
    """Turn one Responses body into Kazi's Anthropic-shaped reply.

    The block records the model the request named, not the body's ``model``:
    OpenAI answers an alias with its dated snapshot, and replay compares
    against the next request's model.
    """
    if not isinstance(data, dict):
        raise OpenAIResponsesError("OpenAI returned a non-object response.")

    status = data.get("status")
    output = data.get("output") or []
    output_items = [item for item in output if isinstance(item, dict)]
    blocks: List[Dict[str, Any]] = []
    refusal = False
    has_tool_calls = False

    for item in output_items:
        item_type = item.get("type")
        if item_type == "reasoning":
            continue
        if item_type == "message":
            for part in item.get("content") or []:
                if not isinstance(part, dict):
                    continue
                if part.get("type") == "output_text":
                    blocks.append({"type": "text", "text": part.get("text", "")})
                elif part.get("type") == "refusal":
                    refusal = True
        elif item_type == "function_call":
            has_tool_calls = True
            blocks.append({
                "type": "tool_use",
                "id": str(item.get("call_id") or item.get("id") or ""),
                "name": item.get("name"),
                "input": _parse_arguments(item.get("arguments")),
            })
        elif item_type == "refusal":
            refusal = True

    stop_reason = _stop_reason(
        status, (data.get("incomplete_details") or {}).get("reason"),
        refusal, has_tool_calls,
    )

    if output_items:
        blocks.insert(0, {
            "type": PROVIDER_STATE_TYPE,
            "provider": OPENAI_PROVIDER,
            "model": requested_model or data.get("model"),
            "items": output_items,
        })
    if not blocks:
        blocks = [{"type": "text", "text": ""}]

    return {
        "id": data.get("id", "openai-response"),
        "type": "message",
        "role": "assistant",
        "content": blocks,
        "stop_reason": stop_reason,
        "usage": _normalise_usage(data.get("usage")),
    }


async def iter_responses_stream(
    lines: AsyncIterator[Any], requested_model: Optional[str] = None,
) -> AsyncIterator[Dict[str, Any]]:
    """Parse a Responses SSE stream into Kazi's event dicts.

    Text arrives as it streams. Tool events are buffered and yielded only after
    the terminal body proves the ending is ``tool_use``, so a cut-off or failed
    turn never exposes a partial tool call. The terminal event may appear at any
    position; it is evaluated after the stream ends, and a stream without one is
    an error, never a normal ``done``.
    """
    terminal: Optional[Dict[str, Any]] = None
    inputs: Dict[int, List[str]] = {}
    saw_text = False

    async for raw in lines:
        line = raw.decode("utf-8", errors="replace") if isinstance(raw, bytes) else str(raw)
        if not line.startswith("data: "):
            continue
        payload = line[6:].strip()
        if payload == "[DONE]":
            break
        try:
            event = json.loads(payload)
        except (json.JSONDecodeError, TypeError):
            continue
        if not isinstance(event, dict):
            continue
        event_type = event.get("type")

        if event_type == "response.output_text.delta":
            text = event.get("delta") or ""
            if text:
                saw_text = True
                yield {"type": "text", "text": text}

        elif event_type == "response.refusal.delta":
            text = event.get("delta") or ""
            if text:
                saw_text = True
                yield {"type": "text", "text": text}

        elif event_type == "response.function_call_arguments.delta":
            delta = event.get("delta") or ""
            if delta:
                index = int(event.get("output_index") or 0)
                inputs.setdefault(index, []).append(delta)

        elif event_type in ("response.completed", "response.incomplete", "response.failed"):
            response = event.get("response")
            terminal = response if isinstance(response, dict) else {"status": event_type.rsplit(".", 1)[-1]}

        elif event_type == "error":
            error = event.get("error") or {}
            parts = []
            if isinstance(error, dict):
                parts = [str(part) for part in (error.get("type"), error.get("code")) if part]
            raise OpenAIResponsesError(
                f"OpenAI stream error: {'/'.join(parts) or 'unknown error'}."
            )

    if terminal is None:
        raise OpenAIResponsesError(
            "OpenAI stream ended without a terminal response event."
        )

    parsed = parse_responses_body(terminal, requested_model)

    if not saw_text:
        text = "".join(
            block.get("text", "")
            for block in parsed["content"]
            if isinstance(block, dict) and block.get("type") == "text"
        )
        if text:
            yield {"type": "text", "text": text}

    if parsed["stop_reason"] == "tool_use":
        for index, item in enumerate(terminal.get("output") or []):
            if not isinstance(item, dict):
                continue
            if item.get("type") != "function_call":
                continue
            call_id = str(item.get("call_id") or item.get("id") or "")
            name = item.get("name") or ""
            yield {"type": "tool_use_start", "id": call_id, "name": name}
            for partial in inputs.get(index) or []:
                yield {"type": "tool_use_input", "partial_json": partial}
            yield {
                "type": "tool_use_end",
                "id": call_id,
                "name": name,
                "input": _parse_arguments(item.get("arguments")),
            }

    provider_state = next(
        (block for block in parsed["content"] if _is_provider_state(block)),
        None,
    )
    yield {
        "type": "message_done",
        "stop_reason": parsed["stop_reason"],
        "usage": parsed["usage"],
        "provider_state": provider_state,
    }


def text_from_result(result: Dict[str, Any]) -> str:
    """The visible text from one Anthropic-shaped reply (provider_state skipped)."""
    return "".join(
        str(block.get("text", ""))
        for block in result.get("content") or []
        if isinstance(block, dict) and block.get("type") == "text"
    )
