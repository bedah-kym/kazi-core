# Approval Contract

**Version:** 1.0 · **Status:** stable · **Tier:** documented only

What `WorkflowExecution.pending_approval` looks like and how the
operator answers it.

## When it appears

A workflow step pauses for approval when **any** of the following is
true:

- `step["requires_approval"]` is set to `True` in the workflow
  definition
- `step["action"]` is high-risk (per the [tool schema](tool-schema.md)
  contract)
- `requires_confirmation()` returns true for the action

The runtime persists a `WorkflowApprovalRecord` and writes its id onto
`WorkflowExecution.pending_approval_id`. The execution moves to
`status = "waiting"` with `waiting_reason = "approval"`.

## Pending approval shape

`WorkflowApprovalRecord` (and the serialized `pending_approval` block
returned from the [execution detail](execution-detail.md) endpoint):

```python
{
    "id": int,                   # the WorkflowApprovalRecord id
    "step_id": str,              # which step is paused
    "step_action": str,          # the action name (e.g. "send_email")
    "summary": str,              # human-readable preview of what will happen
    "params_preview": dict,      # the params the step will run with (sanitized)
    "requested_at": str,         # ISO 8601 UTC
    "deadline_at": str | None,   # ISO 8601 UTC; null if no timeout configured
    "on_timeout": "cancel" | "continue" | "fail",
}
```

## Operator answer shape

`POST /api/workflows/executions/<execution_id>/approve/`:

```json
{ "note": "string, optional, free-form audit message" }
```

`POST /api/workflows/executions/<execution_id>/reject/`:

```json
{ "reason": "string, optional, surfaced in the receipt and execution detail" }
```

Both endpoints return the updated [execution detail](execution-detail.md).

## Resume semantics

- **Approve** → step runs with the previewed params, receipt logged
  (`{"approved_by": user_id, "note": "..."}`), execution moves to
  `status = "running"` until the next step.
- **Reject** → step is skipped, execution moves to
  `status = "rejected"`, downstream steps are not attempted, the
  rejection reason is persisted on the execution row.
- **Timeout** (no answer before `deadline_at`) → behavior follows
  `on_timeout`: `cancel` ends the run, `continue` proceeds to the next
  step (use carefully), `fail` ends the run with a typed failure.
  Enforced by the deferred-run watchdog (`workflows/tasks.py`).

## Receipt

Every transition (approve, reject, timeout) writes a receipt into the
execution's receipts list. Operators reading the [execution
detail](execution-detail.md) see a complete audit trail.

## Example operator session

```bash
# Inspect what's waiting
curl http://localhost:8000/api/workflows/executions/42/
# -> { "status": "waiting", "pending_approval": { ... }, ... }

# Approve
curl -X POST http://localhost:8000/api/workflows/executions/42/approve/ \
  -H "Content-Type: application/json" \
  -d '{"note": "verified with Alex over chat"}'
# -> updated execution detail
```

## Common mistakes

- Marking a write action `risk_level: "low"`. The runtime will not
  pause for approval — and you'll only notice when the side effect
  has already happened.
- Approving via direct DB write. The approve/reject endpoints write
  the receipt and signal Temporal; bypassing them leaves the run in
  an inconsistent state.
- Rejecting and then expecting downstream steps to run. They won't.
  Use `cancel` if you want the whole run to stop with a different
  status; use `reject` to specifically refuse the gated step.

## Changes since

| Version | Date | Change |
|---|---|---|
| 1.0 | v0.4 | First documented version. Lifts the shape from the v0.4 M3-1/M3-3 implementation. |

## Breaking changes

None.
