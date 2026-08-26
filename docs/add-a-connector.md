# Add a Connector

The second developer flow. **One file plus one test** is the SLA.

Publishing or sharing a connector? See [Community Connectors](community-connectors.md)
for the contract, distribution paths, and the boot-time guardrails Kazi enforces.

## What a connector is

A `BaseConnector` subclass that:
- Declares a `name`, `version`, and list of `actions`.
- Returns a list of catalog entries from `get_action_catalog_entries()`.
- Implements `async def execute(parameters, context)`.

The runtime auto-discovers anything in
`Backend/orchestration/connectors/*.py` (built-in connectors) or
`examples/connectors/*/` (when `KAZI_DEMO_MODE=true`) or any pip
package that registers the `kazi.connectors` entry point. No manual
registration call.

## The minimum example

Copy `examples/connectors/echo/echo_connector.py`. Three steps:

1. Rename `EchoConnector` to your class name.
2. Update `name`, `version`, `actions`, `required_credentials`, and the
   one entry returned by `get_action_catalog_entries()` (see the
   [tool schema contract](contracts/tool-schema.md) for the exact
   shape).
3. Implement `execute()` to do the actual work and return
   `{"status": ..., "message": ..., "data": {...}}` per the
   [connector execution contract](contracts/connector-execution.md).

```python
from orchestration.base_connector import BaseConnector


class WeatherConnector(BaseConnector):
    name = "weather"
    version = "1.0.0"
    actions = ["get_weather"]
    required_credentials = ["OPENWEATHER_API_KEY"]

    def get_action_catalog_entries(self):
        return [{
            "action": "get_weather",
            "service": "weather",
            "description": "Get current weather for a city.",
            "params": {
                "city": {
                    "type": "string",
                    "required": True,
                    "description": "City name (e.g. 'Nairobi').",
                },
            },
            "risk_level": "low",
            "replay_safe": True,
        }]

    async def execute(self, parameters, context):
        city = parameters.get("city")
        api_key = self.get_credential("OPENWEATHER_API_KEY")
        # ... call the API ...
        return {
            "status": "success",
            "message": f"Weather for {city}: 24°C, sunny.",
            "data": {"city": city, "temp_c": 24, "conditions": "sunny"},
        }
```

## The minimum test

```python
import asyncio
from unittest.mock import patch
from django.test import SimpleTestCase

from orchestration.connectors.weather_connector import WeatherConnector


class WeatherConnectorTests(SimpleTestCase):
    def test_get_weather_returns_success(self):
        connector = WeatherConnector()
        with patch.object(connector, "get_credential", return_value="fake-key"):
            result = asyncio.run(connector.execute(
                {"action": "get_weather", "city": "Nairobi"},
                context={"user_id": 1},
            ))
        self.assertEqual(result["status"], "success")
        self.assertEqual(result["data"]["city"], "Nairobi")
```

## What you do **not** need to do

- **Register the connector anywhere.** The registry scans the
  directory at boot.
- **Touch `tool_router.py`** (renamed from `mcp_router.py` in v0.5; the
  old name is a deprecation shim). Connector resolution flows through
  `connector_registry.discover_connectors()` (v0.4 M2-1).
- **Update `action_catalog.py`.** Your `get_action_catalog_entries()`
  return value is registered automatically.
- **Re-export the class in `__init__.py`.** Auto-discovery uses
  `inspect.getmembers`.
- **Write a Pydantic schema.** The
  [tool schema contract](contracts/tool-schema.md) is dict-shaped and
  validated by `validate_catalog_entry()` at registration.

## Dependencies and credentials

- API keys belong in env vars or Django settings. Reference them by
  name in `required_credentials`. The framework will skip your
  connector at boot with a clean log line if any are missing — no
  crash.
- Use `self.get_credential("KEY_NAME")` inside `execute()`. Don't
  read `os.environ` directly so the credential lookup is consistent.
- Heavy deps (LLM SDKs, browser automation, big binaries) need a PR
  description that justifies the install size.

## Risk levels

The `risk_level` field on each catalog entry decides whether the
human-gated runtime pauses for approval:

| Level | When |
|---|---|
| `low` | Read-only, idempotent, cheap. Runs immediately. |
| `medium` | Mutates state but reversible. May or may not pause. |
| `high` | Mutates state irreversibly (sends, charges, deletes). **Always** pauses for human approval. |

Pair `risk_level` with `replay_safe`. A high-risk action with a
client-supplied idempotency key can set `replay_safe: True` so reruns
are allowed; without one, set it to `False` so the runtime refuses
unsafe replays. See [replay safety](contracts/replay-safety.md).

## Where to put your connector

| Where | When |
|---|---|
| `Backend/orchestration/connectors/<name>_connector.py` | Built-in: ships with the core, auto-discovered always. |
| `examples/connectors/<name>/<name>_connector.py` | Example or demo connector. Auto-discovered only when `KAZI_DEMO_MODE=true`. |
| External pip package with `[project.entry-points."kazi.connectors"]` | Community connector: distributable separately from the core. |

## Common mistakes

- **Returning `None` for `data`.** Use `{}` instead — the
  [execution contract](contracts/connector-execution.md) requires a dict.
- **Missing `description` on a param.** The planner has nothing to
  prompt with; the contract validator rejects it at boot.
- **Using `risk_level: "low"` for a write action.** The runtime won't
  pause for approval and you'll only notice when the side effect has
  already happened.
- **Reusing an `action` name across two connectors.** The registry
  resolves the conflict in favor of the new-style connector silently;
  the validator surfaces this as a warning.

## Next

- [`writing-a-connector.md`](writing-a-connector.md) — the longer
  reference (parameter types, error semantics, retry hints).
- [`connector-api-reference.md`](connector-api-reference.md) — the
  `BaseConnector` API in full.
- [`add-a-workflow.md`](add-a-workflow.md) — chain your connector into
  a multi-step workflow with the human-gated runtime.
