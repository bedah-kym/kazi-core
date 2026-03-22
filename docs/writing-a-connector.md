# Writing a Kazi Connector

Connectors are how you teach Kazi new skills. Each connector wraps an external
service (or internal logic) and exposes it as one or more actions the agent can call.

## Quick Start

1. Create a file in `Backend/orchestration/connectors/` (e.g., `slack_connector.py`)
2. Subclass `BaseConnector`
3. Implement three things: metadata, catalog entries, and execution logic
4. Restart the server — your connector auto-registers

## Minimal Example

```python
from orchestration.base_connector import BaseConnector


class SlackConnector(BaseConnector):
    name = "slack"
    version = "0.1.0"
    actions = ["send_slack_message"]
    required_credentials = ["SLACK_BOT_TOKEN"]

    def get_action_catalog_entries(self):
        return [{
            "action": "send_slack_message",
            "service": "slack",
            "description": "Send a message to a Slack channel",
            "params": {
                "channel": {
                    "type": "string",
                    "required": True,
                    "description": "Slack channel name or ID",
                },
                "text": {
                    "type": "string",
                    "required": True,
                    "description": "Message text",
                },
            },
            "risk_level": "medium",
            "confirmation_policy": "always",
        }]

    async def execute(self, parameters, context):
        token = self.get_credential("SLACK_BOT_TOKEN")
        channel = parameters.get("channel")
        text = parameters.get("text")

        import httpx
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                "https://slack.com/api/chat.postMessage",
                headers={"Authorization": f"Bearer {token}"},
                json={"channel": channel, "text": text},
            )
            data = resp.json()

        if data.get("ok"):
            return {"status": "success", "message": f"Sent to #{channel}"}
        return {"status": "error", "message": data.get("error", "Unknown error")}
```

## Connector Anatomy

### Class Attributes

| Attribute | Type | Required | Description |
|-----------|------|----------|-------------|
| `name` | `str` | Yes | Unique connector name (e.g., "slack") |
| `version` | `str` | No | Semantic version (default: "0.1.0") |
| `actions` | `list[str]` | Yes | Action names this connector handles |
| `required_credentials` | `list[str]` | No | Env var names needed for this connector |

### Methods to Implement

#### `get_action_catalog_entries() -> list[dict]`

Returns a list of action definitions. Each entry tells the agent:
- What the action does (used in the LLM prompt)
- What parameters it accepts
- How risky it is

Action entry fields:

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `action` | `str` | Yes | Unique action name |
| `service` | `str` | Yes | Service group name |
| `description` | `str` | Yes | What this action does (shown to the LLM) |
| `params` | `dict` | Yes | Parameter definitions (see below) |
| `risk_level` | `str` | Yes | `"low"`, `"medium"`, or `"high"` |
| `aliases` | `list[str]` | No | Alternative names for this action |
| `confirmation_policy` | `str` | No | `"always"`, `"high_risk"`, or `"never"` |
| `capability_gate` | `str` | No | Permission key required |
| `return_description` | `str` | No | Description of what the action returns |

Parameter definition fields:

| Field | Type | Description |
|-------|------|-------------|
| `type` | `str` | `"string"`, `"integer"`, `"number"`, `"boolean"` |
| `required` | `bool` | Whether this parameter is mandatory |
| `description` | `str` | What this parameter is (shown to the LLM) |

#### `execute(parameters, context) -> dict`

Runs the action. Receives:

- `parameters`: dict with `"action"` key plus all action-specific params
- `context`: dict with `"user_id"`, `"room_id"`, `"username"`, and `"preferences"`

Must return a dict with at least:

```python
{"status": "success", "message": "Human-readable result"}
# or
{"status": "error", "message": "What went wrong"}
```

You can include a `"data"` key with structured data the agent can reference.

### Built-in Helpers

#### `self.get_credential(name)`

Looks up a credential by name. Checks env vars first, then Django settings.

```python
token = self.get_credential("MY_API_KEY")
if not token:
    return {"status": "error", "message": "MY_API_KEY not configured"}
```

#### `self.validate_config()`

Called during registration. Checks that all `required_credentials` are available.
You can override this for custom validation (e.g., test API connectivity).

### Error Handling

For structured errors with retry semantics, raise `ConnectorError`:

```python
from orchestration.connectors.connector_error import ConnectorError

async def execute(self, parameters, context):
    try:
        result = await call_api(...)
    except RateLimitError:
        raise ConnectorError(
            "Rate limited by Slack API",
            error_code=ConnectorError.RATE_LIMIT,
            retry_after=30,
        )
    except AuthError:
        raise ConnectorError(
            "Slack token is invalid or expired",
            error_code=ConnectorError.AUTH_FAILED,
        )
```

## Installing Community Connectors

Community connectors can be distributed as pip packages. Users install them:

```bash
pip install kazi-connector-stripe
```

Package authors register via `pyproject.toml`:

```toml
[project.entry-points."kazi.connectors"]
stripe = "kazi_connector_stripe:StripeConnector"
```

The connector auto-discovers on startup.

## Risk Levels

| Level | When to use | User experience |
|-------|------------|-----------------|
| `low` | Read-only, no side effects (weather, search) | Executes immediately |
| `medium` | Creates/modifies data but reversible (reminders) | May ask for confirmation |
| `high` | Sends messages, moves money, books things | Always asks for confirmation |

## Tips

- Keep `description` clear and specific — the LLM uses it to decide when to call your action
- Use `required: True` only for params the action truly cannot work without
- Return helpful error messages — they become part of the conversation
- Test your connector with the echo pattern first, then add real API calls
- Check `example_connector.py` for the simplest possible starting point
