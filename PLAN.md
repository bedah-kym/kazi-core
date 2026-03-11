## LLM-First Orchestration v2 (Cost-Flat)

### Summary
Build an **LLM-first, adaptive orchestration loop** where every turn/step is interpreted by the LLM first, then constrained by deterministic safety/policy layers, with high-risk-only verification passes to keep spend flat.  
This keeps the “intelligent/codex-like” feel while preserving reliability and predictable cost.

### Key Implementation Changes
- **Execution contract (LLM-first adaptive)**
  - Make each step run through: `LLM propose -> deterministic safety gate -> execute or clarify`.
  - Keep deterministic short-circuits only for low-risk/no-ambiguity cases (your chosen “adaptive every step”).
  - Standardize step outcomes into one envelope: `status`, `action`, `params`, `missing_slots`, `risk_level`, `reason`, `next_step`.

- **Verification strategy (cost-flat)**
  - Use **single LLM pass** by default.
  - Trigger **second verifier pass only for high-risk actions** (payments, send actions, bookings, irreversible mutations).
  - Keep deterministic safe fallback when uncertain/failing: ask clarifying question, do not execute risky actions.

- **Single source of truth for action schema**
  - Consolidate supported actions/aliases/required params in one catalog consumed by parser, planner, router, and capability checks.
  - Remove drift bugs (example already present: `find_jobs` references in workflow executor while router no longer provides `UpworkConnector`).
  - Add startup integrity check that fails fast on unresolved action-to-connector mappings.

- **Guardrails and safety layer (deterministic)**
  - Keep/strengthen: prompt-injection filter, room access checks, capability gates, confirmation requirements, ownership checks, idempotency keys, and action receipts.
  - Enforce strict “no-execute on ambiguity” for high-risk actions.
  - Keep webhook/result handling deterministic and idempotent.

- **“Antigravity/Codex” UX without extra API spend**
  - Stream structured progress states per turn: `understanding -> planning -> validating -> executing -> done`.
  - Return concise rationale + next action when clarifying instead of generic errors.
  - Keep deterministic post-processing for stable formatting and low token usage.

- **Load/cost control**
  - Keep current token/rate atomic counters.
  - Add prompt+context hash caching for repeated clarification/verification prompts.
  - Use bounded async waits with proper completion signal; avoid high-frequency DB polling loops.

### Public Interfaces / Contract Changes
- **Unified orchestration result schema** for chat/workflow consumers:
  - `status`, `action`, `risk_level`, `requires_confirmation`, `clarification_prompt`, `data`, `receipt`.
- **Unified action catalog schema**:
  - `action`, `aliases`, `service`, `required_params`, `risk_level`, `confirmation_policy`, `capability_gate`.
- **Workflow step event schema** for streaming progress:
  - `step_id`, `phase`, `state`, `message`, `timestamp`.

### Test Plan
- **Routing integrity**
  - Every catalog action must map to a connector; boot-time test fails on missing mappings.
  - Regression for removed/renamed actions (specifically `find_jobs` path).
- **LLM-first behavior**
  - Ambiguous unique prompts should produce clarify/plan instead of wrong deterministic branch.
  - Low-risk clear prompts should execute with single pass.
- **Safety behavior**
  - High-risk actions require confirmation and/or verifier pass.
  - Prompt injection attempts are blocked for sensitive actions.
  - Ownership checks prevent cross-user invoice/status access.
- **Cost/load behavior**
  - Dual-pass only fires on high-risk actions.
  - Cache hits reduce repeated token usage.
  - Async wait path avoids tight DB polling and handles timeout cleanly.
- **UX behavior**
  - Streaming phases appear in order and terminate deterministically.
  - Receipt and undo hooks are present for auditable actions.

### Assumptions and Defaults
- No new external APIs/providers are introduced.
- Primary mode is **LLM-first adaptive** (your selected policy).
- Spend priority is **keep spend flat**: high-risk-only dual-pass.
- Failure default is deterministic safe fallback (clarify/deny risky execution).
- Existing advanced LLM providers stay; this plan changes orchestration logic, not provider footprint.
