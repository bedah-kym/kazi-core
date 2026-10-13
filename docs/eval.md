# Eval Harness

Kazi Core ships a **golden scenario evaluator** for the orchestration
core (the agent loop, intent parsing + workflow planning). Run it
locally, run it in CI, treat regressions in agent behaviour the same
way you treat regressions in code.

This page covers what the harness is, how to run it, how to add your
own scenarios, and how the CI job is wired.

---

## What gets evaluated

The harness runs each scenario through one or more of:

- **The agent loop** — `orchestration.agent_loop.run_agent_loop()`. A
  scenario with a `loop` object is run with the model scripted and tool
  execution stubbed, so it needs no API key and no network. The
  expectations are the tools that actually ran
  (`expected_executed`), the tool that paused for confirmation, if any
  (`expected_paused_on`), and the tools the gate refused
  (`expected_refused`). This is the path every live chat message takes.

Each scenario can opt into one or more checks.

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
    "id": "weather_single_loop",
    "message": "What is the weather in Nairobi?",
    "loop": {
      "llm_script": [
        {"tool_calls": [{"name": "get_weather", "input": {"city": "Nairobi"}}]},
        {"text": "It is sunny."}
      ]
    },
    "expected_executed": ["get_weather"],
    "expected_paused_on": null,
    "expected_refused": [],
    "pack": "orchestration"
  }
]
```

### Fields

| Field | Type | Meaning |
|---|---|---|
| `id` | string | Stable identifier for the scenario. Shown in pass/fail output. |
| `message` | string | The user message under test. |
| `history` | string | Optional. Conversation history string. |
| `preferences` | object | Optional. User preferences (`date_order`, `time_format`, `tone`, etc.). |
| `requires_llm` | bool | If `true`, the scenario is **skipped** unless `--allow-llm` is set. |
| `loop` | object | If set, runs the scenario through `run_agent_loop` with a scripted model and stubbed tools. See below. |
| `expected_injection` | bool | If set, runs `is_prompt_injection(message)` and asserts the result. |
| `expected_blocked` | bool | If set, runs `should_block_message(message)` and asserts the result. |
| `expected_executed` | array of strings | Loop only. Asserts the tools that ran, in order. |
| `expected_paused_on` | string or `null` | Loop only. Asserts the tool that paused for confirmation (`null` when the turn completed). |
| `expected_refused` | array of strings | Loop only. Asserts the tools the gate refused (out of persona scope, or a denied shell tier). |
| `pack` | string | Capability pack this scenario belongs to (`orchestration`, `injection`, `shell`, …). |
| `expected_shell_tier` | string | If set, treats `message` as a command and asserts `classify_command` returns this tier (`safe`/`bounded`/`destructive`/`denied`). Deterministic — no LLM. |
| `profile` | string | Optional isolation profile for `expected_shell_tier` (default `standard`). |

### Loop scenarios

A `loop` object scripts the model's turns and stubs tool execution, so the
scenario exercises the same think → act → observe loop a live message takes
without calling a provider or touching the network:

```json
{
  "id": "shell_network_standard_pauses_loop",
  "pack": "shell",
  "message": "Fetch the example page.",
  "loop": {
    "preferences": {"shell_profile": "standard"},
    "llm_script": [
      {"tool_calls": [{"name": "run_command",
                       "input": {"command": "curl https://example.com"}}]}
    ]
  },
  "expected_executed": [],
  "expected_paused_on": "run_command",
  "expected_refused": []
}
```

| Loop field | Type | Meaning |
|---|---|---|
| `llm_script` | array | The model's turns, in order. Each turn is `{"text": …, "tool_calls": [{"name", "input"}]}`; the last turn has no tool calls. |
| `preferences` | object | Optional preferences passed to the loop (e.g. `shell_profile`, `approval_overrides`). |
| `tainted` | bool | Optional. Starts the run with untrusted text already in the room. |
| `persona_scope` | array of strings | Optional. Activates a persona whose tool scope is this list; out-of-scope calls are refused. |
| `tool_results` | object | Optional. Per tool name, the result the stub returns (default `{"status": "success"}`). Use it to carry instruction-like text in a result. |

### Adding scenarios

1. Capture a real-or-realistic user message you want the agent loop to
   handle.
2. Decide what's worth pinning: the tools that ran
   (`expected_executed`), the tool that paused (`expected_paused_on`),
   or the tools the gate refused (`expected_refused`).
3. Add the entry to `golden_scenarios.json`.
4. Run `python Backend/manage.py run_golden_eval --limit 1`
   in isolation and confirm it passes before committing.
5. PRs that change the agent loop or a gate should justify any
   scenario regressions in the description.

## Scenario packs

Every capability ships a **pack** of scenarios, and every pack must meet a
minimum size (`SCENARIO_PACK_MIN_SIZE`, default `3`) so coverage is explicit
rather than an ever-growing flat list. `test_scenario_packs` fails CI when a
pack is under-sized.

Current packs:

| Pack | What it covers |
|---|---|
| `orchestration` | The agent loop and the ad-hoc workflow handoff. |
| `injection` | Prompt-injection detection / blocking. |
| `shell` | The governed-shell classifier (`run_command` tiers). |

Run a single pack:

```bash
python Backend/manage.py run_golden_eval --pack shell
```

The run prints a per-pack summary (`pack: passed=… total=…`) so a regression is
attributable to a capability, not just a scenario count.

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
unintended agent-loop regression would show up as a failure. Flip
`continue-on-error` to `false` once the coverage justifies it.

### Nightly full pack

`.github/workflows/nightly-eval.yml` runs the **full pack** every night at
03:00 UTC (and on demand via `workflow_dispatch`). It runs the deterministic
scenarios always, and adds the LLM-backed scenarios only when
`ANTHROPIC_API_KEY` is configured as a repo secret — adding that secret is the
explicit provider-spend approval. Without it, no provider is called.

## See also

- [`Backend/orchestration/eval/README.md`](https://github.com/bedah-kym/kazi-core/blob/main/Backend/orchestration/eval/README.md) — the in-tree note that
  pre-dates this doc; will get a one-line pointer here in a future
  cleanup.
- [`docs/architecture.md`](architecture.md) — where the agent loop and
  the ad-hoc workflow handoff sit in the runtime.
