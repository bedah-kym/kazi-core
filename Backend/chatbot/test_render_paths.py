"""Static wiring checks for the shipped chat renderer.

These tests read the shipped JS and template files. They check wiring, not
rendering behaviour.
"""
from __future__ import annotations

import re
from pathlib import Path

from django.test import SimpleTestCase

HERE = Path(__file__).resolve().parent
BACKEND = HERE.parent
ROOT = BACKEND.parent


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


class RenderPathWiringTests(SimpleTestCase):
    def test_chatbot_template_is_not_referenced(self):
        files = list(BACKEND.rglob("views.py")) + list(BACKEND.rglob("urls.py"))
        files += [
            path for path in (HERE / "templates").rglob("*.html")
            if path.name != "chatbot.html"
        ]
        needles = ("chatbot/chatbot.html", '"chatbot.html"', "'chatbot.html'")
        offenders = [
            str(path.relative_to(ROOT))
            for path in files
            if any(needle in _read(path) for needle in needles)
        ]
        self.assertEqual(offenders, [])

    def test_only_the_render_module_parses_and_sanitises(self):
        offenders = []
        for path in (HERE / "static" / "js").glob("*.js"):
            if path.name == "kazi-render.js":
                continue
            text = _read(path)
            if "marked.parse" in text or "DOMPurify.sanitize" in text:
                offenders.append(path.name)
        for path in (HERE / "templates").rglob("*.html"):
            if path.name == "chatbot.html":
                continue
            text = _read(path)
            if "marked.parse" in text or "DOMPurify.sanitize" in text:
                offenders.append(str(path.relative_to(HERE)))
        self.assertEqual(offenders, [])

    def test_render_module_falls_back_to_plain_text(self):
        path = HERE / "static" / "js" / "kazi-render.js"
        self.assertTrue(path.is_file(), "the shared render module is missing")
        source = _read(path)
        self.assertIn("textContent", source)
        inner_html_lines = [
            line.strip() for line in source.splitlines() if "innerHTML" in line
        ]
        self.assertTrue(inner_html_lines)
        for line in inner_html_lines:
            self.assertRegex(line, r"innerHTML\s*=\s*clean\s*;")
        allowed_attr = re.search(
            r"ALLOWED_ATTR\s*=\s*\[(.*?)\];", source, re.DOTALL,
        )
        self.assertIsNotNone(allowed_attr)
        self.assertNotIn("style", allowed_attr.group(1))
        forbid_tags = re.search(
            r"FORBID_TAGS\s*:\s*\[(.*?)\]", source, re.DOTALL,
        )
        self.assertIsNotNone(forbid_tags)
        self.assertIn("style", forbid_tags.group(1))
        self.assertIn("ALLOW_DATA_ATTR: false", source)

    def test_chatbase_pins_the_libraries_and_loads_the_renderer_first(self):
        source = _read(HERE / "templates" / "chatbot" / "chatbase.html")
        marked_tag = re.search(
            r"<script[^>]*src=\"([^\"]*marked[^\"]*)\"[^>]*>", source,
        )
        purify_tag = re.search(
            r"<script[^>]*src=\"([^\"]*dompurify[^\"]*)\"[^>]*>", source, re.IGNORECASE,
        )
        self.assertIsNotNone(marked_tag)
        self.assertIsNotNone(purify_tag)
        self.assertRegex(marked_tag.group(1), r"marked@\d+\.\d+\.\d+/marked\.min\.js")
        for tag in (marked_tag, purify_tag):
            self.assertIn("integrity=", tag.group(0))
            self.assertIn("crossorigin=", tag.group(0))
        render = source.index("js/kazi-render.js")
        self.assertLess(render, source.index("js/main.js"))
        self.assertLess(render, source.index("js/ai-assistant.js"))
