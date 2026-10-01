# Plan: #155 Workflow definition versioning (W-A)

Status: approved by maintainer (build v0.7 session, 2026-10-01)

## Goal

`UserWorkflow.definition` becomes a versioned artifact: every definition
change creates an immutable `WorkflowVersion` row, executions bind the
version they started with, and the ops UI can list versions and diff two.

## Non-goals

- No reviewer/improvement acceptance path here (W-D, #158/#169) — this issue
  only supplies the version seam those call.
- No mutations to in-flight Temporal runs: they already receive a definition
  snapshot as a start argument; this issue records which version it was.
- No deletion of versions (append-only).

## Touches

- Paths: `Backend/workflows/models.py`, `versioning.py` (new),
  `signals.py` (new), `apps.py`, `temporal_integration.py`, `workflow_agent.py`,
  `ui_views.py`, `ui_urls.py`, templates, tests.
- Protected paths? yes — `Backend/workflows/migrations/0007_...` (additive).
- Contracts, migrations, approvals, payments, secrets? Additive migration:
  two integer fields (default 1) + one new model + data backfill to v1.
  No approval/payment/secret changes.

## Risk class of any new action

read-only: version rows are records, not side effects. Schedule refresh is
best-effort and reuses the existing Temporal schedule seam.

## Approach

1. `WorkflowVersion(workflow, version, definition, change_summary,
   created_by, created_at)`, unique `(workflow, version)`; append-only.
2. `UserWorkflow.definition_version` (default 1); signal creates v1 on
   creation; data migration backfills v1 for existing rows.
3. `WorkflowExecution.definition_version` snapshot; `start_workflow_execution`
   and the `create_execution_record` activity record it; the run argument list
   gains the version so scheduled executions bind correctly.
4. `versioning.create_workflow_version()` is the only write path for a live
   definition change; it snapshots vN+1, bumps the pointer, and (async
   helper) refreshes schedule triggers to the new version.
5. Ops UI: version list + unified JSON diff (`difflib`), linked from the
   automations list.

## Verification (executable)

- `python Backend/manage.py test workflows.test_versioning`
- Full suite + flake8 + bandit + `scripts/check_boundaries.py` in PR.
- Failure paths covered: unknown version diff returns None; execution started
  under v1 keeps v1 after v2 lands; v1 rows stay readable.
- Injection corpus cases added: n/a (no untrusted-input path added).

## Rollback

Revert the migration and the code commit. Version rows are additive; nothing
destructive is performed. Existing workflows keep a valid v1 row either way.

## Open questions for the human

None.
