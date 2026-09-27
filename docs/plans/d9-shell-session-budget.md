# Plan: D9 — raise the shell-session tool-call cap

Status: draft — awaiting human OK (protected path); human approved in chat

## Goal

A session that actually uses the shell gets a modestly higher tool-call cap
(15 -> 25) so the agent can chain a real investigation in one request. The hard
backstop is unchanged.

## Non-goals

- No change to iteration/token/time caps.
- No change when the install-level caps toggle is off (hard backstop applies).
- No sub-agent cap change.

## Touches

- Protected: `Backend/orchestration/agent_loop.py` — `_session_tool_call_cap`
  used at the per-iteration budget check; new `SHELL_SESSION_MAX_TOOL_CALLS`.
- New test `Backend/orchestration/test_shell_session_budget.py`.

## Approach

1. `_session_tool_call_cap(state, caps_enforced)`:
   - not enforced -> `HARD_CAP_TOOL_CALLS` (unchanged);
   - enforced, no shell in `state.tool_call_log` -> `MAX_TOOL_CALLS` (15);
   - enforced, shell used -> `SHELL_SESSION_MAX_TOOL_CALLS` (25).
2. Replace the one-shot `tool_call_cap` with the per-iteration recompute so the
   cap rises once the session touches the shell.

## Verification (executable)

- `test_shell_session_budget`: 15 without shell, 25 once `run_command` is in the
  log, hard cap when caps are off.
- Full suite + flake8 + bandit + `check_boundaries.py`.

## Rollback

Revert; the cap returns to a flat 15.

## Open questions for the human

- 25 is the roadmap's example; confirm (or give a number).
