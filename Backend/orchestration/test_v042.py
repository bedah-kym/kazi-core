"""Unit tests for v0.4.2 pure logic — fail-plausible guard, DeepSeek adapter,
URL placeholder detection, and booking links."""
from datetime import datetime

from django.test import SimpleTestCase

from orchestration.connectors.itinerary_connector import _is_placeholder_url
from orchestration.llm_client import LLMClient
from orchestration.security_policy import redact_sensitive_text
from orchestration.tool_executor import _normalize_error


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

    def test_leaves_plain_text_untouched(self):
        text = "The weather in Nairobi is warm today."
        self.assertEqual(redact_sensitive_text(text), text)


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
