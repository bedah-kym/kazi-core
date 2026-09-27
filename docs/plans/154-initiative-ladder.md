# Plan: #154 Initiative ladder (digest -> propose -> promote)

Status: draft — awaiting human approval (scope)

## Goal

Kazi climbs the initiative ladder one rung at a time, each grant coming from a
human through the existing durable approval seam:

1. watch silently (existing),
2. daily digest (rung 2),
3. propose a specific action with a diff/preview (rung 3),
4. auto-execute under an approved rule, with a receipt every time (rung 4).

## Non-goals / scope notes

- No new DB model/migration: approved rules live in
  `profile.notification_preferences["approved_rules"]` (JSON), like #152.
- No new pipeline stage; rung-3 proposals reuse the agent-loop durable
  confirmation path (`save_pending_confirmation`), not a bespoke flow.
- LLM rule-drafting is optional and mocked in tests.

## Touches

- `Backend/orchestration/initiative.py` (new): `build_digest`,
  `open_proposals`, `propose_action`, `approved_rules` / `promote_rule`,
  `is_allowed_by_rule`, `proactive_budget_remaining` / `consume_proactive_budget`,
  `execute_approved_rule` (guarded dispatch + receipt), `draft_rule` (LLM, mocked).
- `Backend/orchestration/tasks.py`: `send_daily_digest` task.
- `settings.py`: `PROACTIVE_BUDGET_PER_DAY` (default 2) + a morning beat entry.
- `Backend/orchestration/eval/golden_scenarios.json`: digest/proposal injection
  cases (injection pack).
- Tests: `test_initiative.py`.
- Docs: `docs/configuration.md`.
- Protected paths? none.

## Hard caps

- Proactive budget per day, default small, stored per user/day in cache.
- Proactive actions never exceed the safe tier unless an approved rule exists.
- Digest/proposal text is passed through `_sanitize_tool_result` before reaching
  the LLM.

## Verification (executable)

- Rung 2: digest lists fired watches + open proposals; empty digest degrades.
- Rung 3: `propose_action` writes a durable pending confirmation (mocked).
- Rung 4: no rule -> no auto-action; approved rule -> dispatch once, consume
  budget, receipt written; budget exhausted -> refused.
- Injection corpus gains digest/proposal cases and still meets pack min size.
- Full suite + flake8 + bandit + `check_boundaries.py`.

## Rollback

Revert; delete the task/beat entry. Rules are plain JSON and can be cleared.

## Open questions for the human

- Confirm rung-4 execution scope: dispatch the approved action through
  `execute_tool` and write a receipt (this PR), or keep rung 4 to
  storage/gating/budget only and wire dispatch later?
- Confirm the digest is delivered via `NotificationService` (in-app + the
  user's channels) on a morning beat.
