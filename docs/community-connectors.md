# Community Connectors

Kazi's extension point is the **connector**: one file that teaches the agent a
new skill. This page is the contract for people building and sharing them, so
a connector you publish "just works" for anyone who installs it.

## The connector contract

A connector is a `BaseConnector` subclass with five required pieces:

| Piece | What it is | Example |
|---|---|---|
| `name` | Stable snake_case service id | `"ride_hailing"` |
| `version` | Semantic version you own | `"1.0.0"` |
| `actions` | Every action name this connector handles | `["estimate_ride", "book_ride"]` |
| `get_action_catalog_entries()` | One catalog entry per action | see below |
| `async def execute(parameters, context)` | The actual API call | see below |

**Return shape** (the [connector execution contract](contracts/connector-execution.md)):

```python
async def execute(self, parameters, context):
    return {"status": "success", "message": "Ride booked", "data": {...}}
    #   or
    return {"status": "error", "message": "No drivers nearby"}
```

Every result must be a dict with `status` `"success"` or `"error"`. On error,
include a `message` a human can read.

## What Kazi enforces for you

At startup the registry checks every connector and logs a warning when
something won't integrate cleanly. Read these warnings — they're telling you
exactly what's broken:

- **`execute()` must be `async def`.** A sync `execute` will always raise at
  runtime. Define it with `async def execute(...)`.
- **Catalog entry validation.** Each entry is checked against the
  [tool schema contract](contracts/tool-schema.md). Invalid entries are
  skipped with a warning, so one typo never breaks boot.
- **Action collisions.** Registering an action name another connector already
  owns logs a warning (the newest registration wins). Pick namespaced action
  names like `uber_book_ride`, not generic `book`.
- **`actions` vs catalog entries.** If `actions` lists something your catalog
  entries don't, the LLM will never see it as a tool. If an entry isn't in
  `actions`, it never routes at execution. Keep the two lists in sync.
- **Credentials.** Declare them in `required_credentials`; the connector is
  skipped with a clean message if they're missing. Read them with
  `self.get_credential("KEY")`.

## A worked example

```python
from orchestration.base_connector import BaseConnector
from orchestration.connectors.connector_error import ConnectorError


class RideHailingConnector(BaseConnector):
    name = "ride_hailing"
    version = "1.0.0"
    actions = ["estimate_ride", "book_ride"]
    required_credentials = ["RIDE_API_TOKEN"]

    def get_action_catalog_entries(self):
        return [
            {
                "action": "estimate_ride",
                "service": "ride_hailing",
                "description": "Estimate a ride's fare and ETA.",
                "params": {
                    "pickup": {"type": "string", "required": True, "description": "Pickup address"},
                    "dropoff": {"type": "string", "required": True, "description": "Drop-off address"},
                },
                "risk_level": "low",
                "replay_safe": True,
            },
            {
                "action": "book_ride",
                "service": "ride_hailing",
                "description": "Book a ride. This costs money.",
                "params": {
                    "pickup": {"type": "string", "required": True, "description": "Pickup address"},
                    "dropoff": {"type": "string", "required": True, "description": "Drop-off address"},
                    "ride_option_id": {"type": "string", "required": True, "description": "Ride option from estimate_ride"},
                },
                "risk_level": "high",  # costs money -> always asks for confirmation
            },
        ]

    async def execute(self, parameters, context):
        token = self.get_credential("RIDE_API_TOKEN")
        if not token:
            return {"status": "error", "message": "RIDE_API_TOKEN not configured"}

        action = parameters.get("action")
        try:
            if action == "estimate_ride":
                result = await self._call_api("estimates", parameters)
                return {"status": "success", "message": "Here are your options.", "data": result}
            if action == "book_ride":
                result = await self._call_api("rides", parameters)
                return {"status": "success", "message": "Ride booked.", "data": result}
        except ConnectorError:
            raise  # structured; the runtime surfaces the retry hint
        return {"status": "error", "message": f"Unknown action: {action}"}

    async def _call_api(self, endpoint, parameters):
        ...  # your HTTP/SDK code here
```

Key detail: `book_ride` is `risk_level: "high"`, so Kazi pauses for the user's
approval before it executes — no extra code needed on your side.

## Distributing your connector

Three paths, in order of how "official" they are:

1. **Built into core** — open a PR adding a file under
   `Backend/orchestration/connectors/`. It's auto-discovered. *(The v0.4
   release cycle is under a connector freeze — check `AGENTS.md` §5 before
   submitting.)*
2. **Example** — `examples/connectors/<name>/` ships a copy-paste starter;
   only loaded in `KAZI_DEMO_MODE=true`.
3. **Community pip package** — the standard path for third-party connectors:

   ```toml
   # pyproject.toml
   [project.entry-points."kazi.connectors"]
   ride_hailing = "kazi_connector_ride_hailing:RideHailingConnector"
   ```

   Then anyone installs it with `pip install kazi-connector-ride-hailing` and
   it's discovered on the next restart.

## How people find your connector

There is no central marketplace yet — this page is the closest thing to one.
If you publish a connector, open a PR adding a row to the directory below so
others can discover it:

| Connector | Actions | Install |
|---|---|---|
| *(yours here)* | — | `pip install ...` |

Discoverability today is the same as any Python package: PyPI, GitHub, and
word of mouth.

## Further reading

- [Add a Connector](add-a-connector.md) — the one-file-plus-one-test flow.
- [Writing a Connector](writing-a-connector.md) — full `BaseConnector` reference.
- [Connector API Reference](connector-api-reference.md) — interface + discovery.
- [Tool Schema](contracts/tool-schema.md) and
  [Connector Execution](contracts/connector-execution.md) — the stable contracts.
