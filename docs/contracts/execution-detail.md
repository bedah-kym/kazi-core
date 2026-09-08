# Execution Detail Contract

**Version:** 1.0 · **Status:** stable · **Tier:** documented only

What `GET /api/workflows/executions/<execution_id>/` returns. The
**single source of truth** for what's happening on a workflow run.

> This contract reflects the shipped serializer
> `workflows/views._serialize_execution()` as of v0.4.2. A richer v1.0
> shape (`receipts` objects, `replay_hints`, `waiting_reason`) is tracked
> as a planned enhancement — see [Breaking changes](#breaking-changes).

## Shape

```json
{
  "id": 42,
  "workflow_id": 7,
  "status": "pending" | "running" | "waiting" | "completed" | "failed" | "cancelled",
  "current_step": "send_follow_up",
  "last_completed_step": "draft_follow_up",
  "waiting_on": "approval" | "webhook" | "scheduled" | "",
  "attempts": { "draft_follow_up": 1 },
  "trigger_type": "manual",
  "trigger_data": { "source": "agent_handoff" },
  "result_summary": "string",
  "receipt_ids": [ 12, 13 ],
  "pending_approval": { ... } | null,
  "temporal_ids": {
    "workflow_id": "string",
    "run_id": "string" | null
  },
  "failure_summary": "string" | null,
  "recovery_suggestion": "string" | null,
  "result": { ... } | null,
  "error_message": "string" | null,
  "started_at": "2026-05-07T04:21:29Z",
  "completed_at": "2026-05-07T04:23:11Z" | null
}
```

### Status enum

| Value | Meaning |
|---|---|
| `pending` | Created but not yet started. |
| `running` | A worker is actively executing a step. |
| `waiting` | Paused on something — `waiting_on` says what. |
| `completed` | All steps ran successfully. |
| `failed` | A step errored and the run stopped. |
| `cancelled` | An operator cancelled the run. |

`rejected` is **not** an execution status — it is the status of the
related `WorkflowApprovalRecord` (see the [approval contract](approval.md)).
A rejected step leaves the execution in `waiting` or `failed`, depending
on the runtime.

### Field detail

| Field | Type | Notes |
|---|---|---|
| `current_step` | string | The step id the run is on or last touched. Reset on rerun. |
| `last_completed_step` | string | The most recent step that finished successfully. |
| `waiting_on` | string | Empty when `status != "waiting"`. Otherwise describes what's needed. |
| `attempts` | object | Per-step attempt counters. |
| `trigger_type` | string | `manual`, `schedule`, `webhook`, etc. |
| `trigger_data` | object | Payload that started the run. |
| `result_summary` | string | Operator-readable summary. Built by `workflows/runtime.build_result_summary` when the stored summary is empty. |
| `receipt_ids` | array of int | IDs of audit receipts produced so far, in chronological order. |
| `pending_approval` | object or null | When non-null, see the [approval contract](approval.md). |
| `temporal_ids` | object | The Temporal workflow/run ids, when running durably. |
| `failure_summary` / `recovery_suggestion` | string or null | Failure diagnostics + suggested operator action. |
| `result` | object or null | The raw workflow result, if any. |
| `error_message` | string or null | The last error, if the run failed. |

## Producer side

`workflows/views._serialize_execution()` builds this object. Fields come
from `WorkflowExecution` columns (`status`, `current_step`,
`last_completed_step`, `waiting_on`, `attempts`, `trigger_type`,
`trigger_data`, `result_summary`, `receipt_ids`, `failure_summary`,
`recovery_suggestion`, `result`, `error_message`, `started_at`,
`completed_at`), the related `WorkflowApprovalRecord` row (via
`_serialize_approval`), and — when a runtime state blob is available —
live values from Temporal override the persisted columns.

## Consumer side

- The operator surface (admin actions + CLI tools) reads this endpoint
  to decide what action to offer.
- `python Backend/manage.py kazi_trace <execution_id>` renders this plus
  the underlying telemetry events into a timeline.
- External monitors can poll this endpoint and alert on `failed` /
  `cancelled` or unusual time spent in `waiting`.

## Deferred executions

`dead_letter_reason` and the `abandoned` status live on
`DeferredWorkflowExecution` (the Temporal-disabled queue), not on this
endpoint. See [Operate a Workflow](../operate-a-workflow.md) for the
deferred-run watchdog behavior.

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
- Reading `receipts` as objects — the field is `receipt_ids` (ints);
  fetch the receipt records separately if you need their bodies.

## Changes since

| Version | Date | Change |
|---|---|---|
| 1.0 | v0.4.2 | Corrected to match the shipped `_serialize_execution()` (field names, status enum, `receipts` → `receipt_ids`). Previous draft described an unreleased v1.0 shape. |

## Breaking changes

The pre-v0.4 execution shape is a strict subset. Planned (not yet
shipped) additions: `receipts` objects, `replay_hints`, and
execution-level `waiting_reason` / `dead_letter_reason`.
