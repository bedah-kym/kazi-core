# Trace CLI

`python Backend/manage.py kazi_trace <execution_id>` renders a
human-readable timeline for a single workflow execution. The v0.4
brief's "developer can debug an agent run from the trace alone" SLA
is held against this command.

## Quick example

```
$ python Backend/manage.py kazi_trace 42
========================================================================
 Execution #42  workflow='Follow-up email demo' (id=7)
========================================================================
  trigger:        manual
  status:         completed
  current step:   send_follow_up
  started:        2026-05-07T04:21:29Z
  completed:      2026-05-07T04:23:11Z

  result summary:
    draft_follow_up: completed successfully
    send_follow_up: completed successfully

Steps (in declared order):
   1. draft_follow_up              completed                action='echo'  [replay-safe]
   2. send_follow_up               completed                action='echo'  [approval]

Approvals:
  - step='send_follow_up'  status=approved  requested=2026-05-07T04:22:01Z  decided=2026-05-07T04:22:55Z
      summary: Sending follow-up email to alex@example.com
      by: demo  note: ok send it

Receipts: 2 record(s) — ids=[101, 102]
```

## What the trace shows

| Section | Source | Why it matters |
|---|---|---|
| Execution header | `WorkflowExecution` row | Status + timing — answer "what happened?" in one paragraph. |
| Steps in declared order | Workflow definition + `last_completed_step` / `current_step` | Per-step status (completed / running / waiting / pending / skipped / failed). The order is the **declared** order, so a stalled run still shows you where it stopped. |
| Approvals | `WorkflowApprovalRecord` rows | Every pause-and-decide cycle for this execution: who decided, when, with what note. |
| Deferred-run history | `DeferredWorkflowExecution` rows | Watchdog activity: backoffs, dead-letters, recovery hints. |
| Receipts | `WorkflowExecution.receipt_ids` | Audit trail count + ids; pull individual receipts via the receipts API. |

## JSON mode

For piping into other tools or building custom dashboards:

```bash
python Backend/manage.py kazi_trace 42 --json | jq '.steps[] | select(.status != "completed")'
```

The JSON shape is the same struct that backs the pretty render. It is
**not** a stable contract yet — treat it as a v0.4 implementation
detail and pin via grep at your own risk. A formal trace event
contract is queued for v0.5 (see `docs/contracts/README.md`).

## Common operator patterns

| Question | Command |
|---|---|
| "Why is execution 42 stuck?" | `kazi_trace 42` — look at `status` and `waiting_on` |
| "Who approved step X?" | `kazi_trace 42` — Approvals section names the operator |
| "Has this run been retried by the watchdog?" | `kazi_trace 42` — Deferred-run history section |
| "Is the run safe to rerun from step Y?" | check the `[replay-safe]` mark in the Steps section |
| "Give me the trace as machine-readable" | `kazi_trace 42 --json` |

## Roadmap

- **v0.4 M4-2 (this command)** — first version; reads execution + approvals + deferred-run rows.
- **v0.5** — formal trace event contract under `docs/contracts/`; the trace command will additionally consume `telemetry/orchestration.jsonl` so per-step LLM calls, planner decisions, and intermediate state changes appear inline.
- **v0.5** — optional `--follow` flag for live-tailing an in-flight execution.

## See also

- [`docs/contracts/execution-detail.md`](contracts/execution-detail.md) — the data the trace renders
- [`docs/contracts/approval.md`](contracts/approval.md) — the approval shape that drives the Approvals section
- [`docs/contracts/replay-safety.md`](contracts/replay-safety.md) — what the `[replay-safe]` mark means
- [`docs/eval.md`](eval.md) — the golden-scenario harness that catches regressions before they show up in a real trace
