# Connector API Reference

## BaseConnector

```python
from orchestration.base_connector import BaseConnector
```

### Class Attributes

| Attribute | Type | Default | Description |
|-----------|------|---------|-------------|
| `name` | `str` | `""` | Unique connector identifier |
| `version` | `str` | `"0.1.0"` | Semantic version string |
| `actions` | `list[str]` | `[]` | Action names this connector handles |
| `required_credentials` | `list[str]` | `[]` | Env var names that must be set |

### Methods

#### `validate_config() -> tuple[bool, str]`

Checks if all `required_credentials` are available. Returns `(True, "")` on
success or `(False, "error message")` on failure. Called during auto-discovery.

Override for custom validation (e.g., test API connectivity).

#### `get_credential(key: str) -> str | None`

Look up a credential by name. Checks environment variables first, then
Django settings. Returns `None` if not found.

#### `get_action_catalog_entries() -> list[dict]`

Return action definitions for the catalog. Must be implemented by subclasses.

#### `async execute(parameters: dict, context: dict) -> dict`

Execute an action. Must be implemented by subclasses.

**parameters dict:**
- `action` (str): The action name being executed
- Plus all action-specific parameters from the LLM

**context dict:**
- `user_id` (int): Authenticated user's ID
- `room_id` (int): Current chatroom ID
- `username` (str): User's display name
- `preferences` (dict): User's style/locale preferences

**Return value:** Dict with at least `{"status": "success"|"error"}`.

## ConnectorError

```python
from orchestration.connectors.connector_error import ConnectorError
```

Structured error with retry semantics. Raise this instead of returning
error dicts for automatic retry handling.

### Constructor

```python
ConnectorError(
    message: str,
    error_code: str = "SERVICE_ERROR",
    retry_after: int | None = None,
    details: dict | None = None,
)
```

### Error Codes

| Code | When to use |
|------|------------|
| `ConnectorError.RATE_LIMIT` | API rate limit hit |
| `ConnectorError.AUTH_FAILED` | Invalid or expired credentials |
| `ConnectorError.SERVICE_ERROR` | Generic service failure |
| `ConnectorError.VALIDATION_FAILED` | Bad parameters |
| `ConnectorError.NETWORK_ERROR` | Connection failed |
| `ConnectorError.TIMEOUT` | Request timed out |
| `ConnectorError.NOT_FOUND` | Resource not found |
| `ConnectorError.PERMISSION_DENIED` | Insufficient permissions |

### Methods

- `is_retryable() -> bool` — True if `retry_after` is set
- `to_response() -> dict` — Convert to standard API response format

## Action Catalog Entry Format

```python
{
    "action": "my_action",           # Required: unique action name
    "service": "my_service",         # Required: service group
    "description": "Does something", # Required: shown to the LLM
    "params": {                      # Required: parameter schema
        "param_name": {
            "type": "string",        # string | integer | number | boolean
            "required": True,
            "description": "What this param is",
        },
    },
    "risk_level": "low",             # Required: low | medium | high

    # Optional
    "aliases": ["alt_name"],
    "confirmation_policy": "always", # always | high_risk | never
    "capability_gate": "perm_key",
    "return_description": "What the action returns",
}
```

## Auto-Discovery

Connectors are discovered from three sources (in order):

1. **Legacy connectors** — hardcoded in `mcp_router.py` (backward compat)
2. **Directory scan** — `Backend/orchestration/connectors/*.py` files containing
   `BaseConnector` subclasses with non-empty `name` and `actions`
3. **Entry points** — pip packages registering under `kazi.connectors`

New-style connectors override legacy ones on action name conflicts.

### Entry Point Registration (for pip packages)

```toml
# pyproject.toml
[project.entry-points."kazi.connectors"]
my_connector = "my_package.connector:MyConnector"
```
