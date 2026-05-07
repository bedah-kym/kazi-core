"""Tests for the v0.4 stable runtime contracts (M2-2).

The shape under test is documented in docs/contracts/. If you change
either the validator or the doc, change them in the same PR.
"""
from __future__ import annotations

from django.test import SimpleTestCase

from orchestration.contracts import (
    APPROVAL_CONTRACT_VERSION,
    CONNECTOR_EXECUTION_CONTRACT_VERSION,
    EXECUTION_DETAIL_CONTRACT_VERSION,
    REPLAY_SAFETY_CONTRACT_VERSION,
    TOOL_SCHEMA_CONTRACT_VERSION,
    validate_catalog_entry,
)


class ContractVersionTests(SimpleTestCase):
    """Pin the v1.0 baseline. Any bump must update docs/contracts/ in the same PR."""

    def test_versions_are_pinned(self):
        self.assertEqual(CONNECTOR_EXECUTION_CONTRACT_VERSION, "1.0")
        self.assertEqual(TOOL_SCHEMA_CONTRACT_VERSION, "1.0")
        self.assertEqual(APPROVAL_CONTRACT_VERSION, "1.0")
        self.assertEqual(EXECUTION_DETAIL_CONTRACT_VERSION, "1.0")
        self.assertEqual(REPLAY_SAFETY_CONTRACT_VERSION, "1.0")


class ValidateCatalogEntryTests(SimpleTestCase):
    def _good_entry(self) -> dict:
        return {
            "action": "get_weather",
            "service": "weather",
            "description": "Get the current weather for a city.",
            "params": {
                "city": {
                    "type": "string",
                    "required": True,
                    "description": "City name.",
                },
            },
            "risk_level": "low",
        }

    def test_minimal_valid_entry_passes(self):
        ok, errors = validate_catalog_entry(self._good_entry())
        self.assertTrue(ok, errors)
        self.assertEqual(errors, [])

    def test_optional_fields_pass(self):
        entry = self._good_entry()
        entry.update({
            "aliases": ["weather"],
            "confirmation_policy": "never",
            "capability_gate": "allow_weather",
            "replay_safe": True,
        })
        ok, errors = validate_catalog_entry(entry)
        self.assertTrue(ok, errors)

    def test_empty_params_dict_is_valid(self):
        entry = self._good_entry()
        entry["params"] = {}
        ok, errors = validate_catalog_entry(entry)
        self.assertTrue(ok, errors)

    def test_non_dict_entry_fails(self):
        ok, errors = validate_catalog_entry("not a dict")
        self.assertFalse(ok)
        self.assertTrue(any("not a dict" in e for e in errors))

    def test_missing_action_fails(self):
        entry = self._good_entry()
        del entry["action"]
        ok, errors = validate_catalog_entry(entry)
        self.assertFalse(ok)
        self.assertTrue(any("'action'" in e for e in errors))

    def test_non_snake_case_action_fails(self):
        entry = self._good_entry()
        entry["action"] = "GetWeather"
        ok, errors = validate_catalog_entry(entry)
        self.assertFalse(ok)
        self.assertTrue(any("snake_case" in e for e in errors))

    def test_missing_param_description_fails(self):
        entry = self._good_entry()
        entry["params"]["city"] = {"type": "string", "required": True}
        ok, errors = validate_catalog_entry(entry)
        self.assertFalse(ok)
        self.assertTrue(any("description" in e for e in errors))

    def test_invalid_risk_level_fails(self):
        entry = self._good_entry()
        entry["risk_level"] = "extreme"
        ok, errors = validate_catalog_entry(entry)
        self.assertFalse(ok)
        self.assertTrue(any("risk_level" in e for e in errors))

    def test_invalid_param_type_fails(self):
        entry = self._good_entry()
        entry["params"]["city"]["type"] = "datetime"
        ok, errors = validate_catalog_entry(entry)
        self.assertFalse(ok)
        self.assertTrue(any("type" in e for e in errors))

    def test_invalid_confirmation_policy_fails(self):
        entry = self._good_entry()
        entry["confirmation_policy"] = "maybe"
        ok, errors = validate_catalog_entry(entry)
        self.assertFalse(ok)
        self.assertTrue(any("confirmation_policy" in e for e in errors))

    def test_replay_safe_must_be_bool(self):
        entry = self._good_entry()
        entry["replay_safe"] = "yes"
        ok, errors = validate_catalog_entry(entry)
        self.assertFalse(ok)
        self.assertTrue(any("replay_safe" in e for e in errors))

    def test_aliases_must_be_string_list(self):
        entry = self._good_entry()
        entry["aliases"] = [42]
        ok, errors = validate_catalog_entry(entry)
        self.assertFalse(ok)
        self.assertTrue(any("aliases" in e for e in errors))

    def test_multiple_errors_all_collected(self):
        entry = {
            "action": "BadName",
            "service": "",
            "params": "not a dict",
            "risk_level": "extreme",
        }
        ok, errors = validate_catalog_entry(entry)
        self.assertFalse(ok)
        # All four fields should produce errors:
        self.assertGreaterEqual(len(errors), 4)
