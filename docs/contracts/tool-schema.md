# Tool Schema Contract

**Version:** 1.0 · **Status:** stable · **Tier:** validated on writes
(via `validate_catalog_entry` in `contracts.py`)

What every entry returned from
`BaseConnector.get_action_catalog_entries()` must look like.

## Shape

```python
{
    "action": str,                          # required, unique within the catalog
    "service": str,                         # required, group label
    "description": str,                     # required, surfaced to the planner
    "params": dict[str, dict],              # required, per-parameter schema
    "risk_level": "low" | "medium" | "high",  # required
    "aliases": list[str],                   # optional
    "confirmation_policy": "always" | "high_risk" | "never",  # optional
    "capability_gate": str,                 # optional
    "replay_safe": bool,                    # optional, see replay-safety.md
}
```

### Field detail

| Field | Type | Required | Notes |
|---|---|---|---|
| `action` | string | yes | Snake-case, unique across the entire registry. Becomes the routing key in `MCPRouter.connectors`. |
| `service` | string | yes | Group label — usually matches the connector's `name`. Multiple actions can share a service. |
| `description` | string | yes | One sentence the planner reads when deciding whether to use this action. Avoid jargon. |
| `params` | object | yes | Map of param-name → param-schema (see below). Empty `{}` is allowed for actions with no params. |
| `risk_level` | string enum | yes | `"low"` runs without confirmation. `"medium"` may prompt depending on context. `"high"` always pauses for approval (see [approval contract](approval.md)). |
| `aliases` | array of strings | no | Alternative names callers may use. Resolved through `action_catalog.resolve_action_alias`. |
| `confirmation_policy` | string enum | no | Override the default policy implied by `risk_level`. Rarely needed. |
| `capability_gate` | string | no | Permission name (e.g. `"allow_payments"`). Action is hidden from users without that capability. |
| `replay_safe` | bool | no | If `true`, the runtime allows rerunning a workflow from this step without `force=True`. Defaults to `false` for high-risk actions, `true` otherwise. See [replay safety](replay-safety.md). |

### Param schema

Each entry in `params` looks like:

```python
"city": {
    "type": "string",      # "string" | "number" | "integer" | "boolean" | "array" | "object"
    "required": True,      # bool, default False
    "description": "City name to look up weather for.",  # required
    "default": "Nairobi",  # optional
    "enum": ["a", "b"],    # optional, restricts allowed values
}
```

`description` is required because the planner uses it as the
parameter's prompt to the user when the model needs to fill a missing
slot.

## Example

```python
def get_action_catalog_entries(self):
    return [
        {
            "action": "get_weather",
            "service": "weather",
            "description": "Get the current weather for a city.",
            "params": {
                "city": {
                    "type": "string",
                    "required": True,
                    "description": "City name (e.g. 'Nairobi').",
                },
            },
            "risk_level": "low",
            "replay_safe": True,
        },
    ]
```

## Validation

`validate_catalog_entry(entry)` in `Backend/orchestration/contracts.py`
returns `(ok, errors)`. The connector registry calls it for every
entry returned by every connector at boot. A failing entry is logged
as a warning and the entry is skipped — the connector itself stays
loaded so a single bad entry doesn't break the whole boot.

## Common mistakes

- Forgetting `"description"` on a param. The planner has nothing to
  prompt with.
- Using a non-snake-case `action` name. Routing breaks, so the
  validator rejects it.
- Reusing an `action` name across two connectors. The registry
  resolves the conflict in favor of the new-style connector silently
  — the validator surfaces this as a warning.
- Setting `risk_level: "low"` for a write action. Use `"high"` so the
  human-gated runtime can pause for approval.

## Changes since

| Version | Date | Change |
|---|---|---|
| 1.0 | v0.4 | First documented version. `replay_safe` is the only field added in v0.4 — others lifted from the existing catalog. |

## Breaking changes

None. The pre-v0.4 catalog format is a strict subset of v1.0.
