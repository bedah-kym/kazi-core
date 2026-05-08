# Connector Execution Contract

**Version:** 1.1 · **Status:** stable · **Tier:** documented only

What every `BaseConnector.execute(parameters, context)` call must
return.

> **v1.1 honesty note** — v1.0 said `data` was required, but 24 of 25
> built-in connectors return result fields at the top level instead.
> v1.1 documents both shapes. v2.0 will narrow this back to the
> envelope shape and ship a connector migration. See [Two valid
> shapes](#two-valid-shapes-v11) below.

## Shape

```python
{
    "status": "success" | "error" | "needs_clarification",
    "message": str,                  # required for status != "success"; optional for success
    "data": dict | None,             # optional in v1.x; required in v2.0
    "receipt": dict | None,          # optional, see action_receipts
    "reason": str,                   # optional, machine-readable error reason
    # ...connector-specific fields at top level (legacy shape)
}
```

### Field detail

| Field | Type | Required | Notes |
|---|---|---|---|
| `status` | string enum | yes | One of `"success"`, `"error"`, `"needs_clarification"`. Anything else is treated as `"error"` by the executor. |
| `message` | string | conditional | Required when status is `"error"` or `"needs_clarification"` — surfaces to the user. Optional for `"success"`. |
| `data` | object or null | optional (v1.1) | Newer connectors should wrap structured payload here. Legacy connectors return result fields at the top level. Consumers must tolerate both — see [migration](#two-valid-shapes-v11). v2.0 will require this. |
| `receipt` | object or null | optional | If the connector wrote an audit receipt for this call, return it here so the runtime can persist it onto the execution row. |
| `reason` | string | optional | Machine-readable failure code for retry / suggestion logic (e.g. `"missing_param"`, `"upstream_timeout"`). |

## Two valid shapes (v1.1)

The contract supports two response shapes during the v1.x window:

**Envelope shape (recommended for new connectors):**

```python
{"status": "success", "data": {"city": "Nairobi", "temp_c": 24}}
```

**Legacy shape (current built-in connectors):**

```python
{"status": "success", "city": "Nairobi", "temp_c": 24, "message": "Weather for Nairobi: 24°C"}
```

Consumers in the runtime must defend against both:

```python
data_payload = result.get("data") if isinstance(result.get("data"), dict) else result
```

This is what `Backend/orchestration/tool_executor.py`,
`Backend/chatbot/consumers.py`, and `Backend/orchestration/agent_loop.py`
already do — the legacy shape works because every consumer is
defensive. v1.0's "data is required" claim was aspirational. v1.1
makes it ground truth.

## Migration to v2.0

When v2.0 ships:
- `data` becomes required (legacy shape rejected)
- All built-in connectors get rewritten to use the envelope
- `validate_connector_response()` will be added to `contracts.py` to
  enforce the envelope at runtime
- Until then, prefer the envelope shape in any new connector you write
  — it's already valid v1.1, will be the only valid shape in v2.0,
  and the runtime cost of switching later is just the diff

## Examples

**Success (envelope shape — recommended):**

```python
return {
    "status": "success",
    "message": "Email sent.",
    "data": {"message_id": "abc123", "to": "alex@example.com"},
}
```

**Success (legacy shape — works in v1.x):**

```python
return {
    "status": "success",
    "message": "Email sent.",
    "message_id": "abc123",
    "to": "alex@example.com",
}
```

**Error:**

```python
return {
    "status": "error",
    "message": "Could not reach Gmail.",
    "reason": "upstream_timeout",
}
```

**Needs clarification:**

```python
return {
    "status": "needs_clarification",
    "message": "Which Alex? (Found 3 contacts.)",
    "data": {"candidates": [...]},
}
```

## Consumer side

`Backend/orchestration/tool_executor.py` and the workflow runtime read
this shape using the dual-shape pattern shown above. If
`status == "success"`, downstream steps see the merged payload (data
fields if `data` is present, otherwise the top-level fields). If
`status == "error"`, the run fails or routes through the
`build_failure_summary()` path in `workflows/runtime.py`. If
`status == "needs_clarification"`, the message is surfaced as the next
prompt to the user.

## Common mistakes

- **Returning `None` for `data` in the envelope shape.** Use `{}`
  instead, or omit the key entirely (legacy shape).
- **Returning a string instead of a dict.** The executor will coerce
  or fail; neither is what you want.
- **Including secrets in `data`, `message`, or top-level fields.**
  Sanitization happens in `security_policy.sanitize_parameters()` on
  the way in, but connectors are responsible for not leaking on the
  way out.

## Changes since

| Version | Date | Change |
|---|---|---|
| 1.0 | v0.4.0 | First documented version. |
| 1.1 | v0.4.1 | Documents both legacy and envelope response shapes; `data` is now optional during the v1.x window. v1.0's claim that `data` was required did not match the 24/25 built-in connectors that return fields at top level. v1.1 makes the contract honest about ground truth; v2.0 will narrow back to envelope-only and ship the connector migration. |

## Breaking changes

None in 1.1. The envelope shape from 1.0 is still valid; the legacy
shape that 1.0 silently allowed is now explicitly documented.
