# Plan: #133 Two-tier escalation (inline confirm + durable approval)

Status: draft — awaiting human approval (protected paths)

## Goal

The shell risk gate's tier drives the confirmation path: `safe` auto-executes,
`bounded` takes a lightweight chat-scoped **inline confirm**, `destructive`
keeps the durable `WorkflowApprovalRecord` path, and `denied` is refused.

## Non-goals

- No network toggle / host allowlist (#134): `bounded` still cannot actually
  egress in `standard`; that lands next.
- No workspace snapshot/rollback (#135).
- No new UI: the client renders inline vs durable from the payload it already
  receives; server-side the two differ in whether a durable record is written.

## Touches

- Protected: `Backend/orchestration/agent_loop.py` (confirm path),
  `Backend/orchestration/tool_executor.py` (risk gate).
- `Backend/orchestration/shell/classifier.py` (already returns the tier).
- `Backend/chatbot/` consumer (only if resuming a bounded pause needs it —
  inspect first).
- Tests: `test_shell_escalation.py`, extend `test_agentic_scenarios.py`.
- Protected paths? yes: `agent_loop.py`, `tool_executor.py`.

## Approach

1. `get_tool_risk_info("run_command", prefs, input)` returns
   `requires_confirmation = tier != "safe"` (Phase 2 un-gates the safe tier) and
   `shell_tier`. Non-shell tools are unchanged.
2. Agent loop buckets calls: `safe_calls` (auto), `inline_calls` (`bounded`),
   `durable_calls` (`destructive`), and refuses `denied` with an immediate error
   tool_result.
3. The confirmation pause gains a `tier`/`confirmation_kind` field. `bounded`
   uses the existing loop-state pause without writing a durable record;
   `destructive` keeps `save_pending_confirmation` (DB).
4. Confirm handler routes on the tier; existing high-risk actions keep the
   durable path unchanged.

## Verification (executable)

- `test_shell_escalation`: safe→auto, bounded→inline (no DB record),
  destructive→durable (DB record), denied→refused.
- Existing high-risk action (`send_email`) still durable.
- Full suite + flake8 + bandit + `check_boundaries.py`.

## Rollback

Revert; the gate returns to the Phase 1 "everything durable" behavior.

## Open questions for the human

- Server-side, is a **bounded** pause allowed to skip the durable record (Redis
  loop-state only, chat-scoped), with the durable record reserved for
  **destructive**? That matches roadmap §4.4. Confirm before I touch the loop.
- If the chat consumer currently only resumes from a durable record, may I
  extend it in this PR (it is not on the protected list, but it is the runtime
  seam)?
