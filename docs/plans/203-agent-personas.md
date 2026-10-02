# Plan: #203 Agent personas (named specialists, deterministic bounds)

Status: approved by maintainer (build v0.7 session, 2026-10-02)

## Goal

A durable teammate has a name, a job, a tool scope, a risk ceiling, and an
approval boundary — enforced deterministically in the tool path, never by
prompt. An out-of-scope call is denied; above the ceiling it escalates
through the existing pause gate. A persona never exceeds its human's access.

## Non-goals

- No machine identity / separate credentials.
- No multi-agent framework: personas are room-bound single-owner records.
- No new execution path; the existing agent loop buckets tools as today.

## Touches

- Paths: `Backend/workflows/models.py` (+ migration), `Backend/orchestration/
  personas.py` (new), `Backend/orchestration/agent_loop.py` (protected),
  `Backend/workflows/workflow_agent.py`, `promotion.py`, tests.
- Protected paths? yes — `agent_loop.py` (bucket integration) and the migration.
- Contracts, migrations, approvals, payments, secrets? One additive model
  (Persona) + nullable `WorkflowDraft.owner_persona`. No approvals/payments.

## Risk class of any new action

read-only enforcement: personas only narrow what the loop may do; a missing or
failed persona lookup means "no persona = today's behavior".

## Approach

1. `Persona` model (draft/active/archived, tool_scope, risk_ceiling,
   approval_boundary, owner user, optional room binding, duplicate lineage).
2. `orchestration/personas.py`: resolution (owner+room+active, fail closed),
   lifecycle helpers, and `apply_persona_bounds(risk_info, persona, action)`.
3. `_bucket_tool_calls` gains `persona`/`tainted` params: scope-denied calls
   join the denied bucket with a reason; above-ceiling and boundary actions
   force the existing confirmation pause (ask-first wins over "auto").
4. Chat/draft paths stamp `owner_persona` when the room resolves to a persona.

## Verification (executable)

- `python Backend/manage.py test orchestration.test_personas workflows.test_personas`
- Full suite + flake8 + bandit + boundaries in the PR.
- Failure paths covered: out-of-scope denied; above-ceiling pauses; another
  user's persona is invisible; inactive persona = no persona; duplication
  copies no history.

## Rollback

Revert migration + code; deleting persona rows restores today's behavior.

## Open questions for the human

None.
