# Add a Workflow

The third developer flow. A workflow is a JSON document that
sequences connector actions, marks which ones need human approval,
and declares which ones are safe to replay.

## What a workflow is

A `UserWorkflow` row whose `definition` field is a JSON object with
this shape (see also
[`examples/workflows/follow_up_email/workflow.json`](../examples/workflows/follow_up_email/workflow.json)):

```json
{
  "name": "Follow-up email demo",
  "description": "Send Alex a follow-up about tomorrow's meeting.",
  "version": "1.0.0",
  "steps": [
    {
      "id": "draft_follow_up",
      "service": "echo",
      "action": "echo",
      "params": {"message": "Drafting..."},
      "safe_to_replay": true
    },
    {
      "id": "send_follow_up",
      "service": "echo",
      "action": "echo",
      "params": {"message": "Sending..."},
      "requires_approval": true,
      "safe_to_replay": false,
      "depends_on": ["draft_follow_up"]
    }
  ],
  "triggers": [{"type": "manual"}]
}
```

## Step fields

| Field | Required | Notes |
|---|---|---|
| `id` | yes | Unique within the workflow. The trace, approvals, and rerun all reference steps by id. |
| `service` | yes | The connector service name. Usually matches the connector's `name`. |
| `action` | yes | The action this step calls. Must exist in the action catalog (declared by some connector's `get_action_catalog_entries()`). |
| `params` | yes | Parameters passed to the connector. Templating with `{{ previous_step.field }}` is supported. |
| `requires_approval` | no | If `true`, the step pauses for human approval before running. Defaults to `false`; high-risk actions always pause regardless. |
| `safe_to_replay` | no | If `true`, the rerun control allows starting from this step without `force=True`. Defaults follow the action's `replay_safe` declaration. See [replay safety](contracts/replay-safety.md). |
| `approval_timeout_minutes` | no | How long to wait for approval. Defaults to 60. |
| `on_timeout` | no | What to do if approval expires: `cancel`, `continue`, `fail`. Defaults to `cancel`. |
| `timeout_seconds` | no | Per-step execution timeout. Defaults to 300. |
| `max_attempts` | no | Retry count on transient errors. Defaults to 3. |
| `depends_on` | no | List of step ids that must complete before this one. The manager verifier reorders steps to honor dependencies. |

## How to add one

### Option 1 — Seed from a JSON file (the demo path)

Drop a file under `examples/workflows/<name>/workflow.json`. Then
seed it into the database with a one-shot management command:

```bash
python Backend/manage.py seed_demo_workflow --user <username>
```

The command lives at
`Backend/workflows/management/commands/seed_demo_workflow.py` and is
the model to copy for your own seeders. It is idempotent
(`update_or_create(user, name)`).

### Option 2 — Construct directly

```python
from django.contrib.auth import get_user_model
from workflows.models import UserWorkflow

User = get_user_model()
user = User.objects.get(username="alex")

UserWorkflow.objects.create(
    user=user,
    name="Send follow-up email",
    description="What it does in one sentence.",
    definition={
        "name": "Send follow-up email",
        "version": "1.0.0",
        "steps": [...],
        "triggers": [{"type": "manual"}],
    },
)
```

### Option 3 — Let the agent compose one

When a user asks the agent to do something multi-step, the
[`workflow_planner`](../Backend/orchestration/workflow_planner.py)
proposes a workflow definition, the
[`manager_verifier`](../Backend/orchestration/manager_verifier.py)
reorders + fills gaps + catches bad plans, and the runtime executes
it. You don't need to write the JSON by hand for ad-hoc requests —
this is the path most chat-driven workflows take.

## Triggering a run

Once the workflow exists in the DB:

```bash
curl -X POST http://localhost:8000/api/workflows/<workflow_id>/run/ \
  -H "Content-Type: application/json" \
  -d '{"trigger_data": {}}'
```

Returns `{"status": "started", "execution_id": <id>}`. From there,
[operate-a-workflow.md](operate-a-workflow.md) takes over.

## Templating params from previous steps

A param value can reference a prior step's output:

```json
"params": {
  "to": "{{ lookup_contact.email }}",
  "subject": "Following up on tomorrow"
}
```

The runtime resolves the template after the prior step completes and
just before this step runs. References to steps that haven't run yet
or that returned an error are caught by the manager verifier.

## Common mistakes

- **Step `id`s that aren't unique.** Approval and rerun fall over
  silently — both target the first matching step.
- **`requires_approval: true` on a low-risk step.** Annoying, not
  dangerous. The runtime pauses but the operator should reject it as
  unnecessary friction; consider marking the step low-risk instead.
- **`safe_to_replay: true` on a step without an idempotency key.**
  Rerun produces double side effects. The
  [replay-safety contract](contracts/replay-safety.md) covers how to
  pair this with `resolve_step_idempotency_key`.
- **Forgetting `depends_on`.** The manager verifier tries to reorder
  steps based on parameter references, but explicit `depends_on` is
  always safer.

## Next

- [`operate-a-workflow.md`](operate-a-workflow.md) — drive a run
  through the human-gated loop and the trace CLI.
- [`contracts/`](contracts/README.md) — the runtime contracts every
  workflow author works against.
- [`examples/workflows/follow_up_email/`](../examples/workflows/follow_up_email/) — the canonical demo.
