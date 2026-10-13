import asyncio
import os
from unittest.mock import patch

from django.test import SimpleTestCase


def run_async(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


class CapabilityCycleUngatingTests(SimpleTestCase):
    def test_find_dependency_cycle_ignores_malformed_dependencies(self):
        from workflows.capabilities import find_dependency_cycle

        steps = [
            {"id": "a", "depends_on": "b"},   # string, not a list
            None,                              # non-dict step
            {"id": "b", "depends_on": None},   # None, not a list
            {"id": "c"},                        # no depends_on key
        ]
        self.assertEqual(find_dependency_cycle(steps), [])


class ConnectorRegistryUngatingTests(SimpleTestCase):
    @patch.dict(os.environ, {"KAZI_DEMO_MODE": "false"}, clear=False)
    def test_echo_connector_is_disabled_by_default(self):
        # v0.4 M5-2: example connectors are off unless KAZI_DEMO_MODE is set.
        from orchestration.connector_registry import discover_connectors, reset_registry

        reset_registry()
        connectors = discover_connectors()
        self.assertNotIn("echo", connectors)

    @patch.dict(os.environ, {"KAZI_DEMO_MODE": "true"}, clear=False)
    def test_echo_connector_loads_in_demo_mode(self):
        # v0.4 M5-3: examples/connectors/echo/ is auto-discovered when demo mode is on.
        from orchestration.connector_registry import discover_connectors, reset_registry

        reset_registry()
        connectors = discover_connectors()
        self.assertIn("echo", connectors)
        self.assertEqual(connectors["echo"].name, "echo")

    @patch.dict(os.environ, {"KAZI_DEMO_MODE": "false"}, clear=False)
    def test_router_integrity_still_passes_after_dynamic_discovery(self):
        from orchestration.connector_registry import discover_connectors, reset_registry
        from orchestration.tool_router import MCPRouter

        reset_registry()
        discover_connectors()
        MCPRouter()

    @patch.dict(os.environ, {"KAZI_DEMO_MODE": "false"}, clear=False)
    def test_router_consumes_registry_as_single_source(self):
        # v0.4 M2-1: MCPRouter no longer maintains its own action->connector dict;
        # it must reflect exactly what connector_registry.discover_connectors() exposes.
        from orchestration.connector_registry import discover_connectors, reset_registry
        from orchestration.tool_router import MCPRouter

        reset_registry()
        registry_map = discover_connectors()
        router = MCPRouter()

        self.assertEqual(set(router.connectors.keys()), set(registry_map.keys()))
        for action, connector in registry_map.items():
            self.assertIs(router.connectors[action], connector)
