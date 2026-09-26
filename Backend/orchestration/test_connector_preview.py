"""
Unit tests for the connector preview() dry run (issue #168).

Covers: default preview() on BaseConnector, preview_tool() fail-closed
behavior, the sample Mailgun preview, and parameter sanitization before
preview.
"""
import asyncio
from unittest.mock import patch

from django.test import SimpleTestCase

from orchestration.base_connector import BaseConnector


class _PreviewConnector(BaseConnector):
    name = "preview_demo"

    def __init__(self, preview_result=None, raise_on_preview=False):
        self.preview_result = preview_result
        self.raise_on_preview = raise_on_preview
        self.received_parameters = None

    async def execute(self, parameters, context):
        return {"status": "success"}

    async def preview(self, parameters, context):
        self.received_parameters = parameters
        if self.raise_on_preview:
            raise RuntimeError("preview exploded")
        return self.preview_result


class BaseConnectorPreviewTests(SimpleTestCase):
    def test_default_preview_returns_none(self):
        result = asyncio.run(BaseConnector().preview({}, {}))
        self.assertIsNone(result)


class PreviewToolTests(SimpleTestCase):
    def _call_preview(self, connector, tool_input=None):
        from orchestration.tool_executor import preview_tool

        with patch(
            "orchestration.tool_executor._get_connector_map",
            return_value={"send_email": connector},
        ):
            return asyncio.run(
                preview_tool(
                    "send_email",
                    tool_input or {"to": "ops@example.com", "subject": "Hi"},
                    {"user_id": 1, "room_id": 1},
                )
            )

    def test_returns_effects_list(self):
        connector = _PreviewConnector(
            preview_result={"effects": ["Send an email", "Subject: Hi"]}
        )
        effects = self._call_preview(connector)
        self.assertEqual(effects, ["Send an email", "Subject: Hi"])

    def test_connector_without_preview_returns_none(self):
        effects = self._call_preview(BaseConnector())
        self.assertIsNone(effects)

    def test_preview_raising_is_fail_closed(self):
        effects = self._call_preview(_PreviewConnector(raise_on_preview=True))
        self.assertIsNone(effects)

    def test_wrong_shape_returns_none(self):
        effects = self._call_preview(
            _PreviewConnector(preview_result={"nope": "not a list"})
        )
        self.assertIsNone(effects)

    def test_non_effect_strings_are_coerced(self):
        connector = _PreviewConnector(preview_result={"effects": [1, 2.5]})
        effects = self._call_preview(connector)
        self.assertEqual(effects, ["1", "2.5"])

    def test_unknown_tool_returns_none(self):
        from orchestration.tool_executor import preview_tool

        effects = asyncio.run(
            preview_tool("nonexistent_tool", {}, {"user_id": 1, "room_id": 1})
        )
        self.assertIsNone(effects)

    def test_preview_timeout_is_fail_closed(self):
        class _SlowConnector(BaseConnector):
            async def execute(self, parameters, context):
                return {"status": "success"}

            async def preview(self, parameters, context):
                await asyncio.sleep(5)
                return {"effects": ["never returned"]}

        from orchestration.tool_executor import preview_tool

        with patch(
            "orchestration.tool_executor.PREVIEW_TIMEOUT_SECONDS", 0.05
        ), patch(
            "orchestration.tool_executor._get_connector_map",
            return_value={"send_email": _SlowConnector()},
        ):
            effects = asyncio.run(
                preview_tool(
                    "send_email",
                    {"to": "ops@example.com", "subject": "Hi"},
                    {"user_id": 1, "room_id": 1},
                )
            )
        self.assertIsNone(effects)

    def test_parameters_are_sanitized_with_action_key(self):
        connector = _PreviewConnector(preview_result={"effects": ["x"]})
        self._call_preview(connector)
        self.assertEqual(connector.received_parameters["action"], "send_email")
        self.assertEqual(
            connector.received_parameters["to"], "ops@example.com"
        )


class MailgunPreviewTests(SimpleTestCase):
    def test_send_email_preview_describes_effects(self):
        from orchestration.connectors.mailgun_connector import MailgunConnector

        result = asyncio.run(
            MailgunConnector().preview(
                {
                    "action": "send_email",
                    "to": "ops@example.com",
                    "subject": "Nightly report",
                    "text": "All systems nominal.",
                },
                {},
            )
        )
        self.assertIn("effects", result)
        self.assertTrue(
            any("ops@example.com" in line for line in result["effects"])
        )
        self.assertTrue(
            any("Nightly report" in line for line in result["effects"])
        )

    def test_unknown_action_preview_returns_none(self):
        from orchestration.connectors.mailgun_connector import MailgunConnector

        result = asyncio.run(
            MailgunConnector().preview({"action": "list_domains"}, {})
        )
        self.assertIsNone(result)

    def test_invalid_email_params_preview_returns_none(self):
        from orchestration.connectors.mailgun_connector import MailgunConnector

        connector = MailgunConnector()
        missing_recipient = asyncio.run(
            connector.preview(
                {
                    "action": "send_email",
                    "subject": "Hi",
                    "text": "Body",
                },
                {},
            )
        )
        self.assertIsNone(missing_recipient)
        missing_body = asyncio.run(
            connector.preview(
                {
                    "action": "send_email",
                    "to": "ops@example.com",
                    "subject": "Hi",
                },
                {},
            )
        )
        self.assertIsNone(missing_body)
