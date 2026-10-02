# Plan: #159 Standing approvals (workflow-runtime scope)

Status: approved by maintainer (build v0.7 session, 2026-10-01)

## Goal

One durable grant per rule: a human's "Always allow" on a workflow approval
card becomes a grant scoped to `(workflow, definition version, trigger,
capability)` that auto-approves only that slice — never a global unlock — and
lapses on out-of-scope attempts, failure spikes, or age. Ask-first always wins.

## Non-goals

- Agent-loop (chat/shell) standing grants. This cycle the shell stays
  human-gated; the agent-loop seam is untouched (protected core).
- Granting *new* capabilities: grants only cover what the human already
  approved once. A version change or widening delta always re-gates.
- Auto-created grants: nothing self-authorizes; grants are created from a
  human decision on a real approval card.

## Touches

- Paths: `Backend/workflows/models.py`, `grants.py` (new), `temporal_integration.py`,
  `runtime.py`, `tasks.py`, `ui_views.py`/`ui_urls.py` + template, tests.
- Protected paths? yes — `Backend/workflows/migrations/0011_...` (additive).
- Contracts, migrations, approvals, payments, secrets? One new model
  (StandingGrant), additive migration; approval semantics extend
  `WorkflowApprovalRecord` metadata (no new parallel approval system).

## Risk class of any new action

irreversible-in-scope: a matching grant auto-approves a previously approved
workflow step and writes a receipt per run. Bounded by version + trigger +
capability scope; lapse conditions re-gate. Deny grants fail steps closed.

## Approach

1. `StandingGrant` model: user, workflow, `workflow_version`, trigger scope
   (trigger type/id), capability scope (list), decision
   (`allow_once` / `always_allow` / `deny`), status
   (`active` / `lapsed` / `revoked`), lapse reason + `expires_at`.
2. Runtime: in `DynamicUserWorkflow.run`, before pausing a step for approval,
   consult `grants.match_grant`. Match = same workflow+version, trigger in
   scope, step capability in scope, active. `always_allow` auto-approves with
   a receipt row; `allow_once` auto-approves once then lapses; `deny` fails
   the step closed. Ask-first wins: any step still pausing keeps pausing.
   Out-of-scope capability under a matching trigger lapses the grant.
3. Grant creation from the approval card (UI + view helper) after
   `routine_ready_for_grant` passes and `approval_kind_for_delta` allows
   non-escalation; receipts per autonomous run.
4. Sweep task lapses expired grants; ops UI lists active grants + lapse
   reasons.

## Verification (executable)

- `python Backend/manage.py test workflows.test_grants`
- Full suite + flake8 + bandit + boundaries in the PR.
- Failure paths covered: out-of-scope capability re-gates and lapses; new
  version re-gates; ask-first wins over allow; deny fails closed; receipts
  written per autonomous run.
- Injection corpus cases added: n/a (no new untrusted-input path).

## Rollback

Revert migration + code. Grants are additive rows; deleting them returns the
workflow to prompt-on-every-step behavior.

## Open questions for the human

None (agent-loop grants deferred to a future cycle per agreement).
