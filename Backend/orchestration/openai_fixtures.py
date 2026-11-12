"""Request specs shared by the OpenAI probe and its contract tests.

Changing a prompt or tool here changes what the probe records and what the
tests expect, so a recorded fixture swaps in mechanically. Refusal, 401 and 429
stay crafted: there is no safe, deterministic way to make the live API produce
them on demand (and a 401/429 probe would spend or need a bad key).
"""
from __future__ import annotations

from typing import Any, Dict, List

PROBE_SYSTEM = "You are a connectivity probe. Answer exactly once."

WEATHER_TOOL = {
    "name": "get_weather",
    "description": "Get the current weather for a city.",
    "input_schema": {
        "type": "object",
        "properties": {"city": {"type": "string", "description": "City name."}},
        "required": ["city"],
    },
}

CASES: Dict[str, Dict[str, Any]] = {
    "plain_answer": {
        "prompt": "Reply with the single word: pong.",
        "system": PROBE_SYSTEM,
        "tools": [],
        "max_output_tokens": 256,
        "effort": "low",
    },
    "tool_call": {
        "prompt": "Call get_weather once for Nairobi, then stop.",
        "system": PROBE_SYSTEM,
        "tools": [WEATHER_TOOL],
        "max_output_tokens": 512,
        "effort": "high",
    },
    "parallel_tool_calls": {
        "prompt": (
            "Think first, then call get_weather for Nairobi and Mombasa in "
            "parallel, then stop."
        ),
        "system": PROBE_SYSTEM,
        "tools": [WEATHER_TOOL],
        "max_output_tokens": 1024,
        "effort": "high",
    },
    "reasoning_replay": {
        "prompt": "Use the weather results above and answer in one sentence.",
        "system": PROBE_SYSTEM,
        "tools": [WEATHER_TOOL],
        "max_output_tokens": 512,
        "effort": "high",
    },
    "cut_off_max_output_tokens": {
        "prompt": "Write a long, detailed history of Nairobi.",
        "system": PROBE_SYSTEM,
        "tools": [],
        "max_output_tokens": 16,
        "effort": "low",
    },
    "stream_answer_with_tool_call": {
        "prompt": "Call get_weather once for Nairobi, then stop.",
        "system": PROBE_SYSTEM,
        "tools": [WEATHER_TOOL],
        "max_output_tokens": 512,
        "effort": "low",
        "stream": True,
    },
}

CRAFTED_ONLY = {
    "refusal": "A real refusal cannot be forced without content risk.",
    "reasoning_only_cut_off": (
        "Whether reasoning alone consumes the tiny output limit depends on the "
        "model, so this shape is crafted rather than recorded."
    ),
    "unauthorized_401": "A real 401 needs an invalid key, which this probe must not use.",
    "rate_limit_429": "A real 429 depends on account limits and would be flaky.",
}


def messages_for(case: Dict[str, Any]) -> List[Dict[str, Any]]:
    return [{"role": "user", "content": case["prompt"]}]


def replay_messages(prior_body: Dict[str, Any], prior_prompt: str) -> List[Dict[str, Any]]:
    """The second call after a tool step: prompt, OpenAI's reply, tool outputs."""
    from orchestration.llm_openai import parse_responses_body

    prior = parse_responses_body(prior_body)
    results = [
        {
            "type": "tool_result",
            "tool_use_id": item.get("call_id") or item.get("id"),
            "content": "23C",
        }
        for item in prior_body.get("output") or []
        if isinstance(item, dict) and item.get("type") == "function_call"
    ]
    return [
        {"role": "user", "content": prior_prompt},
        {"role": "assistant", "content": prior["content"]},
        {"role": "user", "content": results},
    ]
