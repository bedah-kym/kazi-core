# Plan: #171 Scenario packs + provider continuity test

Status: draft — awaiting human approval (CI touch)

## Goal

Eval coverage becomes a measured artifact: scenarios are grouped into capability
**packs** with a minimum size, the golden harness gains a deterministic **shell**
verifier, each provider adapter gets a multi-step **continuity fixture**, CI runs
a mocked smoke pack, and a **nightly** job runs the full pack.

## Non-goals

- No `--allow-llm` in CI (no provider spend without explicit human approval).
- No model-tier default changes in this PR — the issue says choose defaults
  *from* measured results; this PR produces the measurement (per-pack pass rates)
  and documents the selection process.
- No new heavy dependency; provider fixtures are mocked.

## Touches

- `Backend/orchestration/management/commands/run_golden_eval.py` — read a
  `pack` field, add `--pack` filter, add an `expected_shell_tier` verifier,
  print a per-pack summary, keep exit code non-zero on failure.
- `Backend/orchestration/eval/scenario_packs.py` (new) — pack discovery +
  minimum-size guard.
- `Backend/orchestration/eval/golden_scenarios.json` — tag existing scenarios
  with a `pack`, add a `shell` pack (>= 3 deterministic cases).
- `Backend/Backend/settings.py` — `SCENARIO_PACK_MIN_SIZE` (default `3`).
- `Backend/orchestration/test_scenario_packs.py` (new) — every pack meets the
  minimum, and the shell verifier resolves.
- `Backend/orchestration/test_provider_continuity.py` (new) — DeepSeek + Claude
  multi-step tool-call fixture: coherence within a turn, reset on a new user
  message (mocked HTTP; no network).
- `docs/eval.md` — packs, shell verifier, smoke vs full, nightly.
- `.github/workflows/nightly-eval.yml` (new, **protected**) — scheduled full run.
- Plan: this file.

## Protected paths / approvals

- `.github/workflows/*` (protected) — new nightly workflow. **Needs human OK.**

## Approach

1. `scenario_packs.py`: `load_scenarios()`, `group_by_pack()`,
   `verify_pack_min_size(scenarios, min_size)`, `select_pack(scenarios, name)`.
2. Runner: filter by `--pack`; for a scenario with `expected_shell_tier`, call
   `shell.classifier.classify_command(message, profile=scenario.get("profile",
   "standard"))` and assert the tier (deterministic, no LLM). Print
   `Pack: <name> pass/x total/y`.
3. Scenarios: add `pack` to each existing entry (`intent`, `planner`,
   `injection`); add `shell` cases: read-only (`safe`), network (`bounded`),
   destructive (`destructive`), root (`denied`).
4. Provider continuity: build a two-step tool turn (assistant tool_call -> tool
   result -> assistant final) and assert the adapter keeps the tool_call id
   paired with its result; then prepend a new user message and assert no stale
   tool state leaks across the turn boundary.
5. CI: the existing `main.yml` smoke step already runs without `--allow-llm`;
   add the nightly workflow that runs the full pack (still no LLM unless a
   secret is configured).

## Verification (executable)

- `python Backend/manage.py run_golden_eval` -> all deterministic packs pass;
  shell pack included.
- `python Backend/manage.py run_golden_eval --pack shell` -> shell-only.
- `python Backend/manage.py test orchestration.test_scenario_packs
  orchestration.test_provider_continuity` -> OK.
- Full suite + flake8 + bandit + `check_boundaries.py`.
- Failure paths: a pack below the minimum size fails the guard test; the shell
  verifier fails on a wrong tier.

## Rollback

Revert the commit; delete the nightly workflow. No schema, no runtime change.

## Open questions for the human

- OK to add `.github/workflows/nightly-eval.yml` (scheduled `run_golden_eval`
  full pack)? It is the "nightly job" acceptance item and touches CI.
