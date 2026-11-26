# Plan: Persona-scoped delegation (post-v0.8)

Status: draft for maintainer approval

Builds on #203 (personas) and #137 (handoffs). Scheduled after v0.8 (epic
#227), except Phase 0, which may land alongside v0.8 (see §Working beside v0.8).
No issue is filed yet; each phase becomes one issue and one PR once approved.

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

## Touches (all phases)

- Paths: `Backend/orchestration/agent_loop.py`, `personas.py`, `tool_calls.py`,
  `eval/loop_runner.py`, `eval/golden_scenarios.json`;
  `Backend/workflows/handoffs.py`, `workflows/models.py` + one migration;
  `Backend/Backend/settings.py`; `docs/architecture/model.js`; operator docs; tests.
- Protected paths: `agent_loop.py` (Phases 0–2), `Backend/workflows/migrations/*` (Phase 3).
- Contracts, approvals, payments, secrets: none. One additive nullable field (Phase 3).

## What the code does today

- `delegate_task` takes `task` and a model-written `tools` filter; no persona
  (`agent_loop.py`, `META_TOOL_DEFINITIONS`).
- Calls in one batch run in parallel and split one share of the turn budget:
  half of what is left, divided among the calls, with a parent remainder
  below 5,000 work tokens granting nothing (`_batch_delegated_grants`).
- Sub-agent results are tainted (`_UNTRUSTED_TOOL_NAMES`) and the model sees
  only the summary. Delegated calls are stored as `TurnToolCall` rows with
  `provenance="delegated"` and `parent_call_id`.
- `create_handoff` / `run_handoff` (`workflows/handoffs.py`) have no caller
  outside tests. `Handoff` has no turn link; `room_id` is a plain integer.

---

## Phase 0 — Fix the sub-agent scoping and record bugs (may land with v0.8)

**Bug 0a: a handoff runs under the room's persona, not its target.**
`run_handoff` sets `room_id` to the handoff's room and `_run_sub_agent` looks
the persona up by `(room, user)`. The target persona's risk ceiling, approval
boundary, identity and skills are never applied; only its tool scope reaches
the run, as the tool list.

**Bug 0b: sub-agent tool scope is checked after execution.**
`_execute_scoped_tool_calls` receives no allowlist. A tool call outside the
offered tools runs if the room persona (or no persona) allows it, and
`run_handoff` marks the outcome `denied` only after the run. The same gap
applies to `delegate_task`'s `tools` filter. AGENTS.md §0.3 requires fail closed.

**Bug 0c: delegated call records are wrong.**
`_execute_scoped_tool_calls` drops the id `_record_receipt` returns, so a
delegated `TurnToolCall` row gets the parent `delegate_task` call's receipt
(usually none) instead of its own (`tool_calls.py`, `record_from_event`).
Delegated records also hard-code `not_run: False`, so a call the harness
refused is stored as `error`, as if it ran.

**Bug 0d: native search ignores persona scope.**
Native (provider-side) `web_search` runs without passing through
`_bucket_tool_calls`. The main loop adds it to the offered tools without
checking the room persona's scope (the `web_search_tool` step after
`get_tool_definitions`), and a sub-run with no `tools` filter inherits it.
`_rebuild_search_tool` keeps any `web_search` entry it is given. A persona
whose scope excludes search can therefore still search, and the run only
counts it in `server_tool_uses`.

**Risk class:** read-only enforcement. 0a, 0b and 0d only narrow what may
run; 0c only corrects what is recorded.

**Approach:**
1. `personas.combine_bounds(a, b)`. A side that is None or has an empty
   scope is the identity (no limit from that side). Two non-empty scopes
   intersect; an empty intersection is a deny-all sentinel, never "no limit".
   Ceiling is the lower rank; boundary is the union. Pure function.
2. `run_handoff` resolves the target persona and always sets it in a
   harness-only context key, overwriting any caller value, as it already
   does for `user_id` and `room_id`. `_run_sub_agent` applies
   `combine_bounds(room persona, target)`, and works when the room has no
   persona. A failed lookup of either removes tool reach, like `PERSONA_LOOKUP_FAILED`.
   The target's `persona_identity_prompt` (with its skills) replaces the room
   persona's identity block in the handoff's system prompt.
3. `_execute_scoped_tool_calls` takes the offered tool names as a required
   argument and denies any other call before execution.
4. Each delegated log entry carries its own `receipt_id` and a `not_run`
   flag; `record_from_event` stores them, so refused calls are `denied`.
5. Native search follows one rule and is enforced at the provider boundary:
   - **Rule:** a function in `personas.py` allows search only when the user's
     `allow_web_search` is not off (native search skips that flag today, too)
     and the effective scope is unbounded or includes `web_search` or
     `search_info`. The mapping lives there, not as a catalog alias.
   - **Offer:** `_rebuild_search_tool` takes `search_allowed` as a required
     argument, in the main loop and in sub-runs.
   - **Request boundary:** just before `llm_client` sends any request, it
     keeps only the server-side tool types the harness knows for that
     provider (`web_search_20250305` on Anthropic; DeepSeek's
     `deepseek_web_search` / `deepseek_read_url` only if they are ever added)
     and only when the rule allows. Anything else is removed. This covers
     provider fallback and future call paths. Kazi declares no DeepSeek
     search tool today.
   - **Response check:** a per-provider reader detects server-side tool use
     (Anthropic `usage.server_tool_use`; DeepSeek's search blocks, field
     names to be confirmed from its docs). An unrecognised block type taints
     the run. Search that was not allowed stops the run and is recorded.
   - **Provider table:** per provider, whether search can be turned off and
     whether it can be detected. A turn whose scope excludes search never
     falls back to a provider that fails either check.
   - Taint stays as the last line. Kazi cannot stop a provider that searches
     without being asked; the only remedy there is the choice of provider.
6. Check whether a sub-agent sees `delegate_task` in its own tool list; if
   so, remove the meta-tools from it.

**Verification:**
- New tests, each shown failing on `main` first. They run the real
  `_run_sub_agent` (`executor=None`, LLM scripted) with real `Persona` rows,
  not a mocked executor or a patched `_resolve_persona`:
  - a handoff to a persona whose approval boundary lists an action the room
    persona allows refuses that action (today it runs);
  - the same for an action above the target's ceiling that the room's
    bounds let run without asking;
  - handoffs in a room with no persona apply the target's bounds;
  - a handoff's system prompt names the target persona and carries its
    skills, not the room persona's identity (today it keeps the room's);
  - with a persona scope that excludes `web_search`, the request sent to the
    provider has no native search tool, in the main loop and in a sub-run
    (today both offer it); the same with `allow_web_search` off;
  - an unknown server-side tool type is removed at the request boundary,
    including after a provider fallback;
  - a response reporting search that was not offered stops the run and is
    recorded;
  - two disjoint scopes deny every call;
  - a scripted sub-agent that emits a tool outside its offered list never
    reaches `_execute_with_timeout` (today it does);
  - a failed target lookup makes no tool call;
  - a delegated call that ran links its own receipt; a refused one is stored as `denied`.
- Existing tests that change: `test_sub_agent_parity.py` passes an empty
  `parent_tools` while the script calls `get_weather` / `send_email`. Those
  fixtures will offer the tools they call, so each case keeps failing or
  passing for its original reason (taint, cap, persona), not because the new
  allowlist denies it. The PR lists each changed test and why.
- Golden eval: `loop_runner.py` fakes `_execute_meta_tool`, so delegation never
  reaches the code under test. Add a runner option that lets `delegate_task`
  run the real `_run_sub_agent` on the scripted LLM and records delegated
  executions. Then one `injection` pack scenario: a web result tells the
  sub-agent to call `run_command`, and the call is denied before execution.
- CI's blocking commands from `docs/golden-principles/tests-and-evals.md`.

**Rollback:** revert the PR. Rows written meanwhile stay valid.

---

## Phase 1 — A persona on `delegate_task` (after T-B2 #221)

**Risk class:** read-only enforcement plus a new record. It adds no reach.

**Approach:**
1. `delegate_task` gains an optional `persona` (a name, unique per user). The
   harness resolves it among the requesting user's active personas. Unknown,
   inactive or foreign means a refusal, never a silent fallback to no persona.
2. Effective scope = `combine_bounds(room persona, target)` narrowed further
   by the model's `tools` filter.
3. For that sub-task, the target's `persona_identity_prompt` replaces the
   room persona's identity block in the sub-run's system prompt.
4. Each persona-scoped delegation writes a `Handoff` row and the existing
   room note, so it is never hidden. Without `persona`, behaviour is as today.
5. Taint, budgets and sub-agents' inability to pause stay as they are.

**Verification:** tests for each rule in 1–4, shown failing first, on the real
sub-agent path. A golden loop scenario where the lead delegates to two
personas in one batch and each sub-run can call only its own scope; this
needs the loop runner to resolve personas per lookup, not one fixed persona.
The architecture model check passes.

**Rollback:** revert. Handoff rows already written stay as history.

---

## Phase 2 — Fan-out cap and measurement (with or after Phase 1)

**Risk class:** read-only enforcement. It only lowers how many delegations run.

**Approach:**
1. `AGENT_MAX_PARALLEL_DELEGATIONS` (default 3; a bad value falls back to the
   default, and 0 or less means 1). Calls past the cap in a batch are refused
   with their own message ("only N delegations run at once"), not the budget
   message. The batch's budget share is divided among the calls that run.
2. Operator docs: on a single consumer GPU running a local model, set the cap
   to 1, since parallel sub-runs mostly queue there (unmeasured; to confirm).
3. Telemetry per turn: delegations, personas used, work tokens, wall-clock time.

**Verification:** a batch of 5 delegations with cap 3 runs 3, refuses 2 with
the cap message, and the 3 share the whole batch share; bad setting values
(0, negative, non-integer) behave as stated. Shown failing first; the existing
budget tests pass unchanged.

**Rollback:** revert, or set the cap high to restore today's behaviour.

---

## Phase 3 — Handoff provenance (after the v0.8 Turn schema settles)

**Risk class:** additive schema change; no behaviour change.

**Approach:** a nullable `Handoff.turn` foreign key, set by Phase 1's path.
It answers "which turn asked for this?" for persona recall and is needed
before F-84. `WorkflowExecution` gets the same field only if a recall use
case needs it.

**Verification:** a migration test, and a Phase 1 path test that sets the field.

**Rollback:** reverse the migration (the field is nullable and new, so no
data is lost beyond the links themselves).

---

## Phase 4 — Live eval pack (later, needs contributors)

The golden loop runner scripts the model, so it tests gates, not answer
quality. A live pack would hold about 15 breadth tasks with recorded web and
shell results replayed, and compare one agent, fan-out on one model and
fan-out on mixed models at the same token budget. Score: facts found, false
claims, work tokens, wall-clock time. Model per persona is decided by this
pack, not before it.

---

## Performance budget (every phase)

Self-hosters compare Kazi with harnesses written in Rust and Go, so the
checks in this plan must not make the harness feel slower. Each phase's PR
measures its overhead and pastes the numbers; a phase that misses its budget
does not merge.

- **No new I/O on the hot path.** The checks are in-memory work: set lookups
  on the tool list and a scan of the response blocks. No added database,
  cache or network call per model call or per tool call. Bounds are computed
  once per turn or sub-run, not per call. The provider table is a module
  constant. Where a test can count queries (`assertNumQueries`), it does.
- **Budget per model call:** the request-boundary filter plus the response
  check together add under 1 ms at p99, measured with a micro-benchmark on
  the reference hardware in the frontend redesign proposal (§4.5) and on a
  small home-lab box. [Inference] Model calls take seconds, so this should be
  well under 0.1% of a turn; the PR measures it rather than assuming it.
- **Per delegation (Phases 0–1):** at most one added query, the target
  persona lookup, which `run_handoff` already makes.
- **Telemetry (Phase 2):** written once per turn through the existing
  `record_event` path, never per call. Violation events are rare by design.
- **Regression check:** the golden eval's run time before and after each
  phase is recorded in the PR.

## Working beside v0.8

Open v0.8 issues (2026-11-26): T-A1 #216, T-A2 #217, T-A3 #218, T-B1 #220,
T-B2 #221, T-B4 #223, T-C1–C3 #224–226. T-B3 #222 and T-A4 #219 are closed.

- **Phase 0 can land now, with coordination.** It touches the sub-agent path
  in `agent_loop.py`, `tool_calls.py` and `workflows/handoffs.py`. The v0.8
  proposal does not name `_run_sub_agent`, but two open items are near it:
  T-A2 (#217) changes the loop's events, and T-B2 (#221) must count delegated
  writes, which reads delegated call records. Bug 0c gives T-B2 correct
  receipt links for those records, so agree the record shape with the T-B2
  owner first. Keep it one small PR, rebased on the latest v0.8 work.
- **Phases 1–2 wait for T-B2.** Fan-out multiplies writes, so the completion
  gate should already count delegated writes before more delegation is encouraged.
- **Phase 3 waits for the Turn schema (#216)** so migrations in
  `orchestration` and `workflows` don't race.
- The v0.8 lead may take Phase 0 into v0.8 as a hardening item, or leave all
  of this for v0.9.

## Open questions for the human

1. Phase 0: a separate issue now, or folded into v0.8 as a hardening item?
2. Default for `AGENT_MAX_PARALLEL_DELEGATIONS`: 3, or 1 until Phase 2's
   telemetry gives real numbers?
3. Should Phase 1 ship with example scopes for Researcher (web), Operator
   (shell) and Archivist (notes) in the docs, or no examples?
