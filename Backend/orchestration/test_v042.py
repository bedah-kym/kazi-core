"""Unit tests for v0.4.2 pure logic — fail-plausible guard, DeepSeek adapter,
URL placeholder detection, and booking links."""
import json
import threading
import time
from datetime import datetime

from django.test import SimpleTestCase

from orchestration.connectors.itinerary_connector import _is_placeholder_url
from orchestration.llm_client import LLMClient
from orchestration.security_policy import _SECRET_PATTERNS, redact_sensitive_text
from orchestration.tool_executor import _normalize_error


def _repeat(part, length):
    return (part * ((length // len(part)) + 1))[:length]


def _substrings(value, size):
    return {value[index:index + size] for index in range(len(value) - size + 1)}


class RedactSensitiveTextTests(SimpleTestCase):
    def test_redacts_api_key(self):
        out = redact_sensitive_text("key sk-abcdefghijklmnop123456 done")
        self.assertIn("[REDACTED SECRET]", out)
        self.assertNotIn("sk-abcdefghijklmnop", out)

    def test_redacts_bearer_token(self):
        out = redact_sensitive_text("Authorization: Bearer abcdefghijklmnopqrstuvwxyz123456")
        self.assertIn("[REDACTED SECRET]", out)

    def test_redacts_assignment_secret(self):
        out = redact_sensitive_text("api_key=SECRET123 password=hunter2")
        self.assertIn("[REDACTED SECRET]", out)

    def test_redacts_email_and_phone(self):
        out = redact_sensitive_text("contact joe@example.com or +254 712 345 678")
        self.assertIn("[REDACTED EMAIL]", out)
        self.assertIn("[REDACTED PHONE]", out)

    def test_heading_does_not_redact_the_next_line(self):
        cases = [
            "router password:\n1. Open the admin page",
            "api_key:\n1. Open the admin page",
            "secret=\n1. Open the admin page",
            "password:\r\n1. Open the admin page",
            "Bearer\nabcdefghijklmnopqrstuvwxyz123456",
        ]
        for text in cases:
            with self.subTest(text=text):
                self.assertEqual(redact_sensitive_text(text), text)

    def test_single_line_secrets_are_still_redacted(self):
        cases = [
            "password: hunter2",
            "password=hunter2",
            "API_KEY = sk-abcdefghijklmnop123456",
            "client_secret:\tabc",
            "Bearer abcdefghijklmnopqrstuvwxyz123456",
            "password: 'hunter2'",
        ]
        for text in cases:
            with self.subTest(text=text):
                self.assertIn("[REDACTED SECRET]", redact_sensitive_text(text))

    def test_each_keyword_is_redacted_on_one_line(self):
        keywords = (
            "api_key", "apikey", "access_token", "auth_token", "refresh_token",
            "password", "passwd", "pwd", "secret", "client_secret",
        )
        for keyword in keywords:
            with self.subTest(keyword=keyword):
                self.assertIn(
                    "[REDACTED SECRET]",
                    redact_sensitive_text(f"{keyword}: hunter2"),
                )
                self.assertIn(
                    "[REDACTED SECRET]",
                    redact_sensitive_text(f"{keyword} = hunter2"),
                )

    def test_leaves_plain_text_untouched(self):
        text = "The weather in Nairobi is warm today."
        self.assertEqual(redact_sensitive_text(text), text)


class SecretFormatRedactionTests(SimpleTestCase):
    def _credentials(self):
        filler = "Q7mK2pX9vB4nR8tY3wZ6"
        aws = "AK" + "IA" + _repeat("K7M2Q9X4", 16)
        github = "gh" + "p_" + _repeat("aB3dE6hJ9mN2pQ5sT8vW1y", 36)
        github_pat = "github" + "_pat_" + _repeat("Z8y7X6w5V4u3T2s1R0q9", 30)
        slack = "xo" + "xb-" + "1234567890-abcdefghij"
        google = "AI" + "za" + _repeat("A1b2C3d4E5f6G7h8I9j0K1l2M3n4O5p6", 35)
        jwt = (
            "eyJ" + "hbGciOiJIUzI1NiJ9"
            + "."
            + "eyJzdWIiOiIxMjM0NTY3ODkwIn0"
            + "."
            + "Q7mK2pX9vB4nR8tY3wZ6"
        )
        pem_body = _repeat("MIIEowA1b2C3d4E5", 64)
        pem = (
            "-----BEGIN " + "RSA PRIVATE KEY" + "-----\n"
            + pem_body
            + "\n-----END " + "RSA PRIVATE KEY" + "-----"
        )
        url_password = "P4ssw0rd" + "Q7mK2pX9"
        url = "https" + "://" + "user" + ":" + url_password + "@" + "db.example.com"
        return [
            ("anthropic", "sk" + "-ant-api03-" + _repeat(filler, 24), _repeat(filler, 24)),
            ("openai-project", "sk" + "-proj-" + _repeat(filler, 24), _repeat(filler, 24)),
            ("stripe-live", "sk" + "_live_" + _repeat(filler, 24), _repeat(filler, 24)),
            ("aws", aws, aws[len("AKIA"):]),
            ("github", github, github[len("ghp_"):]),
            ("github-fine-grained", github_pat, github_pat[len("github_pat_"):]),
            ("slack", slack, slack[len("xoxb-"):]),
            ("google", google, google[len("AIza"):]),
            ("jwt", jwt, jwt),
            ("pem", pem, pem_body),
            ("url-password", url, url_password),
        ]

    @staticmethod
    def _contexts(credential):
        return {
            "alone": credential,
            "sentence": f"Use the credential {credential} to authenticate.",
            "assignment": f"token={credential}",
            "json": json.dumps({"credential": credential}),
            "start": credential + " followed by text",
            "end": "text before " + credential,
        }

    def test_common_key_formats_are_redacted(self):
        for name, credential, secret in self._credentials():
            for context_name, text in self._contexts(credential).items():
                with self.subTest(format=name, context=context_name):
                    output = redact_sensitive_text(text)
                    self.assertIn("[REDACTED SECRET]", output)
                    for fragment in _substrings(secret, 6):
                        self.assertNotIn(fragment, output)

    def test_url_password_keeps_user_and_host(self):
        _, credential, _ = next(
            item for item in self._credentials() if item[0] == "url-password"
        )
        self.assertEqual(
            redact_sensitive_text(credential),
            "https://user:[REDACTED SECRET]@db.example.com",
        )

    def test_truncated_pem_header_redacts_to_the_end(self):
        body = _repeat("MIIEowA1b2C3d4E5", 64)
        text = "-----BEGIN " + "OPENSSH PRIVATE KEY" + "-----\n" + body
        output = redact_sensitive_text(text)
        self.assertEqual(output, "[REDACTED SECRET]")
        for fragment in _substrings(body, 6):
            self.assertNotIn(fragment, output)

    def test_todays_redactions_are_unchanged(self):
        cases = [
            ("provider-sk", "sk-" + "abcdefghijklmnop123456", "[REDACTED SECRET]"),
            ("provider-pk", "pk-" + "abcdefghijklmnop123456", "[REDACTED SECRET]"),
            ("provider-rk", "rk-" + "abcdefghijklmnop123456", "[REDACTED SECRET]"),
            (
                "bearer",
                "Authorization: Bearer " + "abcdefghijklmnopqrstuvwxyz123456",
                "[REDACTED SECRET]",
            ),
            ("api-key", "api_key=SECRET123", "[REDACTED SECRET]"),
            ("password", "password=hunter2", "[REDACTED SECRET]"),
            ("email", "joe@example.com", "[REDACTED EMAIL]"),
            ("phone", "+254 712 345 678", "[REDACTED PHONE]"),
        ]
        for name, text, token in cases:
            with self.subTest(case=name):
                self.assertIn(token, redact_sensitive_text(text))

    def test_negative_cases_are_not_redacted(self):
        cases = [
            ("sk-plus-word", "sk-documentation"),
            ("uuid", "123e4567-e89b-12d3-a456-426614174000"),
            ("git-sha", "da39a3ee5e6b4b0d3255bfef95601890afd80709"),
            ("akia-word", "the AKIA prefix is not a key"),
            ("url-without-credentials", "https://example.com/path?q=1"),
        ]
        for name, text in cases:
            with self.subTest(case=name):
                self.assertNotIn("[REDACTED SECRET]", redact_sensitive_text(text))

    def test_email_is_not_treated_as_a_secret(self):
        output = redact_sensitive_text("contact joe@example.com")
        self.assertIn("[REDACTED EMAIL]", output)
        self.assertNotIn("[REDACTED SECRET]", output)


_REDACTION_THREAD_TIMEOUT_SECONDS = 20
_REDACTION_ELAPSED_BOUND_SECONDS = 5.0


def _run_redaction_in_thread(operation):
    result = {}

    def target():
        start = time.perf_counter()
        operation()
        result["elapsed"] = time.perf_counter() - start

    thread = threading.Thread(target=target, daemon=True)
    thread.start()
    thread.join(_REDACTION_THREAD_TIMEOUT_SECONDS)
    return thread.is_alive(), result.get("elapsed")


class RedactionCostTests(SimpleTestCase):
    @staticmethod
    def _pathological_text(mode):
        if mode == "alnum":
            return ("A1b2C3d4E5" * 100000)[:1000000]
        if mode == "hyphens":
            return "-" * 1000000
        return "-----BEGIN RSA PRIVATE KEY-----\n" + "A" * 1000000

    def test_pathological_inputs_finish_within_a_fixed_bound(self):
        for mode in ("alnum", "hyphens", "unclosed-pem"):
            text = self._pathological_text(mode)
            for index, pattern in enumerate(_SECRET_PATTERNS):
                with self.subTest(mode=mode, pattern=index):
                    hung, elapsed = _run_redaction_in_thread(
                        lambda pattern=pattern: pattern.sub("[REDACTED SECRET]", text)
                    )
                    self.assertFalse(
                        hung,
                        f"pattern {index} exceeded {_REDACTION_THREAD_TIMEOUT_SECONDS}s",
                    )
                    self.assertLess(elapsed, _REDACTION_ELAPSED_BOUND_SECONDS)


class NormalizeErrorTests(SimpleTestCase):
    def test_truncates_and_flattens(self):
        out = _normalize_error("x" * 2000)
        self.assertTrue(out.startswith("Tool execution failed:"))
        self.assertLessEqual(len(out), 400)

    def test_flattens_newlines(self):
        self.assertEqual(_normalize_error("boom\n\nbang"), "Tool execution failed: boom bang")

    def test_empty_error(self):
        self.assertEqual(_normalize_error(""), "Tool execution failed.")


class PlaceholderUrlTests(SimpleTestCase):
    def test_empty_is_placeholder(self):
        self.assertTrue(_is_placeholder_url(""))
        self.assertTrue(_is_placeholder_url(None))

    def test_amadeus_host_is_placeholder(self):
        self.assertTrue(_is_placeholder_url("https://amadeus.com/foo"))
        self.assertTrue(_is_placeholder_url("https://www.amadeus.com/x"))

    def test_substring_in_path_is_not_placeholder(self):
        self.assertFalse(_is_placeholder_url("https://evil.com/?next=amadeus.com"))

    def test_suffix_spoof_is_not_placeholder(self):
        self.assertFalse(_is_placeholder_url("https://amadeus.com.evil.com/"))

    def test_real_provider_is_not_placeholder(self):
        self.assertFalse(_is_placeholder_url("https://buupass.com/"))


class OpenAIAdapterTests(SimpleTestCase):
    def setUp(self):
        self.client = LLMClient()

    def test_anthropic_to_openai_basic(self):
        out = self.client._anthropic_to_openai_messages(
            "You are Kazi", [{"role": "user", "content": "hi"}],
        )
        self.assertEqual(out[0], {"role": "system", "content": "You are Kazi"})
        self.assertEqual(out[1], {"role": "user", "content": "hi"})

    def test_anthropic_to_openai_tool_use(self):
        msgs = [{
            "role": "assistant",
            "content": [{"type": "tool_use", "id": "c1", "name": "get_weather", "input": {"city": "Nairobi"}}],
        }]
        out = self.client._anthropic_to_openai_messages("", msgs)
        self.assertEqual(out[0]["role"], "assistant")
        call = out[0]["tool_calls"][0]
        self.assertEqual(call["function"]["name"], "get_weather")
        self.assertEqual(call["function"]["arguments"], '{"city": "Nairobi"}')

    def test_anthropic_to_openai_tool_result(self):
        msgs = [{
            "role": "user",
            "content": [{"type": "tool_result", "tool_use_id": "c1", "content": "23C"}],
        }]
        out = self.client._anthropic_to_openai_messages("", msgs)
        self.assertEqual(out[0]["role"], "tool")
        self.assertEqual(out[0]["tool_call_id"], "c1")
        self.assertEqual(out[0]["content"], "23C")

    def test_anthropic_tools_to_openai(self):
        tools = [{"name": "get_weather", "description": "weather", "input_schema": {"type": "object"}}]
        out = self.client._anthropic_tools_to_openai(tools)
        self.assertEqual(out[0]["type"], "function")
        self.assertEqual(out[0]["function"]["name"], "get_weather")
        self.assertEqual(out[0]["function"]["parameters"], {"type": "object"})

    def test_openai_response_to_anthropic_text(self):
        data = {"id": "m1", "choices": [{"message": {"content": "hello"}, "finish_reason": "stop"}]}
        out = self.client._openai_response_to_anthropic(data)
        self.assertEqual(out["stop_reason"], "end_turn")
        self.assertEqual(out["content"][0]["text"], "hello")

    def test_openai_response_to_anthropic_tool_use(self):
        data = {
            "id": "m2",
            "choices": [{
                "message": {
                    "content": None,
                    "tool_calls": [{"id": "c9", "function": {"name": "search", "arguments": '{"q":"x"}'}}],
                },
                "finish_reason": "tool_calls",
            }],
        }
        out = self.client._openai_response_to_anthropic(data)
        self.assertEqual(out["stop_reason"], "tool_use")
        self.assertEqual(out["content"][0]["name"], "search")
        self.assertEqual(out["content"][0]["input"], {"q": "x"})

    def test_openai_response_to_anthropic_length_wins_over_tool_calls(self):
        data = {
            "id": "m3",
            "choices": [{
                "message": {
                    "content": "Part of a sentence",
                    "tool_calls": [
                        {
                            "id": "c1",
                            "function": {
                                "name": "run_command",
                                "arguments": '{"command": "echo a',
                            },
                        },
                        {
                            "id": "c2",
                            "function": {
                                "name": "get_weather",
                                "arguments": '{"city": "Nairobi"}',
                            },
                        },
                    ],
                },
                "finish_reason": "length",
            }],
        }
        out = self.client._openai_response_to_anthropic(data)
        self.assertEqual(out["stop_reason"], "max_tokens")
        self.assertEqual(
            [block["type"] for block in out["content"]],
            ["text", "tool_use", "tool_use"],
        )

    def test_openai_response_to_anthropic_stop_with_tool_calls_stays_tool_use(self):
        data = {
            "id": "m4",
            "choices": [{
                "message": {
                    "content": None,
                    "tool_calls": [{
                        "id": "c3",
                        "function": {"name": "search", "arguments": '{"q":"x"}'},
                    }],
                },
                "finish_reason": "stop",
            }],
        }
        out = self.client._openai_response_to_anthropic(data)
        self.assertEqual(out["stop_reason"], "tool_use")


class _FakeItem:
    def __init__(self, **kwargs):
        self.item_type = kwargs.get("item_type")
        self.title = kwargs.get("title")
        self.metadata = kwargs.get("metadata", {})
        self.location_name = kwargs.get("location_name")
        self.start_datetime = kwargs.get("start_datetime")
        self.end_datetime = kwargs.get("end_datetime")
        self.booking_url = kwargs.get("booking_url")
        self.provider = kwargs.get("provider")


class BookingLinksTests(SimpleTestCase):
    def _item(self, **kwargs):
        return _FakeItem(**kwargs)

    def test_flight_with_iata_codes(self):
        from travel.booking_links import build_booking_link
        item = self._item(
            item_type="flight",
            title="NBO to LHR",
            metadata={"origin_code": "NBO", "destination_code": "LHR"},
            start_datetime=datetime(2026, 8, 20),
            booking_url="https://amadeus.com/x",
            provider="Amadeus",
        )
        url, provider = build_booking_link(item)
        self.assertIn("skyscanner.net", url)
        self.assertIn("nbo", url)
        self.assertEqual(provider, "Skyscanner")

    def test_hotel_uses_booking_com(self):
        from travel.booking_links import build_booking_link
        item = self._item(
            item_type="hotel",
            title="Sarova",
            start_datetime=datetime(2026, 8, 20),
            end_datetime=datetime(2026, 8, 23),
            booking_url="",
            location_name="Nairobi",
        )
        url, _ = build_booking_link(item)
        self.assertIn("booking.com", url)
        self.assertIn("checkin=2026-08-20", url)

    def test_existing_real_link_is_kept(self):
        from travel.booking_links import build_booking_link
        item = self._item(
            item_type="bus",
            title="x",
            booking_url="https://buupass.com/",
            provider="BuuPass",
        )
        url, provider = build_booking_link(item)
        self.assertEqual(url, "https://buupass.com/")
        self.assertEqual(provider, "BuuPass")

    def test_bus_defaults_to_buupass(self):
        from travel.booking_links import build_booking_link
        item = self._item(item_type="bus", title="x", booking_url="")
        url, provider = build_booking_link(item)
        self.assertEqual(url, "https://buupass.com/")
        self.assertEqual(provider, "BuuPass")
