"""Tests for the golden eval loop runner (T-E4)."""
import json
import os
import tempfile
from io import StringIO

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import SimpleTestCase

from orchestration.eval.loop_runner import evaluate_loop_scenario


def _auto_tool_scenario(**overrides):
    scenario = {
        "id": "sample_loop",
        "pack": "orchestration",
        "message": "What is the weather in Nairobi?",
        "loop": {
            "llm_script": [
                {
                    "tool_calls": [
                        {"name": "get_weather", "input": {"city": "Nairobi"}},
                    ],
                },
                {"text": "It is sunny."},
            ],
        },
        "expected_executed": ["get_weather"],
        "expected_paused_on": None,
        "expected_refused": [],
    }
    scenario.update(overrides)
    return scenario


class LoopRunnerTests(SimpleTestCase):
    def test_correct_expectation_passes(self):
        passed, details, result = evaluate_loop_scenario(_auto_tool_scenario())
        self.assertTrue(passed, msg=details)
        self.assertEqual(details, [])
        self.assertEqual(result.executed, ["get_weather"])
        self.assertIsNone(result.paused_on)

    def test_wrong_expectation_fails_with_readable_reason(self):
        scenario = _auto_tool_scenario(expected_executed=["send_email"])
        passed, details, _result = evaluate_loop_scenario(scenario)
        self.assertFalse(passed)
        self.assertEqual(len(details), 1)
        self.assertIn("executed ['get_weather'] != ['send_email']", details[0])

    def test_pause_is_detected(self):
        scenario = _auto_tool_scenario(
            id="email_pause_loop",
            loop={
                "llm_script": [
                    {
                        "tool_calls": [
                            {
                                "name": "send_email",
                                "input": {"to": "ops@example.com", "subject": "x", "text": "y"},
                            },
                        ],
                    },
                ],
            },
            expected_executed=[],
            expected_paused_on="send_email",
        )
        passed, details, result = evaluate_loop_scenario(scenario)
        self.assertTrue(passed, msg=details)
        self.assertEqual(result.paused_on, "send_email")

    def test_refused_tool_is_detected(self):
        scenario = _auto_tool_scenario(
            id="persona_refusal_loop",
            loop={
                "persona_scope": ["get_weather"],
                "llm_script": [
                    {
                        "tool_calls": [
                            {
                                "name": "send_email",
                                "input": {"to": "ops@example.com", "subject": "x", "text": "y"},
                            },
                        ],
                    },
                    {"text": "Outside my scope."},
                ],
            },
            expected_executed=[],
            expected_paused_on=None,
            expected_refused=["send_email"],
        )
        passed, details, result = evaluate_loop_scenario(scenario)
        self.assertTrue(passed, msg=details)
        self.assertEqual(result.refused, ["send_email"])

    def test_wrong_pause_reports_reason(self):
        scenario = _auto_tool_scenario(expected_paused_on="send_email")
        passed, details, _result = evaluate_loop_scenario(scenario)
        self.assertFalse(passed)
        self.assertIn("paused_on None != 'send_email'", details[0])

    def test_command_exits_non_zero_when_a_loop_scenario_fails(self):
        scenario = _auto_tool_scenario(expected_executed=["send_email"])
        with tempfile.NamedTemporaryFile(
            "w", suffix=".json", delete=False, encoding="utf-8",
        ) as handle:
            json.dump([scenario], handle)
            path = handle.name
        try:
            out = StringIO()
            with self.assertRaises(CommandError):
                call_command("run_golden_eval", path=path, stdout=out)
            self.assertIn("Failed: 1", out.getvalue())
            self.assertIn("executed ['get_weather'] != ['send_email']", out.getvalue())
        finally:
            os.unlink(path)

    def test_a_loop_scenario_still_runs_its_other_checks(self):
        # The loop part passes; the message is not an injection, so this must fail.
        scenario = _auto_tool_scenario(expected_injection=True)
        with tempfile.NamedTemporaryFile(
            "w", suffix=".json", delete=False, encoding="utf-8",
        ) as handle:
            json.dump([scenario], handle)
            path = handle.name
        try:
            out = StringIO()
            with self.assertRaises(CommandError):
                call_command("run_golden_eval", path=path, stdout=out)
            self.assertIn("injection False != True", out.getvalue())
        finally:
            os.unlink(path)

    def test_command_exits_zero_when_all_scenarios_pass(self):
        with tempfile.NamedTemporaryFile(
            "w", suffix=".json", delete=False, encoding="utf-8",
        ) as handle:
            json.dump([_auto_tool_scenario()], handle)
            path = handle.name
        try:
            out = StringIO()
            call_command("run_golden_eval", path=path, stdout=out)
            self.assertIn("Failed: 0", out.getvalue())
        finally:
            os.unlink(path)
