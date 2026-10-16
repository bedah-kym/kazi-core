"""T-F2: a turn offers only the tools that can work here.

The tool list is cut by configuration (connector loaded), capability flag and
persona scope — never by guessing what a message needs. The prompt describes
only the tools that are present, and the shell section rides with run_command.
"""
from __future__ import annotations

import inspect
import os
from unittest.mock import patch

from django.test import SimpleTestCase

from orchestration.connector_registry import reset_registry


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
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("TELEGRAM_BOT_TOKEN", None)
            os.environ.pop("SHELL_EXEC_TOKEN", None)
            reset_registry()
            self.addCleanup(reset_registry)
            names = _names(user_capabilities=ALL_GATES_ON)

        for action in TELEGRAM_ACTIONS:
            self.assertNotIn(action, names)
        self.assertNotIn("run_command", names)

    def test_a_payment_needing_the_missing_library_is_absent(self):
        reset_registry()
        self.addCleanup(reset_registry)
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
