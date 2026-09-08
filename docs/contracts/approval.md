# Approval Contract

**Version:** 1.0 · **Status:** stable · **Tier:** documented only

What `WorkflowExecution.pending_approval` looks like, how the operator
answers it, and how agent-loop confirmations share the same durable
record table.

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
`status = "waiting"` with `waiting_on = "approval"`.

## Agent-loop approvals (`kind = "agent_loop"`)

The ReAct agent loop pauses high-risk tool calls for confirmation too.
Those pauses are recorded on the same `WorkflowApprovalRecord` table with
`kind = "agent_loop"`, `workflow = NULL`, `execution = NULL`, and
`room_id` set to the chat room. The loop state needed to resume lives in
Redis under `orchestration:agent_state:{room_id}:{user_id}`; the pending
tool is captured in `action` and `sanitized_params`.

- `requested_by_id` — the user whose loop paused.
- `expires_at` — `CONFIRMATION_STATE_TTL` (10 minutes) after the pause.
- Resolve via the chat surface: confirming resumes the loop (the record is
  marked `approved` **before** the tool executes), cancel/dismiss marks it
  `cancelled`. An approval whose loop state was lost is marked `rejected`
  and is never executed.
- Cross-user isolation: a pending record is only visible/actionable for
  the `(room_id, requested_by_id)` it was created for.

## Pending approval shape

`WorkflowApprovalRecord` (and the serialized `pending_approval` block
returned from the [execution detail](execution-detail.md) endpoint, via
`workflows/views._serialize_approval`):

```python
{
    "id": int,                    # the WorkflowApprovalRecord id
    "workflow_id": int | None,    # null for agent-loop approvals
    "execution_id": int | None,   # null for agent-loop approvals
    "step_id": str,               # which step is paused ("" for agent-loop)
    "action": str,                # the action name (e.g. "send_email")
    "service": str,               # the connector service
    "status": str,                # pending | approved | rejected | cancelled
    "approval_message": str,      # human-readable preview of what will happen
    "sanitized_params": dict,     # the params the step will run with (sanitized)
    "expires_at": str | None,     # ISO 8601; null if no timeout configured
    "review_comment": str | None, # operator note, set on approve/reject
    "reviewed_by_id": int | None, # operator who decided
    "created_at": str,            # ISO 8601
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

- **Approve** → step runs with the previewed params, receipt logged, the
  execution resumes (`status = "running"`) until the next step.
- **Reject** → the approval record is marked `rejected`; the step does not
  run and downstream steps are not attempted. (`rejected` is a record status,
  not an execution status — the execution itself ends `failed` or `cancelled`.)
- **Timeout** → the stuck-approval sweeper (`workflows/tasks.py`) dead-letters
  an approval that outlives its max pending age; the operator sees the reason
  in the inbox.

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
| 1.0 | v0.5 | Corrected to match the shipped `_serialize_approval()` (field names, `waiting_on`, no `on_timeout` on the record) and documented the `kind` (`workflow`/`agent_loop`) split with nullable `workflow`/`execution` and `room_id`. |
| 1.0 (draft) | v0.4 | First documented version. Described a planned shape (`summary`, `params_preview`, `on_timeout`) that never shipped as-is; superseded by the v0.5 correction. |

## Breaking changes

None in 1.0. The field names in the shipped serializer differ from the
pre-v0.5 draft, but the runtime never exposed the draft shape.
