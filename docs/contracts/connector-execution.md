# Connector Execution Contract

**Version:** 1.0 · **Status:** stable · **Tier:** documented only

What every `BaseConnector.execute(parameters, context)` call must
return.

## Shape

```python
{
    "status": "success" | "error" | "needs_clarification",
    "message": str,                  # required for status != "success"; optional for success
    "data": dict,                    # action-specific payload; may be empty
    "receipt": dict | None,          # optional, see action_receipts
    "reason": str,                   # optional, machine-readable error reason
}
```

### Field detail

| Field | Type | Required | Notes |
|---|---|---|---|
| `status` | string enum | yes | One of `"success"`, `"error"`, `"needs_clarification"`. Anything else is treated as `"error"` by the executor. |
| `message` | string | conditional | Required when status is `"error"` or `"needs_clarification"` — surfaces to the user. Optional for `"success"`. |
| `data` | object | yes | The action's structured payload. May be `{}`. Never `None` — return an empty dict instead. |
| `receipt` | object or null | optional | If the connector wrote an audit receipt for this call, return it here so the runtime can persist it onto the execution row. |
| `reason` | string | optional | Machine-readable failure code for retry / suggestion logic (e.g. `"missing_param"`, `"upstream_timeout"`). |

## Examples

**Success**:

```python
return {
    "status": "success",
    "message": "Email sent.",
    "data": {"message_id": "abc123", "to": "alex@example.com"},
}
```

**Error**:

```python
return {
    "status": "error",
    "message": "Could not reach Gmail.",
    "data": {},
    "reason": "upstream_timeout",
}
```

**Needs clarification**:

```python
return {
    "status": "needs_clarification",
    "message": "Which Alex? (Found 3 contacts.)",
    "data": {"candidates": [...]},
}
```

## Consumer side

`Backend/orchestration/tool_executor.py` and the workflow runtime read
this shape directly. If `status == "success"`, downstream steps see
the `data` payload and any `receipt` is appended to the execution row.
If `status == "error"`, the run fails or routes through the
`build_failure_summary()` path in `workflows/runtime.py`. If
`status == "needs_clarification"`, the message is surfaced as the next
prompt to the user.

## Common mistakes

- Returning `None` for `data`. Use `{}` instead.
- Returning a string instead of a dict. The executor will coerce or
  fail, neither is what you want.
- Including secrets in `data` or `message`. Sanitization happens in
  `security_policy.sanitize_parameters()` on the way in, but
  connectors are responsible for not leaking on the way out.

## Changes since

| Version | Date | Change |
|---|---|---|
| 1.0 | v0.4 | First documented version. Shape lifted from current `BaseConnector` docstring. |

## Breaking changes

None. This is the first documented version.
