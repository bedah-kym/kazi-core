# Follow-up Email Demo

The canonical Kazi Core demo. Two steps:

1. **`draft_follow_up`** — auto-runs (low risk, `safe_to_replay: true`)
2. **`send_follow_up`** — **pauses durably for human approval**
   (`requires_approval: true`, `safe_to_replay: false`)

Together they exercise the v0.4 human-gated runtime end-to-end:
**request → workflow → approval → receipt → replay**.

The demo uses the `echo` example connector (from
[`examples/connectors/echo/`](../../connectors/echo/)) so it runs with
**no real credentials required** when `KAZI_DEMO_MODE=true`.

## Run it

### 1. Boot in demo mode

```bash
KAZI_DEMO_MODE=true docker compose up --build -d db redis web celery_worker celery_beat
docker compose exec web python Backend/manage.py migrate
docker compose exec web python Backend/manage.py createsuperuser   # any user works
```

### 2. Seed the demo workflow

```bash
docker compose exec web python Backend/manage.py seed_demo_workflow --user <your_username>
```

This creates a `UserWorkflow` row from `workflow.json` and prints the
workflow ID.

### 3. Start an execution

```bash
curl -X POST -H "Content-Type: application/json" \
  http://localhost:8000/api/workflows/<workflow_id>/run/ \
  -d '{"trigger_data": {}}'
# -> { "status": "started", "workflow_id": ..., "execution_id": ... }
```

### 4. Watch it pause for approval

```bash
curl http://localhost:8000/api/workflows/executions/<execution_id>/
```

The execution detail will show:

```json
{
  "status": "waiting",
  "current_step": "send_follow_up",
  "waiting_reason": "approval",
  "pending_approval": {
    "step_id": "send_follow_up",
    "step_action": "echo",
    "summary": "Sending follow-up email to alex@example.com"
  },
  "receipts": [ /* draft_follow_up's receipt */ ]
}
```

### 5. Approve

```bash
curl -X POST http://localhost:8000/api/workflows/executions/<execution_id>/approve/ \
  -H "Content-Type: application/json" \
  -d '{"note": "ok send it"}'
```

The workflow resumes, runs `send_follow_up`, and reaches `status: "completed"`.

### 6. Try a replay-safety check

```bash
# Try to rerun from the unsafe step — refused
curl -X POST http://localhost:8000/api/workflows/executions/<execution_id>/rerun/ \
  -H "Content-Type: application/json" \
  -d '{"from_step": "send_follow_up"}'
# -> 400, "Replay is blocked because these steps are not safe to replay: send_follow_up."

# Rerun from the safe step — allowed
curl -X POST http://localhost:8000/api/workflows/executions/<execution_id>/rerun/ \
  -H "Content-Type: application/json" \
  -d '{"from_step": "draft_follow_up"}'
# -> 200
```

## What this demo proves

| Demo behavior | v0.4 primitive |
|---|---|
| Step 2 paused durably across worker restart | M3-1 durable approval checkpoints |
| `GET /executions/<id>/` returns the operator-readable state | M3-2 execution detail records |
| `approve` / `reject` / `rerun` HTTP endpoints work | M3-3 operator controls |
| Replay refuses unsafe steps without `force` | M3-5 replay safety surfaced |
| Watchdog dead-letters runs that wait too long | M3-4 deferred-run watchdog *(uncomment the short timeout in workflow.json to see it)* |

## Adapt to a production workflow

Swap the `echo` action for real ones once your connectors have credentials:

```diff
   {
-    "id": "send_follow_up",
-    "service": "echo",
-    "action": "echo",
-    "params": {
-      "message": "Sending follow-up email to alex@example.com"
-    },
+    "id": "send_follow_up",
+    "service": "gmail",
+    "action": "send_email",
+    "params": {
+      "to": "alex@example.com",
+      "subject": "Following up on tomorrow",
+      "text": "Hi Alex, ..."
+    },
     "requires_approval": true,
     "safe_to_replay": false
   }
```

The runtime, approval, and replay-safety behavior is identical — the
only difference is whether the side effect is real.

## Files

| File | Purpose |
|---|---|
| [`workflow.json`](workflow.json) | The workflow definition. Loaded by the seed command. |
| [`README.md`](README.md) | This file. |

See also:
- [`scripts/demo.sh`](../../../scripts/demo.sh) — single-command driver
- [`docs/demo-mode.md`](../../../docs/demo-mode.md) — what `KAZI_DEMO_MODE` does
- [`docs/v0.4-brief.md`](../../../docs/v0.4-brief.md) — the v0.4 cycle this demo represents
