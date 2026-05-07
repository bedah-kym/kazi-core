# Replay Safety Contract

**Version:** 1.0 · **Status:** stable · **Tier:** documented only

How a connector action declares whether a step is safe to re-run, and
how the runtime surfaces that to operators and enforces it on the
rerun control.

## Why this exists

A workflow execution is a sequence of side effects. Some are
idempotent (read the weather, search a database, query a balance) —
running them twice is harmless. Some are not (send an email, charge a
card, withdraw funds) — running them twice doubles the side effect.

The runtime needs to know which is which so that:

- The rerun control can refuse unsafe replays unless an operator
  explicitly opts in with `force=True`.
- The operator API surfaces a clear "from which step is it safe to
  start over?" hint.

## Declaration

A connector declares per-action safety via the
[tool schema contract](tool-schema.md):

```python
{
    "action": "send_email",
    "service": "gmail",
    "description": "Send a single email.",
    "params": { ... },
    "risk_level": "high",
    "replay_safe": False,    # <-- this field
}
```

If `replay_safe` is omitted, the runtime defaults to:

- `True` when `risk_level` is `"low"` and the action does not require
  confirmation.
- `False` when `risk_level` is `"high"` or the action requires
  confirmation.

A workflow definition can override per-step with the same field name
on the step itself. Step-level wins over action-level.

## Computed `replay_hints`

The [execution detail](execution-detail.md) endpoint includes a
`replay_hints` object built from the workflow's step list and each
step's resolved `replay_safe`:

```json
"replay_hints": {
  "from_step": ["draft_follow_up"],
  "blocked_steps": ["send_follow_up"],
  "force_required_for": ["send_follow_up"]
}
```

| Field | Meaning |
|---|---|
| `from_step` | Steps the operator can rerun from without `force=True`. |
| `blocked_steps` | Steps that include or come after an unsafe step in the rerun slice. |
| `force_required_for` | Steps that need `force=True` to rerun. Logged on the receipt with the operator's identity. |

The computation is in `workflows/runtime.get_replayable_slice()`.

## Rerun semantics

`POST /api/workflows/executions/<execution_id>/rerun/`:

```json
{
  "from_step": "draft_follow_up",
  "force": false
}
```

| Outcome | Condition |
|---|---|
| 200 + new execution started | `from_step` is in `replay_hints.from_step` |
| 400 "Replay is blocked..." | `from_step` is in `blocked_steps` and `force=false` |
| 200 + new execution started, `force_replay: true` on the receipt | `from_step` is in `force_required_for` and `force=true` |
| 404 "Unknown replay step" | `from_step` doesn't match any step id in the workflow definition |

## Producer side

When you write a connector:

- Set `replay_safe: True` on read-only and idempotent actions.
- Set `replay_safe: False` on actions that mutate external state
  without an idempotency key.
- For actions that *can* be made idempotent (e.g. with a
  client-supplied request id), build the connector to use it and then
  set `replay_safe: True`. The
  [`resolve_step_idempotency_key`](../../Backend/workflows/runtime.py)
  helper exists for this.

## Consumer side

The rerun endpoint enforces. The operator UI/CLI should surface
`replay_hints` directly so operators don't have to reason about it.

## Common mistakes

- Setting `replay_safe: True` on an action that mutates state without
  an idempotency key. This is the bug class that costs real money.
- Forgetting to set `replay_safe: True` on a read action. Operators
  hit a wall on rerun for no reason.
- Using `force=true` to "make rerun work" instead of fixing the
  underlying step. The receipt will record who did this and why —
  treat the audit trail as deterrent.

## Changes since

| Version | Date | Change |
|---|---|---|
| 1.0 | v0.4 | First documented version. Implementation lifted from `workflows/runtime.is_step_safe_to_replay` and `get_replayable_slice` (v0.4 M3-5). |

## Breaking changes

None.
