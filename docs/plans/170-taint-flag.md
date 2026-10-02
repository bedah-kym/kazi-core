# Plan: #170 Untrusted-context taint flag

Status: approved by maintainer (build v0.7 session, 2026-10-02)

## Goal

Once output from an untrusted source (shell, web search) enters the agent's
context, the run is tainted. In a tainted run, external-write and
credential-scoped actions require a durable approval one tier up — a stored
"permission once" (auto) rule cannot bypass the gate.

## Non-goals

- No egress/firewall changes; the taint flag is a risk-tier escalator only.
- Inbound-room-message tainting (which messages are authored by whom) is not
  implemented here; the flag covers tool-output vectors.

## Touches

- Paths: `Backend/orchestration/agent_loop.py` (protected), `agent_loop`
  tests, `golden_scenarios.json` corpus additions.
- Protected paths? yes — `agent_loop.py`.
- Contracts, migrations, approvals, payments, secrets? None.

## Risk class of any new action

read-only escalation: taint can only add friction (force a pause), never
remove it. Denied/unmatched paths stay exactly as today.

## Approach

1. `LoopState.tainted` (persisted in save/load_loop_state).
2. Mark tainted when a shell tool result is logged or web search is used.
3. `_bucket_tool_calls` gains `tainted`: for catalog
   `requires_confirmation`/high-risk actions, a tainted run routes the call to
   the durable pause path even when an `auto` override exists.

## Verification (executable)

- `python Backend/manage.py test orchestration.test_taint`
- A test showing a stored `auto` override does NOT bypass the gate in a
  tainted run, and still does in an untainted run (regression).
- Injection corpus: new shell-output-injection scenarios.

## Rollback

Revert the commit; taint defaults False and no gate applies.

## Open questions for the human

None.
