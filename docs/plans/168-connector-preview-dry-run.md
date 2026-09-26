# Plan: #168 Connector preview() dry run for approval cards

Status: approved by maintainer on 2026-09-26

## Goal

Approval cards include predicted effects computed by an optional connector
`preview()` method, instead of showing only raw parameters.

## Non-goals

- No new DB columns or migrations — effects ride in the existing
  `WorkflowApprovalRecord.metadata` JSON field.
- No enforcement: connectors without `preview()` keep working; the payload
  just has no `effects` key value.
- No preview wiring for internal contact/memory tools (they have no
  connector); those cards stay as-is.
- No frontend/card renderer changes (Mathia UI is held back from this repo);
  the payload change is additive.
- `preview()` never commits anything — it is a read-only description of
  effects.

## Touches

- Paths:
  - `Backend/orchestration/base_connector.py` (protected)
  - `Backend/orchestration/tool_executor.py` (protected)
  - `Backend/orchestration/agent_loop.py` (protected)
  - `docs/contracts/connector-execution.md` (protected)
  - `Backend/workflows/temporal_integration.py` + `Backend/workflows/views.py`
    (not on the protected list; workflows area — flagged)
  - `Backend/orchestration/connectors/mailgun_connector.py` (sample preview)
  - Tests: new `Backend/orchestration/test_connector_preview.py`, one new
    scenario in `Backend/orchestration/test_agentic_scenarios.py`, serializer
    additions in `Backend/workflows/tests.py`
- Protected paths? yes: `base_connector.py`, `tool_executor.py`,
  `agent_loop.py`, `docs/contracts/connector-execution.md`
- Contracts, migrations, approvals, payments, secrets? Contract doc minor
  bump v1.1 → v1.2 (additive section only). No migrations. Writes approval
  record metadata (append of one key). No payments, no secrets.

## Risk class of any new action

read-only — `preview()` is a dry run that formats parameter effects for
display and must not commit anything. Fail-closed: any preview error yields
no effects and never blocks or delays the approval flow.

## Approach

1. `BaseConnector.preview(parameters, context)` — optional async method,
   default implementation returns `None`. Contract: returns
   `{"effects": [str, ...]}` (human-readable one-liners) or `None`.
2. `tool_executor.preview_tool(tool_name, tool_input, context)` — resolve
   alias, sanitize parameters, call `connector.preview()` only when the
   connector defines it; any exception → `None`. Never executes.
3. Agent loop: at the confirmation pause, compute effects once and add them
   to (a) the `confirmation` AgentEvent payload and (b) the durable record's
   metadata, so the card can re-render after reload.
4. Temporal workflow-step approvals: same computation merged into the
   approval record metadata at `create_approval_record` time.
5. `workflows/views._serialize_approval` exposes `"effects"` from metadata
   (additive key; null when absent).
6. `MailgunConnector.send_email` gets a sample `preview()` describing the
   outbound email without sending.
7. Contract doc v1.2 documents the optional method and the effects shape.

## Verification (executable)

- `python Backend/manage.py test --noinput` → Ran 595 tests, OK (skipped=1,
  expected failures=3). Notifications tests are flaky on main too (verified
  on a clean tree; unrelated to this change).
- `python Backend/manage.py test orchestration workflows` → 361 tests OK.
- `python Backend/manage.py run_golden_eval` → 21 passed, 4 skipped
  (LLM-only), 0 failed.
- `flake8 Backend --count --statistics --select=E,W,F --ignore=E501,E402,W503 --max-line-length=127 --max-complexity=10` → 0.
- `bandit -r Backend --skip B101,B110` → No issues identified.
- `python scripts/check_boundaries.py` → exit 0.
- `python Backend/manage.py check` → no issues.
- Golden-style scenario: `Scenario2bConfirmationEffectsTest` asserts the
  confirmation event payload carries `effects` and that a connector without
  preview yields `effects: null`.
- Failure paths: `preview_tool` fail-closed tests (raising preview, wrong
  shape, unknown tool, no connector).

## Rollback

Revert the commit. No schema changes, so no data cleanup; connectors without
`preview()` are unaffected by definition.

## Open questions for the human

- Effects item shape: list of plain strings (my choice — simplest to render
  line-by-line on the card) vs list of `{"description": ...}` dicts?
- OK to wire the Temporal workflow-step approval path
  (`workflows/temporal_integration.py`)?
