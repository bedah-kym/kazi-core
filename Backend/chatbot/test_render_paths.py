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
    def test_wiring_chatbot_template_is_not_referenced(self):
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

    def test_wiring_only_the_render_module_parses_and_sanitises(self):
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

    def test_render_module_wiring_has_a_text_content_fallback(self):
        path = HERE / "static" / "js" / "kazi-render.js"
        self.assertTrue(path.is_file(), "the shared render module is missing")
        source = _read(path)
        self.assertIn("textContent", source)
        self.assertIn("createTextNode", source)
        self.assertNotIn("createElement('span')", source)
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

    def test_wiring_ai_assistant_has_no_raw_message_paths(self):
        source = _read(HERE / "static" / "js" / "ai-assistant.js")
        main = _read(HERE / "static" / "js" / "main.js")
        self.assertNotIn("displayAIMessage", source)
        self.assertNotIn("data.command === 'ai_message'", source)
        self.assertNotIn("data.command === 'ai_message'", main)
        self.assertNotIn("${label", source)
        self.assertIn("kazi-step-label", source)

    def test_wiring_chatbase_pins_the_libraries_and_loads_the_renderer_first(self):
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
        self.assertRegex(purify_tag.group(1), r"/dompurify/3\.4\.17/purify\.min\.js")
        for tag in (marked_tag, purify_tag):
            self.assertIn("integrity=", tag.group(0))
            self.assertIn("crossorigin=", tag.group(0))
        render = source.index("js/kazi-render.js")
        self.assertLess(render, source.index("js/main.js"))
        self.assertLess(render, source.index("js/ai-assistant.js"))

    def test_render_module_wiring_shows_raw_html_as_literal_text(self):
        source = _read(HERE / "static" / "js" / "kazi-render.js")
        self.assertIn("function escapeHtml", source)
        html_renderer = re.search(
            r"html\s*:\s*function\s*\(token\)\s*\{(.*?)\}", source, re.DOTALL,
        )
        self.assertIsNotNone(html_renderer)
        self.assertRegex(html_renderer.group(1), r"return\s+escapeHtml\(")
        self.assertNotIn("return token.text;", source)
        self.assertNotIn("protectRawHtmlIndent", source)

    def test_render_module_wiring_allows_the_configured_media_origin(self):
        source = _read(HERE / "static" / "js" / "kazi-render.js")
        self.assertIn("function mediaOrigin", source)
        self.assertIn("new URL(base, global.location.href).origin", source)
        self.assertIn("new URL(src, global.location.href).origin === origin", source)

    def test_render_module_wiring_disables_raw_html_tokenizers(self):
        source = _read(HERE / "static" / "js" / "kazi-render.js")
        tokenizer = re.search(
            r"tokenizer\s*:\s*\{(.*?)\n\s*\}\n\s*\}\);", source, re.DOTALL,
        )
        self.assertIsNotNone(tokenizer)
        self.assertRegex(tokenizer.group(1), r"html\s*:\s*function\s*\(\)\s*\{\s*return undefined;")
        self.assertRegex(tokenizer.group(1), r"tag\s*:\s*function\s*\(\)\s*\{\s*return undefined;")

    def test_render_module_wiring_escapes_image_alt_text(self):
        source = _read(HERE / "static" / "js" / "kazi-render.js")
        image_renderer = re.search(
            r"image\s*:\s*function\s*\(token\)\s*\{(.*?)\n\s*\}", source, re.DOTALL,
        )
        self.assertIsNotNone(image_renderer)
        self.assertIn('alt="', image_renderer.group(1))
        self.assertRegex(image_renderer.group(1), r"escapeHtml\(alt\)")

    def test_render_module_wiring_fails_closed_when_configuring(self):
        source = _read(HERE / "static" / "js" / "kazi-render.js")
        self.assertIn("typeof marked.use !== 'function'", source)
        self.assertIn("if (!configureParser())", source)
        self.assertIn("plainText(container, original);", source)

    def test_render_module_wiring_recognises_only_upload_location_shapes(self):
        source = _read(HERE / "static" / "js" / "kazi-render.js")
        self.assertIn("var LEGACY_FILE_LINK = /^<a href='", source)
        self.assertIn("var LEGACY_FILE_LINK_DOUBLE = /^<a href=\"", source)
        self.assertIn(
            "var LEGACY_IMAGE_DOUBLE = /^<img src=\"([^\"<>\\n]+)\" "
            "alt=\"uploaded image\" \\/>$/;",
            source,
        )
        self.assertIn("var UPLOAD_PATH = /^(?:chat_uploads|documents)\\/", source)
        self.assertIn("global.KAZI_MEDIA_URL", source)
        self.assertIn("url.indexOf(base) !== 0", source)
        self.assertIn("legacyUploadMarkdown(raw)", source)
        self.assertIn("function legacyUploadMarkdown", source)
        self.assertIn("function escapeMarkdown", source)
        self.assertIn("container.innerHTML = clean;", source)

    def test_wiring_file_upload_sends_server_built_content(self):
        main = _read(HERE / "static" / "js" / "main.js")
        self.assertIn("const content = data && typeof data.content === 'string'", main)
        self.assertIn("message: content,", main)
        self.assertNotIn("alt=\"uploaded image\" />`", main)
        self.assertNotIn("<a href=\"${data.fileUrl}\"", main)

    def test_wiring_chatbase_passes_the_media_base_url(self):
        source = _read(HERE / "templates" / "chatbot" / "chatbase.html")
        self.assertIn('window.KAZI_MEDIA_URL = "{{ media_base_url|escapejs }}";', source)

    def test_render_module_wiring_keeps_list_numbers_and_task_markers(self):
        source = _read(HERE / "static" / "js" / "kazi-render.js")
        allowed_attr = re.search(r"ALLOWED_ATTR\s*=\s*\[(.*?)\];", source, re.DOTALL)
        self.assertIsNotNone(allowed_attr)
        self.assertIn("'start'", allowed_attr.group(1))
        self.assertRegex(source, r"tag\s*===\s*'OL'")
        self.assertIn("removeAttribute('start')", source)
        self.assertRegex(source, r"checkbox\s*:\s*function")
        self.assertIn(r"\u2611", source)
        self.assertIn(r"\u2610", source)
        forbid_tags = re.search(r"FORBID_TAGS\s*:\s*\[(.*?)\]", source, re.DOTALL)
        self.assertIsNotNone(forbid_tags)
        self.assertIn("'input'", forbid_tags.group(1))
