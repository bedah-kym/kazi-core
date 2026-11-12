"""Probe one live LLM provider with a plain call and a tool call.

The output is the normalised reply and usage, never the key, headers, or a raw
response body. ``--record-dir`` writes the raw bodies as fixture files with
``"source": "recorded"`` so the contract suite can run them through the same
adapter; review them before committing.

Run only by a person with a real key:

    python Backend/manage.py llm_probe --provider openai --record-dir <dir>
"""
from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any, Dict

from django.core.management.base import BaseCommand, CommandError

from orchestration.llm_client import LLMClient

ECHO_TOOL = {
    "name": "echo",
    "description": "Echo the given text back.",
    "input_schema": {
        "type": "object",
        "properties": {"text": {"type": "string", "description": "Text to echo."}},
        "required": ["text"],
        "additionalProperties": False,
    },
}


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
            blocks.append({"type": "provider_state", "items": len(block.get("items") or [])})
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


class Command(BaseCommand):
    help = "Run one plain and one tool call against a provider and print the normalised result."

    def add_arguments(self, parser):
        parser.add_argument("--provider", default="openai")
        parser.add_argument(
            "--record-dir",
            default="",
            help="Write raw response bodies here as recorded contract fixtures.",
        )

    def handle(self, *args, **options):
        provider = str(options["provider"] or "").strip().lower()
        if provider != "openai":
            raise CommandError("llm_probe currently supports --provider openai only.")

        client = LLMClient()
        if not client.openai_key:
            raise CommandError("OPENAI_API_KEY is not configured.")

        try:
            plain_raw: Dict[str, Any] = {}
            plain = asyncio.run(client._create_openai_responses(
                messages=[{"role": "user", "content": "Reply with the single word: pong."}],
                system="You are a connectivity probe. Answer exactly once.",
                tools=None,
                temperature=0.3,
                max_tokens=256,
                capture=plain_raw,
            ))
            tool_raw: Dict[str, Any] = {}
            tool = asyncio.run(client._create_openai_responses(
                messages=[{
                    "role": "user",
                    "content": "Call the echo tool once with text 'ping', then stop.",
                }],
                system="You are a connectivity probe.",
                tools=[ECHO_TOOL],
                temperature=0.3,
                max_tokens=512,
                capture=tool_raw,
            ))
        except Exception as exc:
            self.stderr.write(json.dumps({"provider": provider, "error": _safe_error(exc)}))
            raise CommandError("the probe call failed") from None

        self.stdout.write(json.dumps({
            "provider": provider,
            "model": client.openai_model,
            "plain": _summary(plain),
            "tool": _summary(tool),
        }, indent=2))

        record_dir = str(options.get("record_dir") or "").strip()
        if not record_dir:
            return
        target = Path(record_dir)
        target.mkdir(parents=True, exist_ok=True)
        for name, raw in (
            ("plain_answer", plain_raw.get("body")),
            ("tool_call", tool_raw.get("body")),
        ):
            if raw is None:
                continue
            (target / f"{name}.json").write_text(
                json.dumps({
                    "source": "recorded",
                    "name": name,
                    "http_status": 200,
                    "body": raw,
                }, indent=2),
                encoding="utf-8",
            )
        self.stdout.write(
            f"Recorded raw bodies for {', '.join(sorted(p.name for p in target.glob('*.json')))} "
            f"with source=recorded; review before committing."
        )
