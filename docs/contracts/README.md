# Contracts

This directory documents the **stable runtime contracts** Kazi Core
exposes — the shapes that:

- Connector authors must produce or consume.
- Operator tools rely on for pause/resume/replay decisions.
- Future versions of Kazi promise to keep working without breaking
  changes inside a minor version.

Treat each contract as a small API. If you change one, you bump its
**`Version`** and add an entry under **`Breaking changes`**.

## Index

| Contract | What it shapes | Version | Status |
|---|---|---|---|
| [Connector execution](connector-execution.md) | What a connector's `execute()` must return | 1.0 | stable |
| [Tool schema](tool-schema.md) | What a connector's `get_action_catalog_entries()` must return | 1.0 | stable |
| [Approval](approval.md) | What `WorkflowExecution.pending_approval` looks like and how operators answer it | 1.0 | stable |
| [Execution detail](execution-detail.md) | What `GET /api/workflows/executions/<id>/` returns | 1.0 | stable |
| [Replay safety](replay-safety.md) | Per-step `replay_safe` declaration + computed `replay_hints` | 1.0 | stable |

Two more contracts are queued for v0.5 (telemetry events, memory
updates). They are deliberately out of scope for v0.4 because the
underlying code is still settling.

## Versioning policy

- Each contract carries a `Major.Minor` version in its file header.
- **Patch-level changes** (clarifications, typo fixes, new optional
  fields) — bump the doc, no version change.
- **Minor-level changes** (new optional fields, expanded enums) — bump
  Minor, document under `Changes since`.
- **Major-level changes** (renamed fields, removed fields, narrowed
  enums) — bump Major, document under `Breaking changes`, and ship a
  one-cycle deprecation shim per the v0.4 brief's non-goal "no breaking
  API rewrites."

A contract change is part of the PR that changes the runtime — never a
separate doc-only PR. The runtime and the doc move together.

## How a contract becomes "enforced"

Three tiers, in increasing order of strictness:

1. **Documented only** (default for new contracts) — the shape is in
   this directory, code respects it informally.
2. **Validated on writes** — a `validate_*` helper in
   `Backend/orchestration/contracts.py` checks shape at the point of
   production (e.g., when a connector registers its catalog entries).
   Violations log a warning, don't break boot.
3. **Validated on reads** — the same helper guards consumer code
   paths. Violations either fail fast or are explicitly rejected
   (e.g., a malformed approval request returns 400).

v0.4 ships every contract at tier 1 plus the **tool schema contract at
tier 2** — `validate_catalog_entry()` in
`Backend/orchestration/contracts.py` is wired into
`connector_registry.discover_connectors()`. Other contracts move up
the tiers as the runtime stabilizes.

## See also

- [`Backend/orchestration/contracts.py`](../../Backend/orchestration/contracts.py)
  — the in-tree builders + validators that back this directory.
- [`docs/v0.4-brief.md`](../v0.4-brief.md) — why this exists (the
  "stable contracts" milestone, M2-2).
