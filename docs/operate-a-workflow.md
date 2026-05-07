# Operate a Workflow

The fourth developer flow. **Approve, reject, cancel, rerun, pause,
resume**, and read traces of any workflow run.

This page is for operators (humans driving the runtime), not for
authors (writing connectors or workflow definitions). For those see
[`add-a-connector.md`](add-a-connector.md) and
[`add-a-workflow.md`](add-a-workflow.md).

## The full operator loop

Every operator interaction with the v0.4 human-gated runtime runs
through five surfaces:

| Surface | Use it to |
|---|---|
| `kazi_trace` CLI | See what's happening on a specific execution. |
| `GET /api/workflows/inbox/` | List runs that need attention (pending approvals, dead-letters). |
| `GET /api/workflows/executions/<id>/` | Read the full execution detail. |
| `POST /api/workflows/executions/<id>/approve` (and friends) | Drive the run forward. |
| Django admin | Same operations with a UI for non-CLI workflows. |

## Step 1 — Find what needs you

```bash
curl http://localhost:8000/api/workflows/inbox/
```

Returns a JSON list of executions in `status = "waiting"` plus
deferred runs that have failed or dead-lettered. Each entry includes
the execution id, the step paused on, and the request shape.

## Step 2 — Inspect a specific run

```bash
python Backend/manage.py kazi_trace 42
```

Renders a chronological timeline (steps in declared order, approvals
in chronological order, watchdog history if any). See
[`docs/trace.md`](trace.md) for the full reference.

For a structured payload:

```bash
curl http://localhost:8000/api/workflows/executions/42/
```

Returns the [execution detail contract](contracts/execution-detail.md)
shape — status, current_step, waiting_reason, pending_approval,
receipts, replay_hints, dead_letter_reason.

## Step 3 — Approve, reject, cancel

```bash
# Approve a paused step
curl -X POST http://localhost:8000/api/workflows/executions/42/approve/ \
  -H "Content-Type: application/json" \
  -d '{"note": "verified with Alex over chat"}'

# Reject a paused step (run stops as 'rejected')
curl -X POST http://localhost:8000/api/workflows/executions/42/reject/ \
  -H "Content-Type: application/json" \
  -d '{"reason": "duplicate request"}'

# Cancel the entire run
curl -X POST http://localhost:8000/api/workflows/executions/42/cancel/ \
  -H "Content-Type: application/json" \
  -d '{"reason": "policy change — abandon this batch"}'
```

All three endpoints write a [receipt](contracts/approval.md) for the
audit trail. The execution detail reflects the new state on the next
poll.

## Step 4 — Rerun from a known-good step

The rerun control honors [replay safety](contracts/replay-safety.md):
unsafe steps are refused unless `force=true` is passed (and the
forced rerun is recorded on the receipt).

```bash
# Allowed — draft_follow_up is replay-safe
curl -X POST http://localhost:8000/api/workflows/executions/42/rerun/ \
  -H "Content-Type: application/json" \
  -d '{"from_step": "draft_follow_up"}'

# Refused — send_follow_up isn't safe to replay
curl -X POST http://localhost:8000/api/workflows/executions/42/rerun/ \
  -H "Content-Type: application/json" \
  -d '{"from_step": "send_follow_up"}'
# -> 400, "Replay is blocked because these steps are not safe to replay: send_follow_up."

# Allowed but logged — operator overrides with force
curl -X POST http://localhost:8000/api/workflows/executions/42/rerun/ \
  -H "Content-Type: application/json" \
  -d '{"from_step": "send_follow_up", "force": true}'
```

Inspect `replay_hints` on the execution detail to know which option
applies before you act.

## Step 5 — Pause and resume

```bash
# Pause a workflow trigger (e.g. a webhook source) — useful while
# investigating a misbehaving integration
curl -X POST http://localhost:8000/api/workflows/triggers/<trigger_id>/pause/

# Resume
curl -X POST http://localhost:8000/api/workflows/triggers/<trigger_id>/resume/
```

`pause` halts new executions; in-flight runs keep going to their
natural pause point (approval or completion).

## What the watchdog does for you

The deferred-run watchdog (Celery beat job in
`Backend/workflows/tasks.py`) scans `WorkflowExecution` rows in
`waiting_for_*` states. If a run exceeds its `max_wait_seconds`
without an answer, the watchdog dead-letters it and writes the reason
to `dead_letter_reason`. You see those runs in the inbox under a
distinct status.

Common dead-letter reasons:

- `approval_timeout` — `approval_timeout_minutes` elapsed; per the
  `on_timeout` policy on the step, the run was cancelled / failed /
  continued.
- `temporal_unavailable` — Temporal worker hasn't ack'd in N minutes.
- Custom — anything the workflow author surfaces from inside the
  step.

## Django admin alternative

`/admin/workflows/workflowexecution/` exposes the same approve /
reject / cancel / rerun / pause / resume actions as bulk actions.
Useful for sweeping a batch of executions during an incident.

## Common operator patterns

| Question | Answer |
|---|---|
| "Why is this stuck?" | `kazi_trace <id>` — look at `waiting_on` and the Approvals section. |
| "Did anyone approve this yet?" | `kazi_trace <id>` — Approvals section names the operator. |
| "Can I just rerun?" | Check `replay_hints.from_step` on the execution detail. |
| "Did the watchdog give up?" | `kazi_trace <id>` — Deferred-run history section will show `dead_letter`. |
| "Has this connector been failing for everyone?" | Cross-reference `kazi_trace` output across executions; aggregate via the admin. |

## Common mistakes

- **Approving without reading the `pending_approval.summary`.** The
  preview is what protects against an operator approving the wrong
  payload.
- **Forcing a rerun to "just make it work."** The receipt shows who
  did it. Treat `force=true` as a paper trail you'll be asked to
  defend.
- **Patching `WorkflowExecution` rows directly in the DB.** Bypasses
  the receipt + Temporal signals; leaves the run inconsistent. Always
  go through the operator endpoints.
- **Polling the execution detail every second.** Once-per-second is
  the cap; transitions are infrequent — tail the trace instead.

## Next

- [`docs/trace.md`](trace.md) — the trace CLI in full.
- [`docs/contracts/`](contracts/README.md) — the runtime contracts
  every operator surface promises to honor.
- [`docs/deploy-safely.md`](deploy-safely.md) — what the operator
  surface looks like in production.
