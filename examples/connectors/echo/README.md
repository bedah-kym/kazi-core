# Echo Connector

The minimal Kazi connector — one action, no credentials, three obvious
files. Copy this directory to start your own.

## Files

| File | Purpose |
|---|---|
| [`echo_connector.py`](echo_connector.py) | The connector itself. ~60 lines. |
| [`README.md`](README.md) | This file. |

## Action

| Action | Risk | Params | Returns |
|---|---|---|---|
| `echo` | low | `message: str` | `{"status": "success", "message": "Echo: ...", "data": {...}}` |

## Try it

With `KAZI_DEMO_MODE=true` set, the runtime auto-discovers and registers
this connector at boot:

```
docker compose up -d
docker compose logs web | grep "Registered connector: echo"
```

Then, from the chat surface or a test script, call:

```python
from orchestration.connector_registry import discover_connectors

connectors = discover_connectors()
echo = connectors["echo"]
result = await echo.execute(
    {"action": "echo", "message": "hello"},
    context={"user_id": 1},
)
# result == {"status": "success", "message": "Echo: hello", "data": {...}}
```

## Adapt this to your own connector

1. Copy the `echo/` directory to `examples/connectors/<your_name>/`.
2. Rename `EchoConnector` and update `name`, `version`, `actions`.
3. Update `get_action_catalog_entries()` with your action shape.
4. Implement `execute()`.
5. Restart the runtime — the new connector auto-registers.

Full guide: [`../../../docs/writing-a-connector.md`](../../../docs/writing-a-connector.md).
