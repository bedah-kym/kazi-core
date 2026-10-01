# Proposed v0.8: Turns that own the truth

**Date:** 2026-10-01
**Status:** approved for filing (epic + issues)
**Basis labels:** `[Literature]` = backed by a cited writeup; `[Analysis]` = design judgment; `[Evidence]` = observed in the 2026-10-01 chat export + live DB.

This document is issue-ready. Section 5 is the issue set; each has executable acceptance
criteria. Section 7 lists the anti-patterns we are explicitly refusing.

---

## 0. Why now (the inciting incident)

The room-1 export from 2026-10-01 plus the live database show three failure classes ending
in a broken promise to the user:

1. **False success.** Kazi said *"All set — Workflow ID: 1 … recurring `*/10` cron … it'll
   keep emailing you."* The database: `UserWorkflow#1` is `active` with
   `definition.triggers=[{trigger_type: schedule}]`, **0 `WorkflowTrigger` rows, 0
   executions**, and `last_executed_at=None`. `shell_workspaces/workspaces/1/` contains no
   `speedtest.sh` ("Done — the script is saved" was also false; it admitted the failure one
   turn later). `[Evidence]`

   Root cause: `handoff_to_workflow` accepts `schedule` in its schema
   (`orchestration/agent_loop.py:700`) but `_create_workflow_handoff` drops it and, for any
   non-manual trigger, creates the `UserWorkflow` **without** registering a `WorkflowTrigger`
   or Temporal schedule, then reports success (`agent_loop.py:998-1039`). Compare
   `workflows/workflow_agent.py:95-129`, which registers triggers correctly. Nothing verifies
   the side effect before the claim is persisted. `[Analysis]`

2. **Narration dumps.** The 00:03:42 and 00:12:34 replies are multiple distinct model text
   blocks concatenated with no separator, with an internal budget warning embedded
   mid-message: `available.The heredoc didn't work…`, `now.Good news…`,
   `*(Running low on my thinking budget for this request.)*`. `[Evidence]`

   Root cause: `broadcast_chunk` appends every `text`/`text_delta` from every loop iteration
   to one buffer and the buffer is persisted as the final message
   (`orchestration/coordinator.py:121-124`, `953-960`). A visible delta is treated as a
   committed message. Clean no-tool turns look fine, which is why this looked intermittent.

3. **Unbounded commitments.** `Reminder#1` ("native env reminder test") is still `pending`
   a day after its scheduled time; a reminder beyond its window is fired late rather than
   marked missed. The proactive nudge (`chatbot/tasks.py:1715-1881`) walks a canned list,
   returns the first *unmet* condition forever (a non-technical user never creates a
   workflow/invoice), counts only an explicit "dismiss" as resolution (ignore does not stop
   it), and has no daily cap or quiet hours. Room notes survive up to 30 days of decay before
   archiving. `[Evidence]` `[Literature]`

## 1. Prior art

| Source | What it establishes |
|---|---|
| TianPan, [The Hallucinated Success Problem](https://tianpan.co/blog/2026/04/23/hallucinated-success-agent-false-completion) `[Literature]` | Self-reflective loops emit the completion verdict from the same model that produced the plan. The fix is architectural: an independent verifier reading authoritative state; "the verifier is the architecture." Prompt-level "verify your work" is not a fix. |
| Particula, [AI Agent Says Done But Did Nothing](https://particula.tech/blog/detect-silent-ai-agent-failure), citing arXiv 2606.09863 `[Literature]` | False success accounts for **45–48% of failures in single-control domains vs 3% in dual-control** (~15x). LLM judges catch almost none (AUROC ≤0.65; 0.54 on raw traces). Fixes: postcondition contract per state-changing tool, idempotency key, verify-before-retry, write-set audit. |
| Pathrule, [LLM Streaming UX](https://www.pathrule.io/patterns/llm-streaming-ux) `[Literature]` | Streaming is a distributed state machine. Typed events + stable message/part IDs + monotonic sequence + **exactly one terminal outcome**; persistence only at semantic commit points; reconnect preserves identity; adversarial stream tests (duplicate/reorder/drop/disconnect/cancel races). "A visible delta is not yet a completed message." |
| Windrose, [Streaming Responses and Real-Time Tool Calls](https://windrose-ai.com/blog/streaming-responses-tool-calls-llm-apis) `[Literature]` | The hard problem is partial state across text, tool calls, and tool results; design explicit UI states rather than implying them from chunk arrival. |
| [Nudge.ai](https://usenudge.ai/blog/building-an-ai-that-knows-when-to-shut-up), [pug.bot](https://pug.bot/blog/proactive-ai-assistant/), [Farvision](https://academy.farvision.com/intermediate/proactive-assistant-design) `[Literature]` | Naive "evaluate every task → schedule reminders → nudge" buries users. Proactive assistants need a decision policy (notify / suggest quietly / do nothing), a low default cap (e.g. 3/day), quiet hours, and opt-out. |
| [AppScale](https://appscale.blog/en/blog/agent-memory-staleness-context-rot-invalidation-temporal-validity-2026), [TianPan memory GC](https://tianpan.co/blog/2026/04/14/agent-memory-garbage-collection), [AWS AgentCore lifecycle](https://aws.amazon.com/blogs/machine-learning/designing-lifecycle-policies-for-agentcore-memory/) `[Literature]` | Memory is a lifecycle to govern, not a store to read/write. Unbounded memory degrades quality; expiration, contradiction detection, and "forgetting by design" are first-class. |

## 2. What the prior art says about us

| Our symptom | Literature name | Our gap |
|---|---|---|
| "All set" with 0 triggers/executions | False success | Single-control path; completion is the model's claim; tool results/receipts never consulted |
| Narration glued to the answer | Delta ≠ committed message | No turn identity, no typed parts, no commit point |
| Nudge → different finished answer; half replies | No terminal/cursor semantics | Turn lifecycle spread across consumer/coordinator/loop; retries indistinguishable from new turns |
| Days-late reminders; canned carousel nudges; 30-day notes | Notification fatigue; memory lifecycle | Commitments have ad-hoc expiry; ignore ≠ resolution; no caps/quiet hours/decision policy |

## 3. What we keep (do not re-architect)

- The ReAct loop, budget caps, provider abstraction, connector registry — sound and
  aligned with practice; we wrap it, we do not rewrite it.
- The security seam: sandbox, dynamic risk gate, durable approvals, append-only receipts.
  This *is* the dual-control mechanism the literature credits with a ~15x reduction in false
  success; the task is to extend verification to routine state changes, not replace it.
- Workflow engine internals (idempotency keys, deferred queue, replay safety).
- Memory storage taxonomy (facts/preferences/episodes/notes). The gap is policy, not schema.

## 4. Target architecture

### 4.1 Turn as a first-class, persisted entity

A `Turn` is created for every routed message and is the single owner of the turn's truth.

- States: `received → planning → streaming → tool → awaiting_approval → committing →
  completed | failed | cancelled | superseded`.
- One writer: the coordinator performs every transition. Consumers only transport frames;
  the agent loop only emits typed events.
- Durable: persisted (DB), with `turn_id` (uuid), room/user/message ids, deadline, attempt
  count, terminal reason, and budget snapshot.
- Cancellation and supersede are explicit transitions: a new message in an active turn
  either queues or supersedes (`superseded`) per policy; the old turn gets a terminal frame.
- Persist the assistant message only in `committing`/`completed`; a `failed` turn may keep a
  partial message explicitly flagged `is_partial`.

### 4.2 Stream protocol v2 (typed, versioned, exactly one terminal event)

All frames carry `turn_id`. Text events carry `part_id`, monotonic `seq`, and
`channel: narration | answer`. One terminal event per turn.

| Event | Payload |
|---|---|
| `turn.started` | turn_id, message_id, model |
| `text.delta` | turn_id, part_id, seq, channel, text |
| `tool.started` / `tool.result` | turn_id, call_id, name, status, effects? |
| `approval.requested` / `approval.resolved` | turn_id, approval_id |
| `turn.completed` | turn_id, final_message_id, usage |
| `turn.failed` | turn_id, reason_code, partial: true |
| `turn.cancelled` / `turn.superseded` | turn_id, reason |

Rules: raw chunk concatenation is not a contract; duplicate/reordered frames must be
ignorable via `seq`; reconnects resume by `turn_id` + last `seq`; the final persisted text is
authoritative over any preview.

### 4.3 Verification layer (the verifier is the architecture)

- Every state-changing action declares `mutates_state` + a postcondition, asserted by the
  harness against the authoritative record (DB row, registered trigger, outbound receipt) —
  never by the model. `preview().effects` (#168) and append-only receipts are the substrate.
- A turn's **write-set** (state-changing tool results/receipts) is audited before a success
  outcome is allowed. Claim without effect → `turn.failed{partial}` with an explicit reason
  and, for high-blast-radius actions, escalation via the durable approval seam (dual
  control).
- Budget/timeout/tool-error endings can never render as completion.
- Canary fix: one workflow-creation service that always registers triggers and reads the
  registered schedule back before reporting.

### 4.4 Commitment lifecycle

- One `Commitment` concept over reminders/workflows/proposals/notes:
  `open → done | dropped | expired`, with a source FK, due time, and resolution actor.
- Proactive policy: decision function `notify | suggest quietly | do nothing`; default cap
  (≤3/day), quiet hours, ignore → exponential backoff → stop; explicit one-tap drop.
- Notes: deterministic completion/drop/supersede by user intent (no model judgment), and
  stale notes excluded from the context prompt after a policy window.

## 5. Issues

### Phase A — Turn state and stream protocol (`T-A`)

**T-A1 — Turn record + state machine (single writer, deadline, cancel/supersede)** `[Analysis]`
- Context: no persisted turn today; lifecycle is implicit across `consumers.py`,
  `coordinator.py`, `agent_loop.py`.
- Build: new persisted `Turn` model (or equivalent) + `orchestration/turns.py` transition
  functions; coordinator is the single writer; deadline + cancel/supersede; every transition
  emits the existing `record_event` telemetry with `turn_id`.
- Acceptance: state machine unit tests (all legal/illegal transitions); a superseded turn
  emits exactly one terminal event; crash/restart leaves no turn in a non-terminal state
  (expired on boot).

**T-A2 — Stream protocol v2: typed events, turn_id, sequence, terminal frame** `[Analysis]`
- Context: `ai_stream_chunk`/`ai_step_event` drop `correlation_id` (`consumers.py:1399-1412`);
  no sequence, no terminal frame.
- Build: event union from §4.2; every frame carries `turn_id`, monotonic `seq`; exactly one
  terminal frame emitted in a `finally`; narration vs answer channels; best-effort sends
  (a progress-frame failure must never abort the turn).
- Acceptance: consumer contract tests for frame shape and terminal guarantee; a forced
  channel-layer failure mid-stream yields `turn.failed`, not a hung turn.

**T-A3 — Adversarial stream/reconnect tests + client contract doc** `[Literature]`
- Context: pathrule pattern; frontend is Mathia-private and needs a written contract.
- Build: deterministic fixtures; inject duplicate, missing, delayed, out-of-order events and
  disconnect before/after tool commit and final persistence; race cancellation against
  completion; reconnect from every cursor. Write `docs/contracts/turn-stream.md` (protected:
  needs plan) or `docs/turn-stream-protocol.md`.
- Acceptance: tests prove client convergence (or reconnect) with exactly one terminal state
  and no duplicate assistant message; doc published and referenced from the consumer module.

**T-A4 — Retire the dead direct-send path** `[Analysis]`
- Context: `generate_ai_response` (`chatbot/tasks.py:239`) is never called; `ai_response_message`
  (`consumers.py:1329`) is a second inconsistent persistence path.
- Build: delete or gate behind a deprecation flag; ensure nudges/reminders use the same
  single persistence helper as turns.
- Acceptance: no references remain; grep-based test (or import-level) that no second
  AI-message send path exists.

### Phase B — Verification (`T-B`)

**T-B1 — Action verification contract: `mutates_state` + postcondition** `[Literature]` *(protected: contracts/base_connector/action_catalog — plan first)*
- Context: connectors return `status` but the harness cannot distinguish a real state change.
- Build: additive contract fields (`mutates_state`, `postcondition`) with a default that
  keeps existing connectors working; postcondition executed by the executor against the
  authoritative record with a visibility budget; `verify-before-retry` + idempotency key for
  writes.
- Acceptance: contract doc updated (minor version); tests for pass/fail/timeout; a
  connector without the fields behaves exactly as today.

**T-B2 — Completion gate: write-set audit before "done"** `[Literature]` *(protected: agent_loop — plan first)*
- Context: `full_response` is persisted regardless of what tools actually did.
- Build: the turn collects a write-set (state-changing tool results/receipts); the harness
  decides the terminal outcome. A success claim without the required effect becomes
  `turn.failed{partial}` with a reason code; no success render/persist. Budget/timeout/error
  endings always partial.
- Acceptance: tests reproducing the observed room-1 turn (claim with zero writes →
  `partial`, never persisted as success); an all-read turn cannot claim completion.

**T-B3 — Unified workflow handoff with schedule read-back (canary)** `[Analysis]` `[Evidence]` *(protected: agent_loop)*
- Context: `handoff_to_workflow` drops `schedule` and never registers triggers
  (`agent_loop.py:998-1039`); three creation paths exist.
- Build: route all workflow creation through one service that validates, captures
  cron/timezone, registers `WorkflowTrigger` (+ Temporal schedule) and **reads the registered
  schedule back**; report from the verified row. Delete the bypass. Errors return `status:
  error`, never narration.
- Acceptance: end-to-end test — a scheduled handoff creates a real trigger and reports the
  persisted cron; killing trigger registration makes the tool return an error; the DB is the
  asserted source in tests.

**T-B4 — Stale commitment delivery policy** `[Literature]` `[Evidence]`
- Context: `Reminder#1` pending a day late; late fires are indistinguishable from on-time.
- Build: on delivery/check, reminders older than a policy window are marked `missed` and
  surface as a digest entry, not a late notification; only explicit user request re-arms.
- Acceptance: test that a >N-hour-late reminder is marked missed, not delivered; on-time
  delivery unchanged.

### Phase C — Commitment lifecycle (`T-C`)

**T-C1 — Commitment record + resolution commands** `[Literature]`
- Context: reminders/workflows/proposals/notes each have ad-hoc expiry; no shared lifecycle.
- Build: `Commitment` (or a shared state protocol across the existing models) with
  `open → done | dropped | expired`, source FK, due time; user commands "done/drop it" map
  deterministically to resolution (no model judgment).
- Acceptance: resolution tests per source type; resolving stops all future surfacing;
  redo/supersede closes the old commitment automatically.

**T-C2 — Proactive policy engine: caps, quiet hours, ignore→backoff→stop** `[Literature]`
- Context: canned carousel; ignore ≠ dismissal; no cap; long-run nagging is unbounded.
- Build: decision function `notify | suggest quietly | do nothing`; default ≤3/day/room;
  quiet hours; ignored nudges back off exponentially then stop; explicit one-tap drop;
  contextual content only (no upsell carousel).
- Acceptance: tests for cap, quiet hours, backoff-then-stop, and drop; a non-technical user
  profile (no workflow/invoice/reminder) is not nudged forever.

**T-C3 — Memory staleness: exclude/resolve stale notes** `[Literature]`
- Context: notes persist up to 30 days and are injected into every prompt.
- Build: stale action items excluded from the context prompt after a policy window and
  routed to a "stale" block the model is instructed not to surface; deterministic completion
  via C1; contradiction/duplication guard on summarizer writes.
- Acceptance: a seeded 30-day-old action item does not appear in the built context prompt;
  a "done" command resolves it immediately.

## 6. Build order

1. T-A1 (foundation) → T-A2 → T-A3.
2. T-B3 (canary, smallest verifiable fix; unblocks user trust) can ship in parallel with
   Phase A since it touches the existing loop.
3. T-B1 → T-B2 (after Phase A's turn exists, so the gate has a home).
4. T-B4, T-C1 → T-C2 → T-C3.
5. T-A4 anytime after T-A2 (the single persistence helper exists).

## 7. Anti-patterns (what we refuse)

- **No prompt-level verification.** "Always verify your work" is another thing the model
  authors. The gate is code reading the record.
- **No LLM judge as the completion gate.** Near coin-flip for existence; use judges only for
  graded quality of artifacts confirmed to exist.
- **No event-sourcing rewrite.** Scope to the turn entity, the protocol, and postcondition
  contracts.
- **No self-improving harness work before the verifier exists** — optimization loops amplify
  false success.
- **No new connectors/features under a broken truth model.** Reach is not the bottleneck.

## 8. Risks and open questions

- **Frontend coordination.** Protocol v2 needs the Mathia frontend; the contract doc (T-A3)
  is the interface. Should we ship v2 behind a capability flag per room?
- **Protected paths.** T-B1/T-B2/T-B3 touch `contracts.py`, `base_connector.py`,
  `action_catalog.py`, `agent_loop.py`; each needs a plan (`docs/plans/`) and human OK.
- **Cross-worker serialization.** The in-process lock (`consumers.py:29-55`) must move to
  Redis/DB-backed ownership as part of T-A1; Redis is required in production already.
- **In-flight turns at deploy.** T-A1 accepts that turns cannot survive a restart
  mid-`streaming`; they expire to `failed{partial}`.
- **Migration.** `Turn` and `Commitment` are additive migrations; existing reminders/notes
  map onto them lazily.
