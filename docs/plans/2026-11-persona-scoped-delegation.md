# Plan: Persona-scoped delegation (post-v0.8)

Status: draft for maintainer approval

Builds on #203 (personas) and #137 (handoffs). Scheduled after v0.8 (epic
#227), except Phase 0, which may land alongside v0.8 (see §Working beside v0.8).

## Goal

A delegated sub-run can work as one of its owner's personas, under that
persona's bounds and never above the room's, and every such run is visible
and recorded. The value is least privilege per sub-task plus independent
evidence from different tools (web, shell, notes), not different personalities.

## Why this shape

- Debate between agents built on one model roughly ties majority voting at
  equal compute and costs 2–3.4× more tokens (arXiv 2605.09618, 2605.00914,
  2310.01798, 2503.16814). Evidence on mixed-model teams conflicts (2602.03794
  against 2609.35875). These are search-result summaries, mostly preprints.
- A lead agent with parallel sub-agents on independent sub-tasks helps for
  breadth-heavy work, and token spend explains most of the gain (Anthropic's
  multi-agent research write-up). Kazi's turn budget already bounds that spend.
- Most multi-agent failures come from system design and missing verification
  (MAST, arXiv 2503.13657). Kazi's answer is deterministic bounds plus the
  v0.8 verifier, not agreement between personas.

## Non-goals (dropped on purpose)

- No debate rounds, brainstorm rooms or persona-to-persona chat.
- No model per persona. Revisit only if the Phase 4 eval shows a gain.
- No N-sample voting in core: the verifier checks state postconditions and
  cannot rank reasoning answers.
- No Critic persona: the verifier is harness code, and the reviewer (#158)
  already requires a distinct model.
- No side chats the model decides to start.
- No team rooms here: F-84 (frontend redesign proposal) is separate and
  depends on Phases 1 and 3.

## What the code does today

- `delegate_task` takes `task` and a model-written `tools` filter; no persona
  (`agent_loop.py`, `META_TOOL_DEFINITIONS`).
- Calls in one batch run in parallel and split one budget share:
  50% of what is left, at least 5,000 work tokens each (`_batch_delegated_grants`).
- Sub-agent results are tainted (`_UNTRUSTED_TOOL_NAMES`) and the model sees
  only the summary; delegated calls are stored as `TurnToolCall` rows with
  `provenance="delegated"`, `parent_call_id` and a `receipt` link.
- `create_handoff` / `run_handoff` (`workflows/handoffs.py`) have no caller
  outside tests. `Handoff` has no turn link; `room_id` is a plain integer.

---

## Phase 0 — Fix two handoff bugs (may land with v0.8)

**Bug 0a: a handoff runs under the room's persona, not its target.**
`run_handoff` sets `room_id` to the handoff's room and `_run_sub_agent` looks
the persona up by `(room, user)`. The target persona's risk ceiling, approval
boundary, identity and skills are never applied; only its tool scope reaches
the run, as the tool list.

**Bug 0b: sub-agent tool scope is checked after execution.**
`_execute_scoped_tool_calls` receives no allowlist. A tool call outside the
offered tools runs if the room persona (or no persona) allows it, and
`run_handoff` marks the outcome `denied` only after the run. The same gap
applies to `delegate_task`'s `tools` filter. AGENTS.md §0.3 requires fail
closed.

**Touches:** `Backend/orchestration/agent_loop.py` (protected:
`_run_sub_agent`, `_execute_scoped_tool_calls`), `Backend/workflows/handoffs.py`,
`Backend/orchestration/personas.py`, tests.

**Risk class:** read-only enforcement. Both fixes only narrow what may run.

**Approach:**
1. `personas.combine_bounds(room_bounds, target_bounds)`: scope is the
   intersection (an empty scope means "no limit" only when both are empty),
   ceiling is the lower rank, boundary is the union. Pure function.
2. The harness passes the target persona to `_run_sub_agent` through a
   harness-only context key that tool input cannot set. The sub-run applies
   `combine_bounds(room, target)`. A failed target lookup removes tool reach,
   like `PERSONA_LOOKUP_FAILED`.
3. `_execute_scoped_tool_calls` takes the offered tool names and denies any
   other call before execution, recorded as `denied`.
4. Check whether a sub-agent can see `delegate_task` in its own tool list;
   if so, remove the meta-tools from it.

**Verification:**
- New tests in `orchestration/test_sub_agent_parity.py` and
  `workflows/test_handoffs.py`, each shown failing on `main` first:
  - a handoff to a persona whose approval boundary lists an action the room
    persona allows refuses that action (today it runs);
  - the same for an action above the target's risk ceiling that the room's
    bounds let run without asking;
  - a scripted sub-agent that emits a tool outside its offered list never
    reaches `_execute_with_timeout` (today it does);
  - a failed target lookup makes no tool call.
- Old cases still blocked: the existing persona-scope and taint tests pass unchanged.
- One golden loop scenario in the `injection` pack: a web result tells the
  sub-agent to call `run_command`, and the call is denied before execution.
- CI's blocking commands from `docs/golden-principles/tests-and-evals.md`.

**Rollback:** revert the PR; no data changes.

---

## Phase 1 — A persona on `delegate_task` (after T-B2 #221)

**Touches:** `agent_loop.py` (protected: the `delegate_task` schema and the
meta-tool path), `personas.py`, `workflows/handoffs.py`, `docs/architecture/model.js`,
tests.

**Risk class:** read-only enforcement plus a new record. It adds no reach.

**Approach:**
1. `delegate_task` gains an optional `persona` (a name). The harness resolves
   it among the requesting user's active personas. Unknown, inactive or
   foreign means a refusal, never a silent fallback to no persona.
2. Effective tool scope = the target's scope ∩ the room persona's scope ∩ the
   model's `tools` filter; ceiling and boundary as in Phase 0.
3. The sub-run's system prompt adds `persona_identity_prompt(target)` for
   that sub-task only.
4. Each persona-scoped delegation writes a `Handoff` row and the existing
   room note, so it is never hidden. Without `persona`, behaviour is as today.
5. Taint, budgets and sub-agents' inability to pause stay as they are.

**Verification:** tests for each rule in 1–4, shown failing first; a golden
loop scenario where the lead delegates to two personas in one batch and each
sub-run can call only its own scope; the architecture model check passes.

**Rollback:** revert. Handoff rows already written stay (append-only history).

---

## Phase 2 — Fan-out caps and measurement (with or after Phase 1)

**Touches:** `agent_loop.py` (protected: `_batch_delegated_grants`),
`Backend/Backend/settings.py`, telemetry, `docs/` for operators.

**Approach:**
1. `AGENT_MAX_PARALLEL_DELEGATIONS` (default 3). Calls past the cap get the
   existing honest "not enough budget" refusal (grant 0). The budget rules
   are unchanged.
2. Operator docs: on a single consumer GPU running a local model, set the cap
   to 1, since parallel sub-runs mostly queue there (unmeasured; to confirm).
3. Telemetry per turn: delegations, personas used, work tokens, wall-clock time.

**Verification:** a test that a batch of 5 delegations with cap 3 runs 3 and
refuses 2, shown failing first; the existing budget tests pass unchanged.

---

## Phase 3 — Handoff provenance (after the v0.8 Turn schema settles)

**Touches:** `Backend/workflows/models.py` + migration (protected).

**Approach:** a nullable `Handoff.turn` foreign key, set by Phase 1's path.
It answers "which turn asked for this?" for persona recall and is needed
before F-84. `WorkflowExecution` gets the same field only if a recall use
case needs it.

**Verification:** a migration test and a Phase 1 path test that sets it.

---

## Phase 4 — Live eval pack (later, needs contributors)

The golden loop runner scripts the model, so it tests gates, not answer
quality. A live pack would hold about 15 breadth tasks with recorded web and
shell results replayed, and compare one agent, fan-out on one model and
fan-out on mixed models at the same token budget. Score: facts found, false
claims, work tokens, wall-clock time. Model per persona is decided by this
pack, not before it.

---

## Working beside v0.8

- **Phase 0 can land now.** It touches only the sub-agent path in
  `agent_loop.py` and `workflows/handoffs.py`. v0.8's open items in that
  file are T-B2 (#221, completion gate) and T-B3 (#222, workflow handoff);
  neither changes `_run_sub_agent`. Keep it one small PR and rebase on the
  v0.8 branch before review. Phase 0 also helps T-B2: delegated calls can
  no longer act outside their scope.
- **Phases 1–2 wait for T-B2.** Fan-out multiplies writes, so the completion
  gate should already count delegated writes before more delegation is
  encouraged.
- **Phase 3 waits for the Turn schema** so migrations in `orchestration` and
  `workflows` don't race.
- The v0.8 lead may take Phase 0 into v0.8 as a hardening item, or leave all
  of this for v0.9.

## Protected paths needing human OK

- `Backend/orchestration/agent_loop.py` (Phases 0, 1, 2)
- `Backend/workflows/migrations/*` (Phase 3)

## Open questions for the human

1. Phase 0: a separate issue now, or folded into v0.8 as a hardening item?
2. Default for `AGENT_MAX_PARALLEL_DELEGATIONS`: 3, or 1 until Phase 2's
   telemetry gives real numbers?
3. Should Phase 1 ship with example scopes for Researcher (web), Operator
   (shell) and Archivist (notes) in the docs, or no examples?
