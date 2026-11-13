"""Exact-shape tests for the approval card builders."""
from __future__ import annotations

import json
import re
from pathlib import Path

from django.test import SimpleTestCase

from orchestration.agent_prompts import (
    build_confirmation_prompt,
    build_confirmation_prompt_many,
)

CORPUS = (
    Path(__file__).resolve().parent.parent
    / "chatbot"
    / "testdata"
    / "render_corpus.json"
)
_FENCE_RE = re.compile(r"^(`{3,})text\n(.*?)\n\1$", re.MULTILINE | re.DOTALL)
_MARKER_RE = re.compile("\u27e8U\\+([0-9A-F]{4,6})\u27e9")


def _decode_block(card: str) -> str:
    match = _FENCE_RE.search(card)
    if match is None:
        raise AssertionError("no fenced text block in card")
    body = match.group(2)
    return _MARKER_RE.sub(lambda m: chr(int(m.group(1), 16)), body)


class ConfirmationPromptTests(SimpleTestCase):
    def test_value_is_shown_verbatim(self):
        value = (
            '<style>body{color:red}</style>\n'
            '<img src="https://example.test/p.png" alt="x">'
        )
        expected = (
            "I'd like to `run command` with the following details:\n"
            "`command` \u2014 77 characters, 2 lines:\n"
            "```text\n"
            f"{value}\n"
            "```\n\n"
            "Should I go ahead? (yes / no)"
        )
        self.assertEqual(build_confirmation_prompt("run_command", {"command": value}), expected)

    def test_fence_is_longer_than_any_backtick_run(self):
        value = "`a</code></pre>`" + "```" + "x" + "```````"
        fence = "`" * 8
        expected = (
            "I'd like to `run command` with the following details:\n"
            "`command` \u2014 27 characters, 1 line:\n"
            f"{fence}text\n"
            f"{value}\n"
            f"{fence}\n\n"
            "Should I go ahead? (yes / no)"
        )
        self.assertEqual(build_confirmation_prompt("run_command", {"command": value}), expected)

    def test_markdown_inside_a_value_stays_inside_the_block(self):
        value = "**bold** [x](http://x) # h\n- item\n    indented"
        expected = (
            "I'd like to `send email` with the following details:\n"
            "`body` \u2014 46 characters, 3 lines:\n"
            "```text\n"
            f"{value}\n"
            "```\n\n"
            "Should I go ahead? (yes / no)"
        )
        self.assertEqual(build_confirmation_prompt("send_email", {"body": value}), expected)

    def test_hidden_characters_get_a_marker_and_a_count(self):
        value = "a\u202eb\u200b\r\nc\u00a0d\u27e8"
        shown = (
            "a\u27e8U+202E\u27e9b\u27e8U+200B\u27e9\u27e8U+000D\u27e9\n"
            "c\u27e8U+00A0\u27e9d\u27e8U+27E8\u27e9"
        )
        expected = (
            "I'd like to `send email` with the following details:\n"
            "`body` \u2014 10 characters, 2 lines; contains 5 hidden characters, "
            "shown as \u27e8U+\u2026\u27e9:\n"
            "```text\n"
            f"{shown}\n"
            "```\n\n"
            "Should I go ahead? (yes / no)"
        )
        self.assertEqual(build_confirmation_prompt("send_email", {"body": value}), expected)

    def test_a_large_value_is_shown_in_full(self):
        value = "x" * 100_000
        card = build_confirmation_prompt("run_command", {"command": value})
        expected = (
            "I'd like to `run command` with the following details:\n"
            "`command` \u2014 100000 characters, 1 line:\n"
            "```text\n"
            f"{value}\n"
            "```\n\n"
            "Should I go ahead? (yes / no)"
        )
        self.assertEqual(card, expected)
        self.assertGreaterEqual(len(card), len(value))

    def test_a_dict_value_is_shown_as_indented_json(self):
        value = {"a": 1, "b": ["x", "y"]}
        shown = json.dumps(value, ensure_ascii=False, indent=2)
        expected = (
            "I'd like to `save note` with the following details:\n"
            f"`payload` \u2014 {len(shown)} characters, {shown.count(chr(10)) + 1} lines:\n"
            "```text\n"
            f"{shown}\n"
            "```\n\n"
            "Should I go ahead? (yes / no)"
        )
        self.assertEqual(build_confirmation_prompt("save_note", {"payload": value}), expected)

    def test_keys_and_tool_names_are_inline_code(self):
        expected = (
            "I'd like to ``run`cmd<b>`` with the following details:\n"
            "``a`b<c>`` \u2014 1 character, 1 line:\n"
            "```text\n"
            "x\n"
            "```\n\n"
            "Should I go ahead? (yes / no)"
        )
        self.assertEqual(
            build_confirmation_prompt("run`cmd<b>", {"a`b<c>": "x"}), expected,
        )

    def test_an_empty_value_is_labelled(self):
        expected = (
            "I'd like to `send email` with the following details:\n"
            "`body` \u2014 (empty), 0 characters, 1 line:\n"
            "```text\n"
            "\n"
            "```\n\n"
            "Should I go ahead? (yes / no)"
        )
        self.assertEqual(build_confirmation_prompt("send_email", {"body": ""}), expected)

    def test_a_batch_gives_each_call_its_own_blocks(self):
        calls = [
            {"name": "run_command", "input": {"command": "echo one"}},
            {"name": "run_command", "input": {"command": "echo two"}},
        ]
        expected = (
            "I'd like to run **2 actions** together, in this order:\n"
            "\n"
            "### 1. `run command`\n"
            "\n"
            "`command` \u2014 8 characters, 1 line:\n"
            "```text\n"
            "echo one\n"
            "```\n"
            "\n"
            "### 2. `run command`\n"
            "\n"
            "`command` \u2014 8 characters, 1 line:\n"
            "```text\n"
            "echo two\n"
            "```\n"
            "\n"
            "Should I go ahead with all of them? (yes / no)"
        )
        self.assertEqual(build_confirmation_prompt_many(calls), expected)

    def test_batch_keeps_the_approval_id(self):
        calls = [
            {"name": "send_email", "input": {"to": "a@b.c"}},
            {"name": "send_email", "input": {"to": "d@e.f"}},
        ]
        card = build_confirmation_prompt_many(calls, short_id="abc12345")
        self.assertTrue(card.endswith("\n\n(approval id abc12345)"))

    def test_round_trip_over_the_corpus(self):
        cases = json.loads(CORPUS.read_text(encoding="utf-8"))["cases"]
        for case in cases:
            with self.subTest(case=case["id"]):
                card = build_confirmation_prompt("run_command", {"value": case["input"]})
                self.assertEqual(_decode_block(card), case["input"])
