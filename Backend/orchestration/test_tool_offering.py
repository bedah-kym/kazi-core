"""T-F2: a turn offers only the tools that can work here.

The tool list is cut by configuration (connector loaded), capability flag and
persona scope — never by guessing what a message needs. The prompt describes
only the tools that are present, and the shell section rides with run_command.
"""
from __future__ import annotations

import inspect
import os
from contextlib import contextmanager
from unittest.mock import patch

from django.conf import settings
from django.test import SimpleTestCase

from orchestration.connector_registry import reset_registry


@contextmanager
def _credentials_absent(env_names=(), setting_names=()):
    """Make the named credentials absent, however the connector reads them.

    Environment variables are popped and settings attributes blanked so a
    developer's ``.env`` cannot make the availability tests non-deterministic.
    """
    with patch.dict(os.environ, {}, clear=False):
        for name in env_names:
            os.environ.pop(name, None)
        patchers = [patch.object(settings, name, "") for name in setting_names]
        for patcher in patchers:
            patcher.start()
        reset_registry()
        try:
            yield
        finally:
            reset_registry()
            for patcher in reversed(patchers):
                patcher.stop()


ALL_GATES_ON = {
    "allow_email": True, "allow_payments": True, "allow_whatsapp": True,
    "allow_calendar": True, "allow_travel": True, "allow_web_search": True,
    "allow_reminders": True, "allow_telegram": True, "allow_voice": True,
}

TELEGRAM_ACTIONS = [
    "send_telegram_message", "send_telegram_media", "send_telegram_keyboard",
    "edit_telegram_message", "delete_telegram_message", "telegram_health",
]


def _names(**kwargs):
    from orchestration.tool_schemas import get_tool_definitions

    return {tool["name"] for tool in get_tool_definitions(**kwargs)}


class ConfiguredOnlyTests(SimpleTestCase):
    """A tool whose connector did not load is not offered."""

    def test_action_without_a_loaded_connector_is_absent(self):
        names = _names(
            user_capabilities=ALL_GATES_ON,
            available_actions={"send_email", "get_weather"},
        )
        self.assertIn("send_email", names)
        self.assertIn("get_weather", names)
        self.assertNotIn("search_flights", names)
        self.assertNotIn("set_reminder", names)
        self.assertNotIn("run_command", names)

    def test_all_telegram_actions_are_absent_without_a_token(self):
        with patch.dict(os.environ, {}, clear=False), \
                patch.object(settings, "TELEGRAM_BOT_TOKEN", ""), \
                patch.object(settings, "SHELL_EXEC_TOKEN", ""):
            os.environ.pop("TELEGRAM_BOT_TOKEN", None)
            os.environ.pop("SHELL_EXEC_TOKEN", None)
            reset_registry()
            self.addCleanup(reset_registry)
            names = _names(user_capabilities=ALL_GATES_ON)

        for action in TELEGRAM_ACTIONS:
            self.assertNotIn(action, names)
        self.assertNotIn("run_command", names)

    def test_unavailable_payment_connector_is_absent(self):
        # Force the legacy IntaSend connector to report unavailable, whatever
        # the SDK/key state on the machine, so the test is deterministic.
        from orchestration.connectors.intersend_connector import IntersendPayConnector

        reset_registry()
        self.addCleanup(reset_registry)
        with patch.object(
            IntersendPayConnector, "validate_config",
            return_value=(False, "IntaSend is not available."),
        ):
            from orchestration.connector_registry import (
                discover_connectors,
                get_available_actions,
                get_skipped_connectors,
            )

            discover_connectors()
            skipped = {item["name"]: item for item in get_skipped_connectors()}
            self.assertIn("IntersendPayConnector", skipped)
            self.assertIn("create_payment_link", skipped["IntersendPayConnector"]["actions"])
            self.assertNotIn("create_payment_link", get_available_actions())

    def test_remaining_tools_are_present(self):
        names = _names(
            user_capabilities=ALL_GATES_ON,
            available_actions={"send_email", "get_weather", "set_reminder", "run_command"},
        )
        self.assertTrue(
            {"send_email", "get_weather", "set_reminder", "run_command"} <= names
        )


class CapabilityGateTests(SimpleTestCase):
    """Gates decide; sensitive gates fail closed."""

    def test_gated_tool_with_flag_off_is_absent(self):
        names = _names(
            user_capabilities={**ALL_GATES_ON, "allow_voice": False},
            available_actions={"generate_speech", "get_weather"},
        )
        self.assertNotIn("generate_speech", names)
        self.assertIn("get_weather", names)

    def test_telegram_gate_removes_an_off_group(self):
        names = _names(
            user_capabilities={**ALL_GATES_ON, "allow_telegram": False},
            available_actions=set(TELEGRAM_ACTIONS) | {"get_weather"},
        )
        self.assertNotIn("send_telegram_message", names)
        self.assertIn("get_weather", names)

    def test_sensitive_gate_with_no_flag_is_absent(self):
        names = _names(
            user_capabilities={},
            available_actions={"send_email", "create_payment_link", "get_weather"},
        )
        self.assertNotIn("send_email", names)
        self.assertNotIn("create_payment_link", names)
        self.assertIn("get_weather", names)

    def test_ungated_group_defaults_to_kept(self):
        names = _names(
            user_capabilities={},
            available_actions={"generate_speech", TELEGRAM_ACTIONS[0]},
        )
        self.assertIn("generate_speech", names)
        self.assertIn("send_telegram_message", names)


class PersonaScopeTests(SimpleTestCase):
    """A persona scope is a hard ceiling at offer time."""

    def test_tool_outside_persona_scope_is_absent(self):
        names = _names(
            user_capabilities=ALL_GATES_ON,
            available_actions={"send_email", "get_weather"},
            tool_scope=["get_weather"],
        )
        self.assertEqual(names, {"get_weather"})

    def test_scope_also_bounds_internal_tools(self):
        names = _names(
            user_capabilities=ALL_GATES_ON,
            available_actions={"get_weather"},
            tool_scope=["get_weather"],
        )
        self.assertNotIn("create_note", names)
        self.assertNotIn("lookup_contact", names)


class NoGuessingTests(SimpleTestCase):
    """Nothing selects tools by the words of a message."""

    def test_tool_builder_takes_no_message(self):
        from orchestration.tool_schemas import get_tool_definitions

        params = set(inspect.signature(get_tool_definitions).parameters)
        self.assertFalse(params & {"message", "query", "user_message", "text"})

    def test_same_inputs_give_the_same_tools(self):
        first = _names(user_capabilities=ALL_GATES_ON, available_actions={"send_email"})
        second = _names(user_capabilities=ALL_GATES_ON, available_actions={"send_email"})
        self.assertEqual(first, second)


class RunCommandEntryTests(SimpleTestCase):
    """One true run_command entry, true for an open (unsandboxed) room."""

    def test_run_command_offered_once_with_a_network_parameter(self):
        from orchestration.tool_schemas import get_tool_definitions

        tools = [
            tool for tool in get_tool_definitions(
                user_capabilities=ALL_GATES_ON, available_actions={"run_command"},
            )
            if tool["name"] == "run_command"
        ]
        self.assertEqual(len(tools), 1)
        self.assertIn("network", tools[0]["input_schema"]["properties"])
        self.assertNotIn("sandboxed container", tools[0]["description"])
        self.assertNotIn("network off by default", tools[0]["description"])


class PromptFollowsToolsTests(SimpleTestCase):
    """Guidance about a tool group is sent only when the group is offered."""

    def _prompt(self, tool_names):
        from orchestration.agent_prompts import build_system_prompt

        return build_system_prompt(
            preferences={}, context_prompt="ROOM", tool_names=tool_names,
        )

    def test_absent_groups_have_no_section(self):
        prompt = self._prompt(["get_weather"])
        self.assertNotIn("## Modality", prompt)
        self.assertNotIn("## Reminders & notifications", prompt)
        self.assertNotIn("## Memory management", prompt)
        self.assertNotIn("## Contact management", prompt)

    def test_present_groups_keep_their_section(self):
        prompt = self._prompt(
            ["generate_speech", "set_reminder", "create_note", "lookup_contact"],
        )
        self.assertIn("## Modality", prompt)
        self.assertIn("## Reminders & notifications", prompt)
        self.assertIn("## Memory management", prompt)
        self.assertIn("## Contact management", prompt)

    def test_environment_block_is_its_own_section_not_conversation_context(self):
        from orchestration.agent_prompts import build_environment_block, build_system_prompt

        block = build_environment_block({"profile": "open", "platform": "Linux", "shell": "bash"})
        prompt = build_system_prompt(
            preferences={}, context_prompt="ROOM", tool_names=["run_command"],
            environment_block=block,
        )
        self.assertIn("## Where you are running", prompt)
        self.assertLess(
            prompt.index("## Where you are running"),
            prompt.index("## Conversation context"),
        )

    def test_environment_block_is_dropped_without_run_command(self):
        from orchestration.agent_prompts import build_environment_block, build_system_prompt

        block = build_environment_block({"profile": "open", "platform": "Linux", "shell": "bash"})
        prompt = build_system_prompt(
            preferences={}, context_prompt="ROOM", tool_names=["get_weather"],
            environment_block=block,
        )
        self.assertNotIn("Where you are running", prompt)


class ConnectorAvailabilityTests(SimpleTestCase):
    """Deliverable G1: a connector with no key/library is not offered.

    Each of these fails on the pre-follow-up branch, where these connectors
    inherited ``BaseConnector.validate_config`` with no required credentials and
    were therefore always counted as configured.
    """

    def test_weather_absent_without_a_key(self):
        with _credentials_absent(
            env_names=["OPENWEATHER_API_KEY"],
            setting_names=["OPENWEATHER_API_KEY"],
        ):
            names = _names(user_capabilities=ALL_GATES_ON)
        self.assertNotIn("get_weather", names)

    def test_gif_absent_without_a_key(self):
        with _credentials_absent(
            env_names=["GIPHY_API_KEY"], setting_names=["GIPHY_API_KEY"],
        ):
            names = _names(user_capabilities=ALL_GATES_ON)
        self.assertNotIn("search_gif", names)

    def test_currency_absent_without_a_key(self):
        with _credentials_absent(
            env_names=["EXCHANGE_RATE_API_KEY"],
            setting_names=["EXCHANGE_RATE_API_KEY"],
        ):
            names = _names(user_capabilities=ALL_GATES_ON)
        self.assertNotIn("convert_currency", names)

    def test_whatsapp_absent_without_credentials_outside_demo(self):
        with _credentials_absent(
            env_names=["TWILIO_ACCOUNT_SID", "TWILIO_AUTH_TOKEN", "KAZI_DEMO_MODE"],
        ):
            names = _names(user_capabilities=ALL_GATES_ON)
        self.assertNotIn("send_message", names)
        self.assertNotIn("send_whatsapp", names)

    def test_email_absent_without_credentials_outside_demo(self):
        with _credentials_absent(
            env_names=[
                "MAILGUN_API_KEY", "MAILGUN_DOMAIN", "MAILGUN_USE_SANDBOX",
                "KAZI_DEMO_MODE", "GMAIL_OAUTH_CLIENT_ID", "GMAIL_OAUTH_CLIENT_SECRET",
                "GOOGLE_CLIENT_ID", "GOOGLE_CLIENT_SECRET",
            ],
            setting_names=["GMAIL_OAUTH_CLIENT_ID", "GMAIL_OAUTH_CLIENT_SECRET"],
        ):
            names = _names(user_capabilities=ALL_GATES_ON)
        self.assertNotIn("send_email", names)

    def test_demo_mode_still_offers_email_without_credentials(self):
        # The canonical demo sends through Mailgun's mock; it must stay offered.
        with patch.dict(os.environ, {"KAZI_DEMO_MODE": "true"}, clear=False):
            for name in ("MAILGUN_API_KEY", "MAILGUN_DOMAIN"):
                os.environ.pop(name, None)
            with patch.object(settings, "GMAIL_OAUTH_CLIENT_ID", ""), \
                    patch.object(settings, "GMAIL_OAUTH_CLIENT_SECRET", ""):
                reset_registry()
                self.addCleanup(reset_registry)
                names = _names(user_capabilities=ALL_GATES_ON)
        self.assertIn("send_email", names)


class ShellNetworkPromptTests(SimpleTestCase):
    """Deliverable G2: the prompt gives an accurate picture of the network."""

    def test_open_shell_section_states_full_network(self):
        from orchestration.agent_prompts import build_environment_block

        block = build_environment_block({"profile": "open", "platform": "Linux", "shell": "bash"})
        self.assertIn("full network access", block)

    def test_standard_shell_section_states_network_needs_approval(self):
        from orchestration.agent_prompts import build_environment_block

        block = build_environment_block({"profile": "standard", "platform": "Linux", "shell": "sh"})
        self.assertIn("no network unless a command asks for it and the harness approves it", block)

    def test_locked_shell_section_states_no_network(self):
        from orchestration.agent_prompts import build_environment_block

        block = build_environment_block({"profile": "locked", "platform": "Linux", "shell": "sh"})
        self.assertIn("This room's shell has no network.", block)

    def test_no_search_note_does_not_imply_the_network_is_down(self):
        from orchestration.agent_prompts import NO_SEARCH_TOOL_NOTE

        lowered = NO_SEARCH_TOOL_NOTE.lower()
        self.assertIn("no web-search tool", lowered)
        for misleading in ("network", "environment", "unreachable", "no fetch", "no curl"):
            self.assertNotIn(misleading, lowered)
