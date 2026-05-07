# Eval Harness

Kazi Core ships a **golden scenario evaluator** for the orchestration
core (intent parsing + workflow planning). Run it locally, run it in
CI, treat regressions in planner output the same way you treat
regressions in code.

This page covers what the harness is, how to run it, how to add your
own scenarios, and how the CI job is wired.

---

## What gets evaluated

The harness runs each scenario through one or both of:

- **Intent parsing** — `orchestration.intent_parser.parse_intent()`. A
  scenario's `expected_intent_action` is matched against the parsed
  action.
- **Workflow planning** — `orchestration.workflow_planner.plan_user_request()`.
  A scenario's `expected_mode` is matched against the planner's chosen
  mode; `expected_actions` is a subset-match against the actions in the
  produced plan's steps.

Each scenario can opt into either or both checks.

## Quick start

Run all scenarios that don't need an LLM:

```bash
python Backend/manage.py run_golden_eval
```

Run all scenarios including the LLM-backed ones (needs
`ANTHROPIC_API_KEY`):

```bash
python Backend/manage.py run_golden_eval --allow-llm
```

Run a custom scenario file:

```bash
python Backend/manage.py run_golden_eval --path /path/to/my_scenarios.json
```

Limit the run to the first N scenarios (handy when iterating on a
single one):

```bash
python Backend/manage.py run_golden_eval --allow-llm --limit 1
```

## Output

Per-scenario lines and a totals summary:

```
[PASS] travel_email_results
[FAIL] hotel_search: missing action search_hotels
[SKIP] weather_single (requires LLM)
[PASS] quota_status

Total: 4 | Passed: 2 | Failed: 1 | Skipped: 1
Failures:
- hotel_search: missing action search_hotels
```

Exit code is **0 on success or skips, non-zero on failures** — CI can
gate on this directly when you flip the advisory job to blocking.

## Scenario format

Scenarios live in `Backend/orchestration/eval/golden_scenarios.json` as
a JSON array. Minimal example:

```json
[
  {
    "id": "weather_single",
    "message": "What is the weather in Nairobi?",
    "history": "",
    "expected_intent_action": "get_weather",
    "requires_llm": true
  }
]
```

### Fields

| Field | Type | Meaning |
|---|---|---|
| `id` | string | Stable identifier for the scenario. Shown in pass/fail output. |
| `message` | string | The user message under test. |
| `history` | string | Optional. Conversation history string, in the same format consumed by the planner. |
| `preferences` | object | Optional. User preferences passed into the planner (`date_order`, `time_format`, `tone`, etc.). |
| `requires_llm` | bool | If `true`, the scenario is **skipped** unless `--allow-llm` is set. |
| `expected_intent_action` | string | If set, runs `parse_intent` and asserts the parsed action matches. |
| `expected_mode` | string | If set, runs `plan_user_request` and asserts the planner mode matches (e.g. `adhoc_workflow`, `single_action`). |
| `expected_actions` | array of strings | If set, runs `plan_user_request` and asserts every listed action appears in the plan. |

### Adding scenarios

1. Capture a real-or-realistic user message you want the planner to
   handle.
2. Decide what's worth pinning: the intent action, the planner mode,
   the action list, or some combination.
3. Add the entry to `golden_scenarios.json`.
4. Run `python Backend/manage.py run_golden_eval --allow-llm --limit 1`
   in isolation and confirm it passes before committing.
5. PRs that change planner or intent-parser logic should justify any
   scenario regressions in the description.

## CI integration

The harness runs in CI as an **advisory job** in
`.github/workflows/main.yml`:

```yaml
- name: Eval - golden scenarios (advisory until coverage matures)
  continue-on-error: true
  run: python Backend/manage.py run_golden_eval
```

Without `--allow-llm` and without `ANTHROPIC_API_KEY` configured as a
repo secret, every LLM-dependent scenario is skipped. The job still
runs the harness end-to-end and reports the totals — that's the **smoke
test for the harness itself**.

To run the full LLM-backed eval in CI, add `ANTHROPIC_API_KEY` as a
repo secret and edit the job to:

```yaml
- name: Eval - golden scenarios (with LLM)
  continue-on-error: true
  env:
    ANTHROPIC_API_KEY: ${{ secrets.ANTHROPIC_API_KEY }}
  run: python Backend/manage.py run_golden_eval --allow-llm
```

The job stays advisory until enough scenarios accumulate that an
unintended planner regression would show up as a failure. Flip
`continue-on-error` to `false` once the coverage justifies it.

## Roadmap

- **v0.4 M4-1 (this surface)** — promoted from the loose
  `Backend/orchestration/eval/README.md` to a documented contributor
  surface, with an advisory CI job.
- **v0.4 M4-2** — the same scenarios will feed the `kazi trace` CLI
  so an operator can replay any failed scenario from the trace alone.
- **v0.5+** — mock LLM provider for credential-free eval coverage in
  CI; per-PR delta reporting (`scenarios_added` / `scenarios_now_failing`).

## See also

- [`Backend/orchestration/eval/README.md`](../Backend/orchestration/eval/README.md) — the in-tree note that
  pre-dates this doc; will get a one-line pointer here in a future
  cleanup.
- [`docs/architecture.md`](architecture.md) — where intent parsing and
  workflow planning sit in the runtime.
