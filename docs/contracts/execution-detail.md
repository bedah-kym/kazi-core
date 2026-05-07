# Execution Detail Contract

**Version:** 1.0 · **Status:** stable · **Tier:** documented only

What `GET /api/workflows/executions/<execution_id>/` returns. The
**single source of truth** for what's happening on a workflow run.

## Shape

```json
{
  "id": 42,
  "workflow_id": 7,
  "workflow_name": "Follow-up email demo",
  "trigger_type": "manual",
  "status": "running" | "waiting" | "completed" | "failed" | "rejected" | "cancelled" | "abandoned",
  "current_step": "send_follow_up",
  "waiting_reason": "approval" | "webhook" | "scheduled" | "" ,
  "pending_approval": { ... } | null,
  "receipts": [ { ... }, ... ],
  "result_summary": "string",
  "replay_hints": {
    "from_step": [ "draft_follow_up" ],
    "blocked_steps": [ "send_follow_up" ],
    "force_required_for": [ "send_follow_up" ]
  },
  "dead_letter_reason": "string" | null,
  "started_at": "2026-05-07T04:21:29Z",
  "updated_at": "2026-05-07T04:23:11Z",
  "completed_at": "2026-05-07T04:23:11Z" | null
}
```

### Status enum

| Value | Meaning |
|---|---|
| `running` | A worker is actively executing a step. |
| `waiting` | Paused on something — `waiting_reason` says what. |
| `completed` | All steps ran successfully. |
| `failed` | A step errored and the run stopped. |
| `rejected` | An operator rejected a gated step. |
| `cancelled` | An operator cancelled the run. |
| `abandoned` | The watchdog dead-lettered the run; see `dead_letter_reason`. |

### Field detail

| Field | Type | Notes |
|---|---|---|
| `current_step` | string | The step id the run is on or last touched. Reset on rerun. |
| `waiting_reason` | string | Empty when `status != "waiting"`. Otherwise describes what's needed. |
| `pending_approval` | object or null | When non-null, see the [approval contract](approval.md). |
| `receipts` | array | Audit records produced so far, in chronological order. Each receipt has at minimum `step_id`, `outcome`, `timestamp`. |
| `result_summary` | string | Multi-line operator-readable summary built by `workflows/runtime.build_result_summary`. |
| `replay_hints` | object | See [replay-safety contract](replay-safety.md). |
| `dead_letter_reason` | string or null | Set by the watchdog (`workflows/tasks.py`) when a waiting run exceeds its max-wait policy. |

## Producer side

`workflows/views._serialize_execution()` builds this object. Every
field listed above is populated from `WorkflowExecution` columns
(`status`, `current_step`, `waiting_reason`, `pending_approval`,
`receipts`, `result_summary`, `replay_hints`, `dead_letter_reason`)
plus the related `WorkflowApprovalRecord` row (when present).

## Consumer side

- The operator surface (admin actions + CLI tools) reads this
  endpoint to know what action to offer.
- The forthcoming `kazi trace <run_id>` CLI (M4-2) renders this plus
  the underlying telemetry events into a timeline.
- External monitors (e.g. a status dashboard) can poll this endpoint
  and trigger alerts on `status == "abandoned"` or unusual time spent
  in `waiting`.

## Stability promise

Every field listed above is stable for v0.4.x. New fields may appear
without warning at minor versions; field removals or type changes go
through a deprecation cycle.

## Common mistakes

- Treating `status == "waiting"` as a failure. It's the human-gated
  runtime working correctly — surface the `pending_approval` to an
  operator instead.
- Polling this endpoint at high frequency. The execution row updates
  on every step transition; once-per-second is plenty.
- Writing to `WorkflowExecution` directly to "fix" a stuck run. Use
  the operator endpoints; they emit receipts and signal Temporal
  correctly.

## Changes since

| Version | Date | Change |
|---|---|---|
| 1.0 | v0.4 | First documented version. All fields lifted from the v0.4 M3-2 implementation (migration `0003_human_gated_runtime`). |

## Breaking changes

None. The pre-v0.4 execution shape is a strict subset.
