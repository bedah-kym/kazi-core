"""Connector-registry integration guard tests.

Locks the guardrails that keep third-party connectors from breaking Kazi:
catalog-entry validation for entry-point (pip) connectors, action-collision
warnings, async `execute()` enforcement, and `actions`/catalog parity warnings.
"""
from unittest import mock

from django.test import SimpleTestCase

from orchestration import connector_registry
from orchestration.base_connector import BaseConnector


def make_connector(name="test_conn", actions=("ping",), entries=None, async_execute=True):
    base_entries = [
        {
            "action": action,
            "service": name,
            "description": f"Does {action}",
            "params": {},
            "risk_level": "low",
        }
        for action in actions
    ]

    connector = BaseConnector()
    connector.name = name
    connector.version = "1.0.0"
    connector.actions = list(actions)
    connector.required_credentials = []
    connector.get_action_catalog_entries = lambda: (
        entries if entries is not None else base_entries
    )

    if async_execute:
        async def _execute(parameters, context):
            return {"status": "success", "message": "ok"}
    else:
        def _execute(parameters, context):
            return {"status": "success", "message": "ok"}
    connector.execute = _execute
    return connector


class ConnectorGuardTests(SimpleTestCase):
    def test_sync_execute_warns(self):
        connector = make_connector(name="sync_conn", async_execute=False)

        with self.assertLogs("orchestration.connector_registry", level="WARNING") as logs:
            connector_registry._warn_if_not_async(connector)

        self.assertTrue(any("async def" in msg for msg in logs.output))

    def test_async_execute_does_not_warn(self):
        connector = make_connector(name="async_conn", async_execute=True)

        with self.assertNoLogs("orchestration.connector_registry", level="WARNING"):
            connector_registry._warn_if_not_async(connector)

    def test_action_collision_warns(self):
        existing = make_connector(name="builtin")
        connector_map = {"ping": existing}

        with self.assertLogs("orchestration.connector_registry", level="WARNING") as logs:
            connector_registry._warn_on_conflict(connector_map, "ping", "community")

        self.assertTrue(any("overrides" in msg for msg in logs.output))

    def test_same_connector_re_register_does_not_warn(self):
        existing = make_connector(name="builtin")
        connector_map = {"ping": existing}

        with self.assertNoLogs("orchestration.connector_registry", level="WARNING"):
            connector_registry._warn_on_conflict(connector_map, "ping", "builtin")

    def test_actions_entries_mismatch_warns(self):
        connector = make_connector(name="mismatch", actions=("a", "b"))
        entries = [{"action": "a"}, {"action": "c"}]

        with self.assertLogs("orchestration.connector_registry", level="WARNING") as logs:
            connector_registry._warn_on_actions_mismatch(connector, entries)

        output = "\n".join(logs.output)
        self.assertIn("never see them as tools", output)  # 'b' has no entry
        self.assertIn("will not be routable", output)  # 'c' not in actions

    def test_register_actions_and_entries_skips_invalid_entries(self):
        valid = {
            "action": "ping",
            "service": "test_conn",
            "description": "Ping",
            "params": {},
            "risk_level": "low",
        }
        invalid = {"action": "Bad-Name!"}  # invalid snake_case + missing fields
        connector = make_connector(actions=("ping",), entries=[valid, invalid])
        connector_map = {}

        with self.assertLogs("orchestration.connector_registry", level="WARNING") as logs:
            entries = connector_registry._register_actions_and_entries(connector_map, connector)

        self.assertEqual([entry["action"] for entry in entries], ["ping"])
        self.assertEqual(list(connector_map.keys()), ["ping"])
        self.assertTrue(any("violates tool-schema" in msg for msg in logs.output))

    @mock.patch.object(connector_registry, "is_demo_mode", return_value=False)
    @mock.patch.object(connector_registry, "_load_legacy_connectors", return_value={})
    @mock.patch.object(connector_registry, "_scan_examples_directory", return_value=[])
    @mock.patch.object(connector_registry, "_scan_connectors_directory", return_value=[])
    @mock.patch.object(connector_registry, "_scan_entry_points")
    @mock.patch("orchestration.action_catalog.register_actions")
    def test_entry_point_entries_are_validated(
        self,
        mock_register_actions,
        mock_scan_entry_points,
        mock_scan_directory,
        mock_scan_examples,
        mock_legacy,
        mock_demo,
    ):
        valid = {
            "action": "ping",
            "service": "test_conn",
            "description": "Ping",
            "params": {},
            "risk_level": "low",
        }
        invalid = {"action": "Bad-Name!"}
        connector = make_connector(actions=("ping",), entries=[valid, invalid])
        mock_scan_entry_points.return_value = [connector]

        connector_registry.reset_registry()
        try:
            with self.assertLogs("orchestration.connector_registry", level="WARNING") as logs:
                connector_registry.discover_connectors()

            registered = connector_registry.get_registered_catalog_entries()
            self.assertEqual([entry["action"] for entry in registered], ["ping"])
            self.assertTrue(any("violates tool-schema" in msg for msg in logs.output))
        finally:
            connector_registry.reset_registry()


class EntryPointShadowingTests(SimpleTestCase):
    """CR-3 companion guard: a pip-installed (entry-point) connector may add
    NEW actions but must never replace a built-in's implementation of an
    existing action."""

    def _discover_with_entry_point(self, entry_point_connector):
        legacy_owner = make_connector(name="builtin")
        with mock.patch.object(connector_registry, "is_demo_mode", return_value=False), \
                mock.patch.object(
                    connector_registry, "_load_legacy_connectors",
                    return_value={"ping": legacy_owner},
        ), \
                mock.patch.object(connector_registry, "_scan_examples_directory", return_value=[]), \
                mock.patch.object(connector_registry, "_scan_connectors_directory", return_value=[]), \
                mock.patch.object(
                    connector_registry, "_scan_entry_points",
                    return_value=[entry_point_connector],
        ), \
                mock.patch("orchestration.action_catalog.register_actions"):
            connector_registry.reset_registry()
            try:
                return connector_registry.discover_connectors()
            finally:
                pass

    def test_entry_point_cannot_shadow_builtin_action(self):
        community = make_connector(name="community", actions=("ping", "novel"))
        connector_map = self._discover_with_entry_point(community)

        self.assertIs(getattr(connector_map["ping"], "name", None), "builtin")
        self.assertIs(getattr(connector_map["novel"], "name", None), "community")

    def test_shadowing_refusal_logs_warning(self):
        community = make_connector(name="community", actions=("ping",))

        with mock.patch.object(connector_registry, "is_demo_mode", return_value=False), \
                mock.patch.object(
                    connector_registry, "_load_legacy_connectors",
                    return_value={"ping": make_connector(name="builtin")},
        ), \
                mock.patch.object(connector_registry, "_scan_examples_directory", return_value=[]), \
                mock.patch.object(connector_registry, "_scan_connectors_directory", return_value=[]), \
                mock.patch.object(
                    connector_registry, "_scan_entry_points", return_value=[community],
        ), \
                mock.patch("orchestration.action_catalog.register_actions"):
            connector_registry.reset_registry()
            try:
                with self.assertLogs("orchestration.connector_registry", level="WARNING") as logs:
                    connector_registry.discover_connectors()
            finally:
                connector_registry.reset_registry()

        self.assertTrue(any("shadow action" in msg for msg in logs.output))


class ContractViolationRecordingTests(SimpleTestCase):
    """Invalid catalog entries must be recorded, not just logged, so the
    CR-3 sweep can observe them."""

    @mock.patch.object(connector_registry, "is_demo_mode", return_value=False)
    @mock.patch.object(connector_registry, "_load_legacy_connectors", return_value={})
    @mock.patch.object(connector_registry, "_scan_examples_directory", return_value=[])
    @mock.patch.object(connector_registry, "_scan_entry_points", return_value=[])
    @mock.patch.object(connector_registry, "_scan_connectors_directory")
    @mock.patch("orchestration.action_catalog.register_actions")
    def test_invalid_entry_is_recorded(
        self, mock_register_actions, mock_scan_directory,
        mock_scan_entry_points, mock_scan_examples, mock_legacy, mock_demo,
    ):
        invalid = {"action": "Bad-Name!"}
        connector = make_connector(actions=("Bad-Name!",), entries=[invalid])
        mock_scan_directory.return_value = [connector]

        connector_registry.reset_registry()
        try:
            connector_registry.discover_connectors()

            violations = connector_registry.get_contract_violations()
            self.assertEqual(len(violations), 1)
            self.assertEqual(violations[0]["connector"], "test_conn")
            self.assertEqual(violations[0]["action"], "Bad-Name!")
            self.assertEqual([], [
                e["action"] for e in connector_registry.get_registered_catalog_entries()
            ])
        finally:
            connector_registry.reset_registry()


class RegisteredConnectorsContractSweepTests(SimpleTestCase):
    """CR-3: the whole live registry must satisfy the v1.0 tool-schema
    contract. Boot-time validation drops invalid entries with only a
    warning; this sweep fails CI if ANY real connector ships one."""

    def test_live_registry_has_no_contract_violations(self):
        from orchestration.contracts import validate_catalog_entry

        connector_registry.reset_registry()
        try:
            connector_registry.discover_connectors()

            violations = connector_registry.get_contract_violations()
            self.assertEqual(violations, [])

            bad = []
            for entry in connector_registry.get_registered_catalog_entries():
                ok, errors = validate_catalog_entry(entry)
                if not ok:
                    bad.append({"action": entry.get("action"), "errors": errors})
            self.assertEqual(bad, [])
        finally:
            connector_registry.reset_registry()
