"""Exact-shape tests for the approval card builders."""
from __future__ import annotations

import json
import re
from datetime import date
from decimal import Decimal
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
RENDER_EXPECTED = (
    Path(__file__).resolve().parent.parent
    / "chatbot"
    / "testdata"
    / "render_expected.json"
)
_FENCE_LINE = re.compile(r"^(`{3,})text$")
_INLINE = re.compile(r"^(`+)(.*?)\1")
_MARKER_RE = re.compile("\u27e8U\\+([0-9A-F]{4,6})\u27e9")


def _decode_markers(text: str) -> str:
    return _MARKER_RE.sub(lambda match: chr(int(match.group(1), 16)), text)


def _label_key(label: str) -> str:
    match = _INLINE.match(label)
    if match is None:
        raise AssertionError(f"no inline key in label: {label!r}")
    content = match.group(2)
    if len(content) >= 2 and content.startswith(" ") and content.endswith(" "):
        content = content[1:-1]
    return _decode_markers(content)


def _parameter_blocks(card: str):
    lines = card.split("\n")
    blocks = []
    index = 0
    while index < len(lines):
        opener = _FENCE_LINE.match(lines[index])
        if opener is None:
            index += 1
            continue
        fence = opener.group(1)
        label_index = index - 1
        while label_index >= 0 and lines[label_index].strip() == "":
            label_index -= 1
        if label_index < 0:
            raise AssertionError("fenced block has no label")
        close_index = None
        for probe in range(index + 1, len(lines)):
            if lines[probe] == fence:
                close_index = probe
                break
        if close_index is None:
            raise AssertionError("unclosed fenced block")
        body = "\n".join(lines[index + 1:close_index])
        blocks.append((_label_key(lines[label_index]), _decode_markers(body)))
        index = close_index + 1
    return blocks


def _visible_label_key(card: str) -> str:
    """The key as a browser shows it once Marked renders the label's code span.

    Models CommonMark 0.31.2 code-span normalisation (one space stripped from
    each end when both ends are spaces) and HTML collapsing of whitespace runs
    in inline text. The real-library check is in the PR's browser run.
    """
    label = card.split("\n")[1]
    match = _INLINE.match(label)
    if match is None:
        raise AssertionError(f"no inline key in label: {label!r}")
    content = match.group(2).replace("\n", " ")
    if len(content) >= 2 and content.startswith(" ") and content.endswith(" ") and content.strip(" "):
        content = content[1:-1]
    return re.sub(r"[ \t]+", " ", content)


def _display(value):
    if isinstance(value, str):
        return value
    return json.dumps(value, ensure_ascii=False, indent=2)


class ConfirmationPromptTests(SimpleTestCase):
    def test_value_is_shown_verbatim(self):
        value = (
            '<style>body{color:red}</style>\n'
            '<img src="https://example.test/p.png" alt="x">'
        )
        expected = (
            "I'd like to `run command` with the following details:\n"
            "`command` (text) \u2014 77 characters, 2 lines:\n"
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
            "`command` (text) \u2014 27 characters, 1 line:\n"
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
            "`body` (text) \u2014 46 characters, 3 lines:\n"
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
            "`body` (text) \u2014 10 characters, 2 lines; contains 5 hidden characters, "
            "shown as \u27e8U+\u2026\u27e9:\n"
            "```text\n"
            f"{shown}\n"
            "```\n\n"
            "Should I go ahead? (yes / no)"
        )
        self.assertEqual(build_confirmation_prompt("send_email", {"body": value}), expected)

    def test_default_ignorable_characters_get_markers(self):
        value = "\u034f\u115f\u3164\uffa0\ufe0f\U000E0100"
        shown = (
            "\u27e8U+034F\u27e9\u27e8U+115F\u27e9\u27e8U+3164\u27e9"
            "\u27e8U+FFA0\u27e9\u27e8U+FE0F\u27e9\u27e8U+E0100\u27e9"
        )
        expected = (
            "I'd like to `send email` with the following details:\n"
            "`body` (text) \u2014 6 characters, 1 line; contains 6 hidden characters, "
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
            "`command` (text) \u2014 100000 characters, 1 line:\n"
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
            f"`payload` (JSON) \u2014 {len(shown)} characters, {shown.count(chr(10)) + 1} lines:\n"
            "```text\n"
            f"{shown}\n"
            "```\n\n"
            "Should I go ahead? (yes / no)"
        )
        self.assertEqual(build_confirmation_prompt("save_note", {"payload": value}), expected)

    def test_keys_and_tool_names_are_inline_code(self):
        expected = (
            "I'd like to ``run`cmd<b>`` with the following details:\n"
            "``a`b<c>`` (text) \u2014 1 character, 1 line:\n"
            "```text\n"
            "x\n"
            "```\n\n"
            "Should I go ahead? (yes / no)"
        )
        self.assertEqual(
            build_confirmation_prompt("run`cmd<b>", {"a`b<c>": "x"}), expected,
        )

    def test_boundary_and_repeated_spaces_in_a_key_are_marked(self):
        expected = (
            "I'd like to `send email` with the following details:\n"
            "`⟨U+0020⟩to⟨U+0020⟩` (text) — 1 character, 1 line:\n"
            "```text\n"
            "x\n"
            "```\n\n"
            "Should I go ahead? (yes / no)"
        )
        self.assertEqual(build_confirmation_prompt("send_email", {" to ": "x"}), expected)
        card = build_confirmation_prompt("send_email", {"a  b": "x"})
        self.assertEqual(
            card.split("\n")[1].split(" (text)")[0],
            "`a⟨U+0020⟩⟨U+0020⟩b`",
        )

    def test_keys_that_differ_only_in_spaces_stay_distinct_when_rendered(self):
        keys = ["to", " to", "to ", " to ", "a b", "a  b", " ", "   "]
        shown = [
            _visible_label_key(build_confirmation_prompt("send_email", {key: "x"}))
            for key in keys
        ]
        self.assertEqual(len(set(shown)), len(keys), shown)

    def test_a_key_with_a_line_break_stays_on_one_line(self):
        expected = (
            "I'd like to `send email` with the following details:\n"
            "`a\u27e8U+000A\u27e9b` (text) \u2014 1 character, 1 line:\n"
            "```text\n"
            "x\n"
            "```\n\n"
            "Should I go ahead? (yes / no)"
        )
        self.assertEqual(build_confirmation_prompt("send_email", {"a\nb": "x"}), expected)

    def test_a_key_with_a_line_break_before_markup_stays_on_one_line(self):
        expected = (
            "I'd like to `send email` with the following details:\n"
            "`a\u27e8U+000A\u27e9<!--` (text) \u2014 1 character, 1 line:\n"
            "```text\n"
            "x\n"
            "```\n\n"
            "Should I go ahead? (yes / no)"
        )
        self.assertEqual(build_confirmation_prompt("send_email", {"a\n<!--": "x"}), expected)

    def test_a_tool_name_with_a_line_break_stays_on_one_line(self):
        expected = (
            "I'd like to `run\u27e8U+000A\u27e9cmd` with the following details:\n"
            "`x` (text) \u2014 1 character, 1 line:\n"
            "```text\n"
            "y\n"
            "```\n\n"
            "Should I go ahead? (yes / no)"
        )
        self.assertEqual(build_confirmation_prompt("run\ncmd", {"x": "y"}), expected)

    def test_a_batch_keeps_breaks_in_keys_and_tool_names_on_one_line(self):
        calls = [
            {"name": "run\ncmd", "input": {"a\nb": "x"}},
            {"name": "send\nmail", "input": {"a\n<!--": "y"}},
        ]
        expected = (
            "I'd like to run **2 actions** together, in this order:\n"
            "\n"
            "### 1. `run\u27e8U+000A\u27e9cmd`\n"
            "\n"
            "`a\u27e8U+000A\u27e9b` (text) \u2014 1 character, 1 line:\n"
            "```text\n"
            "x\n"
            "```\n"
            "\n"
            "### 2. `send\u27e8U+000A\u27e9mail`\n"
            "\n"
            "`a\u27e8U+000A\u27e9<!--` (text) \u2014 1 character, 1 line:\n"
            "```text\n"
            "y\n"
            "```\n"
            "\n"
            "Should I go ahead with all of them? (yes / no)"
        )
        self.assertEqual(build_confirmation_prompt_many(calls), expected)

    def test_a_trailing_line_break_and_trailing_spaces_are_visible(self):
        cards = {}
        for value in ("a", "a\n", "a ", ""):
            cards[value] = build_confirmation_prompt("send_email", {"v": value})
        self.assertEqual(len(set(cards.values())), 4)
        self.assertIn("```text\na\u27e8U+000A\u27e9\n```", cards["a\n"])
        self.assertIn("```text\na\u27e8U+0020\u27e9\n```", cards["a "])
        self.assertIn("```text\n\n```", cards[""])
        self.assertIn("```text\na\n```", cards["a"])

    def test_a_value_that_is_only_a_line_break_is_distinct(self):
        newline_card = build_confirmation_prompt("send_email", {"v": "\n"})
        empty_card = build_confirmation_prompt("send_email", {"v": ""})
        self.assertNotEqual(newline_card, empty_card)
        self.assertIn("```text\n\u27e8U+000A\u27e9\n```", newline_card)

    def test_text_and_json_values_are_labelled(self):
        pairs = [
            ("payload", "false", "text"),
            ("payload", False, "JSON"),
            ("payload", "123", "text"),
            ("payload", 123, "JSON"),
            ("payload", "null", "text"),
            ("payload", None, "JSON"),
        ]
        for key, value, kind in pairs:
            with self.subTest(value=value, kind=kind):
                card = build_confirmation_prompt("save_note", {key: value})
                self.assertIn(f"`{key}` ({kind}) \u2014 ", card)
                blocks = _parameter_blocks(card)
                self.assertEqual(blocks, [(key, _display(value))])

    def test_fallback_values_are_labelled(self):
        cases = [
            ("when", date(2026, 11, 13), "2026-11-13"),
            ("amount", Decimal("1.50"), "1.50"),
            ("raw", b"abc", "b'abc'"),
            ("tags", {1}, "{1}"),
        ]
        for key, value, shown in cases:
            with self.subTest(value=value):
                card = build_confirmation_prompt("save_note", {key: value})
                self.assertIn(f"`{key}` (fallback text) \u2014 ", card)
                self.assertEqual(_parameter_blocks(card), [(key, shown)])

    def test_a_non_string_key_is_shown(self):
        card = build_confirmation_prompt("save_note", {1: "x"})
        self.assertIn("`1` (text) \u2014 1 character, 1 line:", card)
        self.assertEqual(_parameter_blocks(card), [("1", "x")])

    def test_an_empty_value_is_labelled(self):
        expected = (
            "I'd like to `send email` with the following details:\n"
            "`body` (text) \u2014 (empty), 0 characters, 1 line:\n"
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
            "`command` (text) \u2014 8 characters, 1 line:\n"
            "```text\n"
            "echo one\n"
            "```\n"
            "\n"
            "### 2. `run command`\n"
            "\n"
            "`command` (text) \u2014 8 characters, 1 line:\n"
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

    def test_rendered_cards_match_the_builder(self):
        values = {
            "approval_div_value": "<div>\n  <p>x</p>\n    <span>y</span>\n</div>",
            "approval_comment_value": "<!--\n  note\n-->",
            "approval_script_value": "<script>\n  alert(1)\n</script>",
        }
        cases = {
            case["id"]: case
            for case in json.loads(RENDER_EXPECTED.read_text(encoding="utf-8"))
        }
        for case_id, value in values.items():
            with self.subTest(case=case_id):
                self.assertEqual(
                    cases[case_id]["input"],
                    build_confirmation_prompt("run_command", {"command": value}),
                )
                self.assertNotIn("&nbsp;", cases[case_id]["html"])

    def test_round_trip_over_the_corpus(self):
        cases = json.loads(CORPUS.read_text(encoding="utf-8"))["cases"]
        for case in cases:
            with self.subTest(case=case["id"]):
                card = build_confirmation_prompt("run_command", {"value": case["input"]})
                self.assertEqual(
                    _parameter_blocks(card), [("value", _display(case["input"]))],
                )

    def test_round_trip_with_varied_keys(self):
        tool_input = {
            "a\nb": "one",
            "a`b": "two",
            "<b>": "three",
            7: "four",
            "nested": "```\ninner\n```",
        }
        card = build_confirmation_prompt("save_note", tool_input)
        self.assertEqual(
            _parameter_blocks(card),
            [(str(key), value) for key, value in tool_input.items()],
        )

    def test_round_trip_over_a_batch(self):
        calls = [
            {"name": "run\ncmd", "input": {"first": "one", "a`b": "two"}},
            {"name": "send\nmail", "input": {"first": "three", "<b>": "four"}},
        ]
        expected = [
            ("first", "one"), ("a`b", "two"),
            ("first", "three"), ("<b>", "four"),
        ]
        self.assertEqual(_parameter_blocks(build_confirmation_prompt_many(calls)), expected)
