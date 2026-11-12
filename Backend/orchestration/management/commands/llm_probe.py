"""Probe one live LLM provider and record its contract fixtures.

The output is the normalised reply and usage, never the key, headers, or a raw
response body. ``--record-dir`` writes the raw 200 bodies (and SSE events) as
fixture files with ``"source": "recorded"`` so the contract suite can run them
through the same adapter; review them before committing.

Every case comes from ``orchestration.openai_fixtures.CASES`` so the probe and
the tests share one prompt/tool/max-token/effort spec. Refusal, 401, 429 and a
reasoning-only cut-off stay crafted; their reasons live in that module.

Run only by a person with a real key:

    python Backend/manage.py llm_probe --provider openai --record-dir <dir>
"""
from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any, Dict, List

import httpx
from django.core.management.base import BaseCommand, CommandError

from orchestration.llm_client import LLMClient
from orchestration.llm_openai import build_responses_request, error_from_body
from orchestration.openai_fixtures import CASES, messages_for, replay_messages


def _summary(result: Dict[str, Any]) -> Dict[str, Any]:
    blocks = []
    for block in result.get("content") or []:
        if not isinstance(block, dict):
            continue
        block_type = block.get("type")
        if block_type == "text":
            blocks.append({"type": "text", "text": block.get("text", "")})
        elif block_type == "tool_use":
            blocks.append({
                "type": "tool_use",
                "name": block.get("name"),
                "input": block.get("input"),
            })
        elif block_type == "provider_state":
            blocks.append({
                "type": "provider_state",
                "model": block.get("model"),
                "items": len(block.get("items") or []),
            })
        else:
            blocks.append({"type": str(block_type)})
    return {
        "stop_reason": result.get("stop_reason"),
        "usage": result.get("usage"),
        "blocks": blocks,
    }


def _safe_error(exc: Exception) -> str:
    head = str(exc).split(":", 1)[0].strip()
    return f"{exc.__class__.__name__}: {head or 'probe call failed'}"


def _stream_summary(events: List[Dict[str, Any]]) -> Dict[str, Any]:
    terminal = next(
        (event.get("response") for event in reversed(events)
         if event.get("type") in ("response.completed", "response.incomplete", "response.failed")),
        {},
    )
    return {
        "event_types": [event.get("type") for event in events],
        "status": (terminal or {}).get("status"),
        "usage": (terminal or {}).get("usage"),
    }


class Command(BaseCommand):
    help = "Run every OpenAI fixture case and print the normalised result."

    def add_arguments(self, parser):
        parser.add_argument("--provider", default="openai")
        parser.add_argument(
            "--record-dir",
            default="",
            help="Write raw 200 bodies and SSE events here as recorded fixtures.",
        )

    def handle(self, *args, **options):
        provider = str(options["provider"] or "").strip().lower()
        if provider != "openai":
            raise CommandError("llm_probe currently supports --provider openai only.")

        client = LLMClient()
        if not client.openai_key:
            raise CommandError("OPENAI_API_KEY is not configured.")

        try:
            summaries, fixtures = asyncio.run(self._probe(client))
        except Exception as exc:
            self.stderr.write(json.dumps({"provider": provider, "error": _safe_error(exc)}))
            raise CommandError("the probe call failed") from None

        self.stdout.write(json.dumps({
            "provider": provider,
            "model": client.openai_model,
            "cases": summaries,
        }, indent=2))

        record_dir = str(options.get("record_dir") or "").strip()
        if not record_dir:
            return
        target = Path(record_dir)
        target.mkdir(parents=True, exist_ok=True)
        for name, fixture in fixtures.items():
            (target / f"{name}.json").write_text(
                json.dumps(fixture, indent=2),
                encoding="utf-8",
            )
        self.stdout.write(
            f"Recorded {len(fixtures)} raw fixture(s) with source=recorded; "
            "review before committing."
        )

    async def _call(self, client, name: str, messages):
        case = CASES[name]
        client.openai_reasoning_effort = case["effort"]
        capture: Dict[str, Any] = {}
        result = await client._create_openai_responses(
            messages=messages,
            system=case["system"],
            tools=case["tools"] or None,
            temperature=0.3,
            max_tokens=case["max_output_tokens"],
            capture=capture,
        )
        return result, capture

    async def _raw_stream(self, client, name: str) -> List[Dict[str, Any]]:
        case = CASES[name]
        body = build_responses_request(
            model=client.openai_model,
            instructions=case["system"],
            messages=messages_for(case),
            tools=case["tools"] or None,
            temperature=0.3,
            max_tokens=case["max_output_tokens"],
            reasoning_effort=case["effort"],
        )
        body["stream"] = True
        events: List[Dict[str, Any]] = []
        async with httpx.AsyncClient(timeout=120.0) as http:
            async with http.stream(
                "POST",
                client.openai_url,
                headers={
                    "Authorization": f"Bearer {client.openai_key}",
                    "Content-Type": "application/json",
                },
                json=body,
            ) as response:
                if response.status_code != 200:
                    error_body = await response.aread()
                    raise error_from_body(response.status_code, error_body)
                async for line in response.aiter_lines():
                    if not line.startswith("data: "):
                        continue
                    payload = line[6:].strip()
                    if payload == "[DONE]":
                        break
                    try:
                        event = json.loads(payload)
                    except (json.JSONDecodeError, TypeError):
                        continue
                    if isinstance(event, dict):
                        events.append(event)
        return events

    async def _probe(self, client):
        summaries: Dict[str, Any] = {}
        fixtures: Dict[str, Any] = {}

        def record(name, capture):
            fixtures[name] = {
                "source": "recorded",
                "name": name,
                "http_status": 200,
                "body": capture["body"],
            }

        for name in ("plain_answer", "tool_call", "parallel_tool_calls"):
            result, capture = await self._call(client, name, messages_for(CASES[name]))
            summaries[name] = _summary(result)
            record(name, capture)

        prior = fixtures["tool_call"]["body"]
        result, capture = await self._call(
            client,
            "reasoning_replay",
            replay_messages(prior, CASES["tool_call"]["prompt"]),
        )
        summary = _summary(result)
        summary["request_input_items"] = len(capture["request"]["input"])
        summaries["reasoning_replay"] = summary
        fixtures["reasoning_replay"] = {
            "source": "recorded",
            "name": "reasoning_replay",
            "http_status": 200,
            "request_input": capture["request"]["input"],
            "body": capture["body"],
        }

        for name in ("cut_off_max_output_tokens",):
            result, capture = await self._call(client, name, messages_for(CASES[name]))
            summaries[name] = _summary(result)
            record(name, capture)

        name = "stream_answer_with_tool_call"
        events = await self._raw_stream(client, name)
        summaries[name] = _stream_summary(events)
        fixtures[name] = {
            "source": "recorded",
            "name": name,
            "http_status": 200,
            "stream": True,
            "events": events,
        }

        return summaries, fixtures
