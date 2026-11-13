# Telegram remote-control bridge: design and plan

Status: **proposal, no code. Waiting for approval to start development.** Nothing in
this document approves a change. The data model needs migrations and one ticket
touches the coordinator and a contract (protected paths), so development starts with
plans under `docs/plans/` and a human OK (`AGENTS.md` §0).

- Investigation: 2026-11-08, against `main` at `121df6d` (includes #303, the turn
  runner).
- Plan revised: 2026-11-13, after #306 merged and after a review of how other teams'
  bridges failed (§2.1). This revision replaces the earlier tracks with one staged
  plan (§8).

**How to read the labels**

| Label | Meaning |
|---|---|
| **[Verified]** | Read in the code or docs during this pass. A `file:line` or page follows. |
| **[Inference]** | My conclusion from verified facts. Not run or tested. |
| **[Known]** | From background knowledge. Not checked this session. |
| **[Cannot verify]** | Could not be confirmed either way with the sources reachable. |

Paths are relative to `Backend/` unless they start with `docs/`, `scripts/` or a
repo-root file name.

**Where the citations come from.** The Phase 1 citations were gathered by read-only
code exploration. I re-read about 40 of the load-bearing ones by hand: the inbound
path, the turn runner, approval ids, consent, approval-row reuse, capability gates,
receipts, the sandbox environment, the connector and the settings. Those are the ones
the design depends on.

---

## 0. The plan on one page

**What it is.** A way to talk to a Kazi room from Telegram when you are away from the
keyboard: send it work, see what it answers, and approve or reject what it asks.

**The principle: Telegram is a window onto the room.**
- The room does all the work, once. Telegram shows it and feeds it.
- A room has one history. What you type in Telegram appears on the web, and what
  happens on the web appears in Telegram.
- If Telegram and the web ever disagree, the database is right.

**What you will see**
- **Each Kazi room is its own topic** in your private chat with the bot. There is no
  switching command. Typing a new message in the bot's main view starts a new topic,
  which becomes a new room (D4, D23).
- **Send** text, voice notes, files and photos into a topic. They go into that room
  exactly as if typed on the web.
- **See** Kazi's replies, its "ended" notices and anything typed on the web, in order.
- **While Kazi works** you see "Thinking…" with a Stop button, then live progress
  lines once the core's stream protocol lands (T-A2, #217).
- **Approve or reject** from buttons under a card. High-risk approvals ask for a PIN.
- **`/status`** and a "Telegram: connected" line on the web tell you whether the
  bridge is alive.

**The priority order: reliability and consistency first, features on top.**
- **The delivery layer must not fail quietly.** Every message Telegram accepts from
  you gets a visible outcome. Every room message reaches Telegram at least once, in
  order, across restarts.
- **Everything else may degrade.** If a progress line, a "Thinking…" draft or a widget
  fails, you lose that decoration and nothing else.

**How it works, in five facts**
1. **A new Django app, `telegram_bridge/`, not the connector.** The existing Telegram
   connector only sends (§1.1). The model never sees bridge actions, so no tool
   catalog entry changes.
2. **In: one shared entry point.** The bridge calls the same
   `submit_user_message` the web socket calls (TB-1), so every existing gate runs
   unchanged (§1.8).
3. **Out: delivered from the database, not from the live stream.** A mirror loop
   reads each bound room's messages and sends the ones Telegram has not had yet,
   recording each send (§4.1). This needs nothing from T-A2.
4. **Approvals feed the existing chat-reply path.** A button sends your "yes" or "no"
   through the same entry point as a typed reply, named for the one approval it
   answers (TB-16). Cards are read from the database.
5. **One poller, inside the web process, held by a database lease.** It retries
   forever and reports its own health (§3.2, §4.5).

**Build order** (§8)

| Stage | What you get | Waits on |
|---|---|---|
| 0. Device checks and plans | Answers to the open Telegram behaviour questions, on your phone | Approval of this plan |
| 1. Delivery layer | Pairing, a topic per room, text in, everything mirrored out, "Thinking…" and Stop, Approve/Reject cards, health. **Usable every day** | TB-1 and TB-16 in core |
| 2. Rich, no stream needed | PIN approvals, Continue buttons, topic lifecycle, files, voice notes in, photos, workflow approvals, notifications | Stage 1 |
| 3. Live | Progress lines while Kazi works | T-A2 (#217) |
| 4. Extras, each optional | Voice replies, photo understanding, widgets, group mode | Your call per item |

**Decisions already made** (§7): the connector freeze does not apply (D1); no Telegram
Premium (D21); no native speech model, voice is transcribed at the edges (D15); a topic
per room with no switching (D4); built new on the v0.8 core.

**Two things I still need from you** (they don't block approval):
- **Group mode (TB-14).** Will anyone but you use this bot? If not, I would drop it.
- **Voice replies (TB-M2).** Spoken answers, or are voice notes in and text out enough?

---

## 0.1 Where this fits in the roadmap

The roadmap (`docs/roadmap.md:28-33`) has v0.8, "turns that own the truth", in flight.
Its epic is #227, and its build order is A, then B, then C, with "do NOT jump ahead".

**Status on 2026-11-13** [Verified: GitHub PRs and issues, `CHANGELOG.md`, the code]

**T-A1 (#216) is delivered as a series of slices** from the approved #216b plan.
216b-2 (the turn runner, #303) merged on 2026-11-07, and 216b-3 (exact stop cancels
the running turn, #306) merged on 2026-11-12.

| Slice | PR | State | What it did |
|---|---|---|---|
| 216a: durable Turn rows, honest endings | #286 | Merged | `Turn` model, one writer, CAS transitions (`CHANGELOG.md`, "Added") |
| P3-3: durable pauses | #289 | Merged | Pauses on `Turn.paused_state_enc` (`CHANGELOG.md:237-264`) |
| 216b-0: consent postdates the prompt | #291 | Merged | `CONSENT_SKEW_SECONDS` (`CHANGELOG.md:229-236`) |
| 216b-1: turn leases, self-fence, one reap routine | #292 | Merged | `CHANGELOG.md:266-286` |
| 216b-2: the turn runner | #303 | Merged 2026-11-07 | Queue, FIFO claim, pause continuation, shutdown endings (`CHANGELOG.md:10-37`) |
| 216b-3: exact stop cancels the running turn | #306 | Merged 2026-11-12 | `Turn.cancel_requested_at`: an exact stop ends the running turn as `cancelled(user_cancel)` within `TURN_CANCEL_POLL_SECONDS` (default 2). `Turn.paused_at`: a budget or quota `continue` is consent only if sent after `paused_at` + skew (`CHANGELOG.md` on `main`, "exact stop cancels the running turn") |

Other v0.8 tickets: T-A4 #219 and T-B3 #222 are closed. T-A2 #217, T-A3 #218 and
T-B1/B2/B4 and T-C1-3 (#220-221, #223-226) are open. There are no T-A2 emitters in the
code.

**What I cannot see.** The approved #216b plan is not in `docs/plans/` or on GitHub. Nor
is the roadmap the plans cite, with its T-D, T-F, P3, E and V items
(`docs/plans/2026-10-per-message-work.md:9`). Local planning docs are gitignored
(`.gitignore:172-182`). So the placement below covers the slices I can see. Any later
slice of the #216b plan may move it.

**What #303 says about callers like the bridge** [Verified: the #303 description, "Live
calls (V-5)"]. The runner's only public contract is `submit(room_id, user_id)` and the
claimed `Turn`. The message-to-turn assumption lives in `enqueue_turn` and the
consumer. A future voice-call caller would submit a claimed Turn directly, keyed by
session rather than (room, user). The bridge is a message-based caller, so it uses
`enqueue_turn` + `submit` exactly as the consumer does, which needs no runner change.

**Placement.** The bridge is the **second client of the turn**. It sits on Phase A and
runs beside Phase B. Only its live progress lines wait for T-A2.

```
 T-A1 #216:  216a ✓ ─ P3-3 ✓ ─ 216b-0 ✓ ─ 216b-1 ✓ ─ 216b-2 ✓ (#303) ─ 216b-3 ✓ (#306)
                                                                              │
 core tickets the bridge adds:        TB-1 shared entry point ── TB-16 decision names its approval
                                                │                        │
 bridge:                 Stage 1 delivery layer ─────────────► Stage 2 rich features
                                                                         │
 T-A2 #217 (typed frames, + TB-4 fields and sinks) ────────────► Stage 3 live progress
 T-A3 #218 (client contract, + TB-A3)              ────────────► bridge in the contract fixtures
```

**What the bridge needs from the core, and when**

1. **A transport-neutral inbound door (TB-1). Needed for stage 1.** #216's rule is
   "consumers only transport frames". After #303 the consumer still owns persistence,
   routing gates and enqueueing (`consumers.py:861-1057`). Moving that into
   `chatbot/inbound.py` is in T-A1's spirit. It waited on 216b-3, which changed how a
   cancel is recorded in that same path. With #306 merged, it can start.
2. **A decision that names its approval (TB-16). Needed for stage 1.** Without it, a
   stale tap can be absorbed by a newer pause (§4.2). It touches the coordinator and
   `docs/contracts/approval.md`, so it needs a plan.
3. **Typed frames (T-A2, #217). Needed only for stage 3.** Messages, endings and
   approval cards come from the database (§4.1, §4.2). Only the per-tool progress
   lines need `turn.started` and tool events. Building those on today's
   `ai_step_event` text would mean building twice, so stage 3 waits.
4. **Truth on the phone (Phase B).** The bridge sends the persisted final message,
   never the concatenated stream. When Phase B makes endings honest (#221), Telegram
   inherits that with no bridge change.

**What 216b-3 (#306) already gives the bridge**
- **Stop from the phone.** The bridge sends an exact `stop` through the shared entry
  point, and #306 records it as `cancel_requested_at`. The running turn ends as
  `cancelled(user_cancel)`. A socket close, or a poller restart, is still not a cancel.
- **"Continue" buttons for budget and quota pauses.** A `continue` counts only if it
  was sent after `Turn.paused_at`, so a button inherits the same consent check as
  approval cards (216b-0).

**What to fold into existing v0.8 work** (proposed; not filed)
- **#216:** the transport-neutral `submit_user_message` (TB-1), and the broadcast of
  human messages to the room group so web tabs see Telegram-typed text (TB-2).
- **#217 (T-A2):** `approval.requested` carries `approval_pk`, `short_id`,
  `approval_kind` and a risk class; `approval.resolved` carries `decision` and `via`;
  frames reach any in-process subscriber through a sink registry (TB-4). Today's
  `confirmation` event uses the name `approval_id` for the 8-hex short id
  (`agent_loop.py:3596`), so name both fields explicitly.
- **#218 (T-A3):** the client contract doc is written for "a client", and its
  reconnect and duplicate fixtures run against a generic sink as well as the web
  consumer (TB-A3). A second client keeps the contract from encoding web-only
  assumptions.

---

## 1. Phase 1 findings: what exists

### 1.1 The Telegram connector (v0.4.2): outbound only

- **[Verified] It only sends.**
  - Its six actions call `sendMessage`, `sendPhoto`/`sendVideo`/`sendDocument`/`sendAudio`,
    `editMessageText`, `deleteMessage` and `getMe`. All go through `_tg_post`
    (`orchestration/connectors/telegram_bot_connector.py:610-615`).
  - Nothing calls `getUpdates`, `setWebhook` or `getFile`.
  - The class docstring says "Send and receive" (`:34`). That is not true.
- **[Verified] The documented webhook route does not exist.**
  - The module docstring (`:5-6`) and `docs/telegram.md:19-27` both tell users to set a
    webhook at `/api/telegram/webhook/`.
  - No `urls.py` defines that route. `Api/urls.py` has only a Calendly webhook
    (`:17`), and `Backend/urls.py:25-40` has no Telegram include.
- **[Verified] Token.**
  - It is the env var `TELEGRAM_BOT_TOKEN`, read into `settings.TELEGRAM_BOT_TOKEN`
    (`Backend/settings.py:688`).
  - The connector reads settings first, then the environment (`telegram_bot_connector.py:65-68`).
  - There is one global bot per install, with no per-user tokens.
  - The variable is documented in `docs/configuration.md:285` but missing from
    `.env.example`.
- **[Verified] Chat ids.**
  - No default chat is configured. Every action takes a `chat_id`.
  - A per-user chat id is *read* from `UserIntegration(integration_type="telegram").metadata["chat_id"]`
    (`chatbot/reminder_channels.py:66-76`, `chatbot/tasks.py:543-551`).
  - Nothing writes that row. `"telegram"` is not in `UserIntegration.INTEGRATION_TYPES`
    (`users/models.py:337-344`).
  - There is no pairing or linking flow.
- **[Verified] HTTP client.**
  - It uses its own `httpx.AsyncClient(timeout=20s)` (`telegram_bot_connector.py:75`).
  - It has no retry and no `retry_after` handling.
  - Errors are returned as `"Telegram API error: {exc}"` and logged
    (`telegram_bot_connector.py:297-303`).
- **Security item, handled privately.** One issue in the connector's error handling is
  handled under `SECURITY.md`; details are left out of this public doc.
- **[Inference] Bug: buttons can be truncated mid-character.** `callback_data` is cut to
  64 *characters* (`:449`). Telegram's limit is 64 *bytes* (§2), so a multibyte value
  can exceed it.
- **[Verified] Risk levels.**
  - Send, media, keyboard and edit are `risk_level:"low"`, `confirmation_policy:"never"`
    (`:110,145,179,206`; static catalog `orchestration/action_catalog.py:437,452,467,482`).
  - Delete is `medium` / `high_risk` (`:228-229`).
  - All have `capability_gate:"allow_telegram"`, which defaults to True
    (`orchestration/user_preferences.py:239-241`).
  - [Inference] Sending to the outside world at "low" conflicts with the v0.7 doctrine
    that outside-world sends keep a human gate (`docs/v0.7-brief.md:169-170`).
  - This design does not change it. Raising it is listed as a separate ticket, because
    `action_catalog.py` is protected.

### 1.2 Inbound path: WebSocket is hard-wired, but the seam is clean

- **[Verified] The path today:**
  1. `ChatConsumer.new_message` (`chatbot/consumers.py:819-1073`) validates the frame,
     encrypts, saves the `Message` and links it to the room (`:916-960`).
  2. It runs the routing gates (`:1004-1029`) and `enqueue_turn(...)` (`:1031-1038`).
  3. It calls `await get_turn_runner().submit(room_id, user_id)` (`:1057`) and
     returns.
  4. `TurnRunner._execute` (`chatbot/turn_runner.py:685-880`) rebuilds everything from
     the database. It builds the `send_chunk`/`send_step_event` callbacks around
     `group_send` to `chat_{room.id}` (`:741-767`).
  5. It calls `OrchestrationCoordinator.handle_message(...)` (`:816-830`) and persists
     the reply with `persist_kazi_message` (`:841-857`).
- **[Verified] There is no transport abstraction.**
  - The coordinator talks back only through injected callbacks
    (`orchestration/coordinator.py:1-8`).
  - Neither the agent context (`coordinator.py:935-955`) nor `Turn`
    (`orchestration/models.py:9-65`) has a `channel` or `source` field.
- **[Verified] No non-WebSocket entry point runs a turn.**
  - `retry_ai_message` is a stub (`chatbot/message_actions.py:203`). Voice upload
    stores a message and transcribes it, but never enqueues a turn
    (`chatbot/voice_views.py:16-74`).
  - Reminders, nudges and notices only inject Kazi-authored messages through
    `persist_kazi_message` (`chatbot/kazi_messages.py:125-189`).
- **[Verified] Human-authored messages have no helper.**
  - The logic in steps 1-3 lives inline in the consumer.
  - The one consumer-free recipe is in a test: encrypt, create the `Message`,
    `chats.add`, `create_turn`, then `runner.submit` (`chatbot/test_turn_runner.py:236-248`).
- **[Verified] Constraints a second channel must respect:**
  - **`submit` needs a long-lived event loop.** It calls `asyncio.create_task(self._drain_key(...))`
    (`turn_runner.py:299`), and the lease heartbeat needs a running loop
    (`orchestration/turn_leases.py:259-288`).
  - **Queued rows without a live owner are reaped.** After `2 × TURN_LEASE_SECONDS`
    the reaper expires them (`orchestration/turns.py:1091-1133`).
  - **Lite's boot sweep expires other processes' queued rows.** It expires every
    queued row not owned by the booting process (`turns.py:1176-1187`, called at
    `Backend/asgi.py:76-83`).
  - [Inference] So "enqueue in one process, drain in another" does not work today.
- **[Verified] The human message is never broadcast to the room group.** The consumer
  echoes it only to the sender's socket (`consumers.py:979`, `:1422-1424`). A web tab
  would not see a Telegram-sent message until it reloads.

**Cleanest entry point [Inference].** Extract `consumers.py:861-1057`, minus the
WebSocket-only checks (`from` equals username, `chatid` equals the socket room), into
`chatbot/inbound.py::submit_user_message(room, user, text, *, via)`. Both the consumer
and the bridge call it. `chatbot/` is not protected.

### 1.3 Rooms

- **[Verified] Models.**
  - `Chatroom` (`chatbot/models.py:40-63`) has `participants` (M2M to `Member`),
    `chats` (M2M to `Message`), a per-room AES-256-GCM `encryption_key` stored as
    `enc:` + Fernet (`:43,49-60`), and `model_pref` (`:44-47`).
  - It has an integer primary key and no owner field. Access means membership.
- **[Verified] Creation** happens in five places: the signup signal
  (`users/signals.py:39-46`), `_ensure_default_room` (`chatbot/views.py:38-67`),
  `create_room` (`views.py:287-334`), `seed_kazi`, and `invite_user` (`views.py:341-412`).
- **[Verified] Access checks.**
  - **Cached check:** `security_policy.user_has_room_access(user_id, room_id)`
    (`orchestration/security_policy.py:123-138`). It returns False when either id is
    missing, caches for 300 s, and is invalidated by signals (`chatbot/signals.py:10-40`).
  - **Not on the chat path:** it is called on the workflow and router paths, but not on
    the WS → runner → agent loop path. The consumer checks membership inline
    (`consumers.py:100-103`, `:958`).
- **[Verified] Preferences.** The model selector is `Chatroom.model_pref`, a single
  column. It has an API at `chatbot/model_api.py:43-86`, helpers in
  `chatbot/model_preference.py:89-117`, and there is no general room-settings JSON.
  Other per-room state lives in separate tables (for example `ShellHostGrant`,
  `orchestration/models.py:121-141`) or in the cache.
- **Could a Telegram binding live on the room? [Inference]** No. Use separate tables.
  - A binding is per user *and* per external chat or thread, and rooms have no owner.
  - Bindings are security state that must be revocable and auditable. The repo's
    precedent for that is append-style rows such as `ShellHostGrant`.
- **[Verified] Writing a message correctly from outside the consumer:**
  - encrypt `{content, timestamp}` with the room cipher (`kazi_messages.py:97-112`);
  - store `json.dumps({data, nonce})` (`consumers.py:931-940`);
  - `room.chats.add` (`:960`).
- **[Verified] Files.**
  - `upload_document_to_ai` (`chatbot/message_actions.py:221-346`) checks access and a
    per-plan quota, accepts PDF up to 10 MB or images up to 5 MB, saves the file, creates
    a `DocumentUpload` and queues `process_document_task`.
  - The agent sees the three newest completed documents, 2000 characters each, through
    `ContextManager` (`chatbot/context_manager.py:155-167`).
  - Uploading a document does not start a turn.

### 1.4 Approvals

- **[Verified] One model, two kinds.** `WorkflowApprovalRecord` (`workflows/models.py:532-620`)
  holds both `kind="workflow"` and `kind="agent_loop"`.
  - Primary key: a sequential `BigAutoField` (`workflows/migrations/0003_human_gated_runtime.py:86`).
  - At most one pending agent-loop row per (room, user)
    (`uniq_agent_loop_pending_room_user`, `:614-620`).
  - No `risk_level` field.
- **[Verified] Agent-loop short id.** It is 8 hex characters of
  `sha256(room:user:time:count)` (`orchestration/agent_loop.py:3477-3479`).
  - It is display-only (`orchestration/agent_prompts.py:380,392`).
  - No reply parser reads it. Approval matching is a whole-message match against a
    fixed set (`orchestration/shell/chat_intents.py:14-20,77-107`).
- **[Verified] Agent-loop approval happens only through a chat reply.** There is no REST
  or WebSocket approve command (`consumers.py:488-524`). The path is: a "yes" message,
  then `handle_message`, then:
  1. **Consent timing** `_consent_postdates_prompt` (`coordinator.py:127-175`). The
     message must be newer than `record.created_at + CONSENT_SKEW_SECONDS` (default 2,
     `Backend/settings.py:545`). A missing message or any error denies.
  2. `resume_after_confirmation` (`agent_loop.py:3875-4007`), which:
     - claims the paused turn through `_claim_turn_for_resume` (`:3833`), which refuses
       another room or user (`:3848-3853`) or an expired pause (`:3861-3863`);
     - requires an exact match of call ids and argument hashes (`:3960-3981`);
     - consumes the row with a conditional UPDATE, with reviewer equal to requester
       (`:881-904`, `:3983-3986`).
  3. Each held call writes a receipt with `basis="human"` (`agent_loop.py:2805-2807`).
  - Decline is `cancel_pending_action` (`agent_loop.py:4187-4216`).
- **[Verified] The approval row is reused.**
  - `save_pending_confirmation` reuses an existing pending row for a new pause. It does
    so when the row is expired or belongs to another turn, and it resets `created_at`
    (`agent_loop.py:839-851`).
  - **[Inference] Consequence:** a button keyed only on the row id could approve a
    *different* action. The bridge must bind the button to (row id, turn id, short id).
- **[Verified] Operator API (workflow kind only).**
  - Endpoints: `POST /api/workflows/executions/<id>/approve|reject/` (`workflows/urls.py:10-11`).
  - Both go through `_approval_action` (`workflows/views.py:426-459`), which checks
    owner (`workflow__user=request.user`) and `status="waiting"`.
  - It then calls **`submit_execution_approval(execution, *, approval_id, reviewer_id, decision, comment)`**
    (`workflows/temporal_integration.py:984-1017`).
  - That function uses the Temporal Update `record_approval_decision` when
    `WORKFLOW_APPROVALS_UPDATE_API` is on. The update rejects a stale id with
    `ApprovalMismatch` (`:409-422`). Otherwise it sends a signal, which has no id check
    (`:400-402`).
- **[Verified] Sweeper.** `sweep_stuck_approvals` (`workflows/tasks.py:239-330`) runs
  every 300 s (`Backend/settings.py:587-590`). It expires paused turns and times out
  pending rows past `expires_at`.
- **Which function a Telegram button should call:**
  - **Agent-loop approvals: none directly.** Calling `resume_after_confirmation`
    from the bridge would skip consent timing, the pause hand-off, runner ordering and
    reply persistence (`coordinator.py:1218-1236,1346-1431`). The button should submit
    "yes" or "no" through `submit_user_message` (§1.2), exactly as a typed reply would.
    [Inference]
  - **Workflow approvals:** call `submit_execution_approval`, after the same owner and
    status checks as `_approval_action`. [Verified that both views call it; Inference
    that the bridge should.]
- **Security item, handled privately.** One workflow-approval issue found during this
  pass is handled under `SECURITY.md`; details are left out of this public doc.

### 1.5 Agent events and streaming

- **[Verified] Layer 1: the agent loop's generator.** It yields
  `AgentEvent(kind, data)` (`agent_loop.py:128-132`). Kinds:
  - `thinking`, `text_delta`, `harness`
  - `tool_start` (`:3427`), `tool_result` (`:3428-3433`)
  - `confirmation` with `approval_id`, `tier`, `calls`, `effects` (`:3581-3597`)
  - `paused`, `budget_request`, `error`
  - `done` with iterations, tool calls and elapsed (`:3715-3723`)
- **[Verified] Layer 2: the coordinator** maps these into chunks and step events
  (`coordinator.py:870-933`).
  - **The structured approval payload is dropped.** Only the text goes out
    (`coordinator.py:903-906`), followed by the step `("validating", "completed",
    "Waiting for confirmation.")`.
- **[Verified] Layer 3: group frames on `chat_{room_id}`.**
  - `ai_stream_chunk {chunk, is_final, correlation_id}` (`turn_runner.py:747-755`)
  - `ai_step_event {event{step_id, phase, state, message, timestamp}, correlation_id}`
    (`turn_runner.py:760-767`), with `step_id = "turn_<request_message_id>"`
    (`coordinator.py:461`)
  - `ai_message_saved {message{id, member, content, timestamp, parent_id}}` (`kazi_messages.py:183-186`)
- **[Verified] No event bus.** There are no signals on `Message`. The only pub/sub is
  the channel layer:
  - Redis in the full profile (`Backend/settings.py:465-482`)
  - in-memory in lite (`settings.py:439-448`)
  - Celery already does cross-process `group_send` into room groups
    (`kazi_messages.py:183`, `chatbot/tasks.py:278,359`).
- **Can a second subscriber listen without touching the consumer? Yes [Inference].**
  - The bridge can `new_channel()`, `group_add("chat_<room>", ch)` and `receive(ch)`.
    In lite it must live in the same process.
  - [Known] channels_redis caveats (not set in settings): group membership expires
    (default 86400 s), so re-add periodically. The default channel capacity is 100, and
    `group_send` drops silently when full. Delivery is at most once.
  - So the database (`Message`, `Turn`, `WorkflowApprovalRecord`) stays the source of
    truth. Frames are only a trigger.
- **[Verified] Planned replacement.** v0.8 T-A2 plans typed frames such as
  `turn.started`, `approval.requested` and `turn.completed`
  (`docs/proposals/2026-10-turn-state-and-verification.md:96-113,151-167`). The
  contract doc `turn-stream.md` would come with T-A3. Neither exists yet. The bridge
  delivers messages from the database (§3.1) and uses v2 frames only for progress
  lines (stage 3). It never reads today's frames.

### 1.6 Notifications

- **[Verified] Routing is hard-coded per channel.** `NotificationService.notify`
  (`notifications/services.py:62-144`) has one `if` per channel key (`in_app`,
  `email`, `whatsapp`), each with its own Celery task (`notifications/tasks.py:25-115`).
  - Delivery flags are fixed columns (`notifications/models.py:95-98`).
  - The per-user matrix lives in `profile.notification_preferences` with a fixed
    channel set (`orchestration/user_preferences.py:81-111`).
- **[Verified] There is no channel interface to implement.**
  - `notifications/capabilities.py:17-22` already declares a `telegram` row, but no
    production code imports it.
  - Telegram is a channel only in the separate reminder pipeline
    (`chatbot/reminder_channels.py:108-116`, `chatbot/tasks.py:536-563,641-664`).
- **[Inference] Latent bugs.**
  - `deliver_notification_email` calls an async `execute` without `context` and
    without awaiting it (`notifications/tasks.py:49-55`). The error is swallowed
    (`:58-59`).
  - WhatsApp delivery omits `context` (`:103-107`).
- **Should Telegram be a notification channel? Yes, later (§8, TB-15) [Inference].**
  `workflow.approval` events are already routed through `notify` (`services.py:45`). A
  Telegram channel with buttons is the natural way to approve workflows from a phone.
  - It means editing the hard-coded branches and adding a delivery flag (which needs a
    migration). There is no interface to implement.
  - Do it after the bridge, so it reuses pairing and the Bot API client.

### 1.7 Process model

- **[Verified] Compose services:** `redis`, `db`, `web` (gunicorn + uvicorn,
  `--workers 4`), `celery_worker`, `celery_beat` and `temporal_worker`
  (`docker-compose.yml:2-115`).
  - The `kazi` supervisor (`scripts/kazi.py:1131-1173`) runs `web` (one uvicorn
    worker), `sidecar`, `worker` and `beat`.
  - It has a file lock against a second supervisor (`:275-316`).
- **[Verified] Existing long-running patterns:**
  - management commands `start_temporal_worker` (`workflows/management/commands/start_temporal_worker.py:22-48`,
    `asyncio.run` plus `worker.run()`) and `run_shell_exec`;
  - in-process lifespan tasks: `InProcessScheduler` (`Backend/asgi.py:87-91`, stopped at
    `:120-122`), the turn heartbeat and the runner drainers.
- **Celery is a poor host for a long poll** [Verified settings; Inference conclusion].
  - Concurrency 1, prefetch 1, 30-minute hard limit, recycled children
    (`Backend/settings.py:427-432`).
  - In lite it runs eagerly (`:393`).
- **[Verified] Singleton patterns:**
  - `cache.add` claims (`chatbot/tasks.py:787-807`)
  - a database compare-and-set claim (`orchestration/in_process_scheduler.py:209-258`)
  - database leases with heartbeat (`orchestration/turn_leases.py`)
  - There is no Redis lock.
- **Where the poller lives [Inference]:** in the web process as a lifespan task, held by
  a database lease. Turns must be drained by the process that enqueued them, which must
  stay alive (§1.2), and lite's channel layer is in-memory. A separate compose service
  works only in the full profile. It would need its own `TurnRunner` and shutdown, and
  it would touch `docker-compose*.yml`, which is protected. See D3.

### 1.8 Security hooks the bridge has to pass through

| # | Hook | Where | Fail behaviour | Bridge gets it by |
|---|---|---|---|---|
| 1 | Room membership | `consumers.py:958`; `security_policy.user_has_room_access` `:123-138` | Closed on missing ids; a DB error raises (`security_policy.py:135-137`), so callers must deny on exception | Calling `user_has_room_access` per update (new), catching errors as deny |
| 2 | Mute | `consumers.py:590-602,861-874` | Raises, then rejects | Shared seam |
| 3 | Message rate limit, 30/min | `consumers.py:576-588` | **[Inference] Open** when Redis is down (`IGNORE_EXCEPTIONS`, `settings.py:438`) | Shared seam, plus the bridge's own closed limiter |
| 4 | Length 1-5000 | `consumers.py:899-908` | Closed | Shared seam |
| 5 | Moderation buffer | `consumers.py:604-674` | Best effort | Shared seam |
| 6 | Kill-switch directives | `turn_runner.run_immediate_directives` `:101-184` | Closed | Shared seam |
| 7 | Queue cap | `enqueue_turn` `turn_runner.py:920-962` | Closed | Shared seam |
| 8 | Consent timing | `coordinator._consent_postdates_prompt` `:127-175` | Closed | Unchanged. `Message.timestamp` is when the bridge saves the message, not Telegram's `date` (see §4.2 on why that matters) |
| 9 | Whole-message approve/decline match | `chat_intents.py:77-107` | Closed | The button sends the exact word |
| 10 | Prompt-injection check on the query | `security_policy.should_block_action` `:97-102`, used `tool_executor.py:117-123` via `raw_query` (`coordinator.py:955`) | Blocks sensitive actions | Unchanged, as long as the text reaches the coordinator as the query |
| 11 | Injection in parameters; parameter sanitizing | `tool_executor.py:124-139`; `security_policy.sanitize_parameters` `:105-120` | Closed | Unchanged |
| 12 | Capability gate | `tool_executor.py:107-115` | **[Verified] Open** if `context["preferences"]` lacks the gate (`preferences.get(gate, True)`, `:111`) | Unchanged. The coordinator loads preferences, with a conservative fallback (`coordinator.py:700-735`) |
| 13 | Risk bucketing and persona ceiling | `agent_loop._bucket_tool_calls` `:415-468`; `tool_executor.get_tool_risk_info` `:344-381` | Closed | Unchanged |
| 14 | Room taint | `agent_loop.py:302-352` | Closed on exception; [Inference] open when Redis is down | Unchanged |
| 15 | Token quota | `llm_client._check_token_quota` `:685-730` | Closed | Unchanged |
| 16 | Output redaction | `agent_loop.py:3175` (model text); `_sanitize_tool_result` `:1570-1589` | — | Already applied to frames. The bridge re-applies `redact_sensitive_text` before sending |
| 17 | Action receipts | `action_receipts.record_action_receipt` `:227-284`, via `agent_loop._record_receipt` `:1841-1879` | **[Verified] Open**: a failure is logged and swallowed (`:1878-1879`) | Unchanged. The bridge keeps its own append-only decision rows |
| 18 | Sandbox environment | `orchestration/shell_exec/backends.py:57-80` (allowlist) | Closed | `TELEGRAM_BOT_TOKEN` is not on the allowlist, so it stays out of commands |

**Where reality disagrees with AGENTS.md (§10 asks me to report these):**

- **Receipts are append-only only by convention.** `_undo_reminder` updates
  `receipt.status` (`orchestration/action_receipts.py:370-374`), and the admin allows
  edits (`orchestration/admin.py:6-30`). This contradicts non-negotiable 4.
- **The capability gate fails open** when preferences are missing (`tool_executor.py:110-111`).
  This contradicts non-negotiable 3.
- **`docs/contracts/approval.md` is out of date.**
  - It describes loop state "in Redis" with a 10-minute TTL (`:29-34`). Pauses moved to
    `Turn.paused_state_enc` (`CHANGELOG.md:237-241`).
  - It documents a `note`/`reason` body, but the view reads `comment`
    (`workflows/views.py:441`).

### 1.9 Connector freeze and contracts

- **[Verified] The freeze.**
  - It started in v0.4 as a one-cycle freeze on new connectors, explicitly including
    "messaging channels", with "scheduling surfaces" listed under domain features
    (`docs/v0.4-brief.md:47-52,199-201`).
  - It became the lasting "shell is the reach story" direction (`docs/roadmap.md:12-13`;
    `AGENTS.md`, "v0.7 direction").
  - v0.8 adds "No new connectors/features under a broken truth model"
    (`docs/proposals/2026-10-turn-state-and-verification.md:260`).
  - Telegram itself shipped in 0.4.2 (`CHANGELOG.md:929-938`) as an undocumented
    exception.
- **Maintainer decision (2026-11-08):** the freeze does not apply to this work. The
  facts above are kept for the record.
- **Contracts** (`docs/contracts/`: `tool-schema`, `connector-execution`, `approval`,
  `execution-detail`, `replay-safety`, `credential-scoping`):
  - **[Inference] No change is needed if approvals go through the chat-reply path.**
    `approval.md` already says agent-loop approvals are resolved "via the chat surface"
    (`docs/contracts/approval.md:39`), and Telegram becomes another chat surface.
  - Recommend a patch-level note in `approval.md` naming non-web chat surfaces.
  - **A contract change would be needed only for:**
    - binding the approval id into consent (TB-16, an additive minor bump to `approval.md`);
    - consuming the v0.8 stream frames, which depends on `turn-stream.md` (T-A3), not
      yet written.
- **[Verified] Nothing is planned in this area.**
  - The nearest vision is "One tap on a dance floor: durable approval + diff + receipt"
    (`docs/v0.6-brief.md:43-50,69`).
  - There is no roadmap item for inbound channels or remote control.
- **[Verified] Boundaries.** `scripts/check_boundaries.py:23-51` has two rules:
  - connector files may not import six named core modules (`:28-35`);
  - no raw shell anywhere.
  - [Inference] A new `telegram_bridge/` app may import `chatbot`, `orchestration` and
    `workflows` without a violation. Putting bridge logic *in the connector* would
    violate rule 1.

### 1.10 Tests to reuse

**Runner and rules**

- [Verified] Tests run with Django's runner `tests.runner.KaziDiscoverRunner`
  (`tests/runner.py:36-48`). There is no pytest, respx or factory_boy.
- [Verified] Rules: `docs/golden-principles/tests-and-evals.md` (must-fail, real
  layer, races with barriers, reset singletons). CI's blocking commands are at `:68-80`.

**Where to look**

| Area | Files and helpers [Verified] |
|---|---|
| Telegram HTTP | `orchestration/test_v042_http.py:35-102` (`patch.object(c, "_tg_post", AsyncMock)`, fake token marked `# nosec B106` at `:98`). A real `httpx.Response` via patching `httpx.AsyncClient.post`: `orchestration/test_tts_connector.py:29-62` |
| Turn runner and consumer | `chatbot/test_turn_runner.py`: `_FakeLLM` (`:75-103`), `InMemoryChannelLayer` override (`:117-120`), resets of module singletons (`:122-163`), `_queued_request` (`:236-248`), and the approval-through-socket case `test_a_queued_yes_cannot_approve_a_prompt_it_never_saw` (`:679-729`) |
| Durable pauses | `orchestration/test_durable_pauses.py`: `_PauseHarness` (`:134-216`) and `CoordinatorRestartTests._coordinator` (`:676-733`, with `CONSENT_SKEW_SECONDS=0`) |
| Approvals | `orchestration/test_agent_approvals.py:32-196`; `orchestration/test_consent_postdates_prompt.py` |
| Workflow approvals | `workflows/tests.py`: `TemporalUpdateApprovalTests` (`:1206`) with `_submission_env` (`:1247-1255`) |
| Telegram user mapping | `orchestration/test_reminder_delivery_policy.py:443-477,555-563` |

**Note** [Verified]: the golden-principles file lists `python scripts/check_architecture_model.py`
as blocking, but `.github/workflows/main.yml` does not run it.

---

## 2. Phase 2: Telegram facts

**Sources and method.**
- The official pages were fetched with curl on 2026-11-08 and read as text:
  `core.telegram.org/bots/api`, `/bots/api-changelog`, `/bots/faq`, `/bots/features`,
  `/api/end-to-end`.
- The newest version on the changelog was **Bot API 10.3, August 24, 2026** when this
  section was written. **Bot API 10.4 (2026-11-09)** was checked on 2026-11-13; what
  it adds for the bridge is in §3.6.
- GitHub issue pages were read through a fetch tool that returns a summary, not raw HTML.
  Treat those quotes as close paraphrase.

| Claim | Status | Evidence |
|---|---|---|
| getUpdates and webhooks cannot both be active | **Confirmed** | API: "two mutually exclusive ways of receiving updates". getUpdates note 1: "This method will not work if an outgoing webhook is set up." FAQ: "not possible to get updates via long polling while an outgoing Webhook is set." Also: "Incoming updates are stored on the server until the bot receives them… not kept longer than 24 hours." |
| `callback_data` limit is 64 bytes | **Confirmed** | InlineKeyboardButton: "Data to be sent in a callback query to the bot when the button is pressed, 1-64 bytes". |
| `has_topics_enabled` | **Confirmed** | Added in 9.3 (Dec 31, 2025): "True, if the bot has forum topic mode enabled in private chats. Returned only in getMe." |
| `message_thread_id` in DMs | **Confirmed** | 9.3 added `message_thread_id` and `is_topic_message` for "private chats with forum topic mode enabled", plus the send parameter. sendMessage today: "for forum supergroups and private chats of bots with forum topic mode enabled only". |
| `createForumTopic` in private chats | **Confirmed** | 9.4 (Feb 9, 2026): "Allowed bots to create topics in private chats using the method createForumTopic." Method text: "create a topic in a forum supergroup chat or a private chat with a user". 9.4 also added `allows_users_to_create_topics` and a BotFather setting to stop users creating or deleting topics. Topic mode is enabled per bot in @BotFather (features page). |
| Private-chat topics need Telegram Premium | **Unconfirmed** | The official API reference, changelog and features page say nothing about Premium for private-chat topics. There, Premium appears only for custom emoji, gifts and similar. The #847 reporter happened to be on Premium. Whether clients without Premium display DM topics, I cannot verify. |
| Bot API 10.0 regression, tdlib/telegram-bot-api #847 | **Changed: fixed** | Opened May 8, 2026 (`sendMessage` with `message_thread_id` → "message thread not found" for all DM topics after the 10.0 rollout). Closed the same day; maintainer levlam: "This should be fixed already." A user confirmed it was fixed. **But** #905 (opened Sep 29, 2026, still open, on Bot API 10.3) reports a newly created DM topic sometimes refusing every bot write ("message thread not found"; `deleteForumTopic` → `TOPIC_ID_INVALID`), per topic and not reproducible on demand. A search-result title for #854 says `message_thread_id` is silently ignored when the topic was deleted. I did not open that page: unconfirmed. |
| `sendMessage` rate limits | **Confirmed (wording changed)** | FAQ: in one chat, avoid more than one message per second ("short bursts" may be allowed, then 429). In a group, no more than 20 messages per minute. Bulk is about 30 per second, or up to 1000 per second with paid broadcasts (needs 100,000 Stars and 100,000 MAU). 429 responses carry `retry_after` (ResponseParameters). Features page: do not depend on hardcoded limit values. |
| `editMessageText` rate limits | **Unconfirmed** | No official number for edits. Design assumption [Inference]: count edits against the same one-per-second-per-chat budget and honour `retry_after`. |
| Bot chats are not end-to-end encrypted | **Unconfirmed in the Bot API docs; treat as true** | No sentence on api, faq, features or api/end-to-end says so. [Known] Telegram's E2E "secret chats" exist only between users. Bot chats are cloud chats. [Inference] getUpdates returns message text as plaintext JSON from `api.telegram.org`, so Telegram's servers hold the plaintext of everything the bridge sends or receives. |

**Other changelog items that matter to this design [Verified]**

- **`sendMessageDraft` (9.3; "allowed all bots" in 9.5, March 1, 2026; `can_stop`/`keep_on_stop`
  in 10.3).** It streams partial messages. The plan uses it for "Thinking…", Stop and
  progress lines (D10, §4.5); details are under "Facts added on 2026-11-13" below.
- **Guest mode (10.0).** Bots can receive `guest_message` updates from chats they are
  not in. Exclude them with `allowed_updates`, and reject anything with `guest_query_id`.
- **Bots can see some other bots' messages in groups (10.0).** Reject `from.is_bot`.
- **`DisabledButton` / `InlineKeyboardButton.disabled` (10.3).** These can grey out
  resolved approval buttons. Removing the keyboard also works.
- **`answerCallbackQuery` is required.** "Telegram clients will display a progress bar
  until you call answerCallbackQuery".
- **`getFile` limits.** Downloads up to 20 MB. The file link is "valid for at least
  1 hour". The download URL contains `bot<token>`.
- **`editMessageText` 48-hour limit** applies only to business messages, not to the
  bot's own messages.

**Facts added on 2026-11-13** [Verified: `core.telegram.org/api/forum`,
`core.telegram.org/api/bots/ai`, `telegram.org/tos/bot-developers`, Bot API reference]

- **Topics in a private chat are turned on per bot in @BotFather ("Threaded mode").**
  The forum page: "Bots can also behave like forums if Threaded mode is enabled via
  @botfather". The Bot API reports it as `has_topics_enabled`.
- **It costs nothing unless the bot sells things for Telegram Stars.** Terms §6.2.6:
  while topics in private chats are enabled, "all subsequent Telegram Stars purchases
  carried out within that TPA shall be subject to a non-refundable fee of 15%". The
  bridge sells nothing. The same clause says the feature works "provided that the
  relevant users' clients support these features".
- **When users may create topics, there is no General box to type in.** The forum
  page: "graphical clients should prevent users from sending topic-less messages" and
  the General view shows "Type any message to create a new thread". A message typed
  there creates a new topic. The Bot API marks such a topic with `is_name_implicit`:
  the name "wasn't specified explicitly by its creator and likely needs to be changed
  by the bot". This changes the earlier "General is the lobby" idea (D4, §4.4).
  - When users may **not** create topics (the BotFather option "Disallow users to
    create new threads"), clients do allow messages in General.
- **A deleted topic sends the bot nothing.** The forum page: "There is no dedicated
  update emitted when a topic is deleted"; it shows up as message deletions, which the
  Bot API does not deliver to bots. The bridge learns of it from a failed send.
- **`sendMessageDraft` is for private chats only** (`chat_id`: "Unique identifier for
  the target private chat"), takes a `message_thread_id`, and is "a temporary
  30-second preview".
  - Empty text shows a "Thinking…" placeholder.
  - `can_stop` shows a Stop button, and the bot receives a
    `stopped_message_generation` update when it is pressed.
- **Drafts and "typing…" share one rate limit, per chat:** "Max 20 calls in 5 seconds"
  and "Max 40 calls in 30 seconds", and "the same ratelimits apply to… sendMessageDraft,
  sendChatAction". The server "may still send short cool-down FLOOD_WAITs (at most 3
  seconds)".
- **Not every app shows several drafts in one topic.** "Not all Telegram clients
  currently support rendering multiple live drafts… in the same topic concurrently."
  The bridge uses one draft per topic.

---

## 2.1 Lessons from people who built this kind of bridge

Read on 2026-11-13. **(R)** means I fetched and read the page. **(S)** means I saw only
a search-result summary, so treat it as a lead, not a fact. Pages that only resell or
summarise other people's work were left out.

### What went wrong for others, and what this plan does about it

| # | Who | What happened | What this plan does |
|---|---|---|---|
| L1 | **Libera.Chat ⇄ Matrix bridge**, shut down August 2023. Libera's retrospective (R), Matrix.org's closing post (R) | After a change that kept connections alive across bridge restarts, "the bridge began silently dropping ranges of messages, resulting in roughly one out of every five messages being dropped during periods of high bridge activity. These issues were often undetectable by the average user on either side." Outages were ones the operator "failed to notice until Libera staff put substantial effort into documenting the symptoms". The final trigger was a privacy filter that stopped working: people saw channel content they should not have. Matrix.org later wrote that it "does not have the resources to maintain and operate the bridge" | **No silent drops:** every send is a row in a delivery ledger, so a missing message is a visible pending row, and `/status` shows the count (§4.1). **Simple restarts:** the bridge keeps no state in memory that matters; on restart it re-reads the database. **Its own health check** (§4.5). **Access is re-checked on every tick and fails closed** (§4.1 step 1). **Small enough for one maintainer:** one process, one loop, no second queue |
| L2 | **grammY**, a widely used Telegram bot framework: reliability guide and flood-limit guide (R) | "Bots cannot guarantee an *exactly once* execution". Plain long polling gives "*at least once*" and "users may receive duplicate messages". Its concurrent runner confirms updates "prematurely", so a kill at the wrong moment "leads to data loss". On limits: "They are unspecified… they are flexible constraints". "Hitting rate limits does not lead to bans. Ignoring them does." A 429 "can happen anytime", on any method | **Advance the update offset only after the update's outcome is committed** (§4.1). **Process updates one at a time.** Accept at-least-once and make it harmless with idempotent rows. **Always obey `retry_after`**, on every method, and never rely on a fixed limit number |
| L3 | **OpenClaw**, an agent gateway with a Telegram channel: transports, messaging and troubleshooting docs, and the 2026.8.1 release notes (R) | The 2026.8.1 notes list what broke in production: "Prevent Telegram messages from being lost after restarts", "Prevent duplicate Telegram replies", "Keep concurrent Telegram updates in the correct session", "Keep Telegram reactions and poll answers in their original topics", "Recover stalled Telegram media downloads without blocking later messages", "Prevent stalled Telegram responses from blocking groups". Their fixes: updates are "saved to a durable queue and processed in order for each chat and topic"; the restart position is saved "after an update is committed to the durable ingress queue"; polling is rebuilt "after 120 seconds without completed long-poll liveness"; "timeouts after sending remain ambiguous to avoid duplicate replies". In the same release the **default changed** from streaming the answer into an edited message to "one editable status draft during the turn" with "the final answer as a normal message" | **Same shape, arrived at independently:** durable inbound, order per topic, the final answer as a normal message, status as a separate throwaway thing. **Slow work (downloads, transcription) never runs on the poll loop** (§4.3). **A stall watchdog** (§4.5). **An ambiguous send is tracked as its own state** and re-sent once with a note, because for an away-from-keyboard user a lost reply is worse than a rare duplicate (§4.1) |
| L4 | **A developer's flood-control write-up** (ai-muninn, R) | A bot "streamed" a slow model's answer by editing one message every 0.8 s for 175 s. Telegram answered "flood control, waiting 247.0s", and then the **final answer could not be sent either**: "by then it isn't just edits getting blocked". Extra edits with no new text failed with "Message is not modified" | **Decoration must never be able to block a reply.** Progress is shown in a draft, which has its own limit (§2). Where an edited message is the fallback, edits are at least 3 s apart, skipped when the text is unchanged, and **dropped for the rest of the turn on the first 429**, never queued |
| L5 | **Claude Code channels** (Anthropic's Telegram and Discord plugins): official docs (R), a launch-week write-up (R, third party), and the plugin's issue list (S) | At launch a write-up reported that "there is no way to respond to permission prompts from Telegram… If Claude hits a permission gate, it stops", so people ran with every permission check turned off. The docs now describe relaying prompts and warn: "Anyone who can reply through the channel can approve or deny tool use in your session". Issue titles (S) describe the poller: "polling exits permanently on network errors", "poller gives up on 409 after ~28 s and never recovers, while /mcp still reports 'connected'", "duplicate bot processes across sessions, causing message loss", "inbound messages silently dropped when… busy" | **Approvals are in stage 1**, because a remote control that cannot approve pushes people to turn safety off. **Only the paired numeric user id can approve.** **The poll loop never exits:** every error is retried with backoff, including 409. **One poller, enforced by a database lease.** **"Connected" means a recent completed poll**, not "the process is up" (§4.5) |
| L6 | **OpenClaw security advisories**: GHSA-j4c9-w69r-cw33 via a CVE report (R, third party); a February 2026 fix list (R, third party); an allowlist advisory (S); Zenity Labs research (R) | (a) "unauthorized users… send inline button callbacks that mutate session state, circumventing the direct message pairing restrictions": the button path skipped the check the message path had. (b) The bot "would download and write the media to disk before checking if the sender was authorized". (c) (S) An allowlist that matched changeable usernames instead of numeric ids. (d) An injected instruction made the agent "establish a backdoor… by adding a new chat integration under their control" | (a) **One authorisation gate for every update type**, before any handler, with a test per type (§5). (b) **Nothing is downloaded before that gate passes.** (c) **Numeric `from.id` only.** (d) **The agent cannot pair, unpair or change the allowlist:** the bridge exposes no tools, and pairing codes come only from a logged-in person (§5) |
| L7 | **Telegram's own bug tracker** (R) and **tdlib/telegram-bot-api #847, #905** (summaries, §2) | An open report: in a private-chat topic the "typing…" indicator "renders in parent chat instead of DM topic" on macOS and iOS, while Desktop and Web are correct. #905: a new private-chat topic sometimes refuses every bot write | **Check on the real phone before building** (TB-0). **A broken topic is marked broken and reported**, never silently redirected (§4.4) |
| L8 | **Hermes Agent** docs (R, earlier pass) and a Habr post on Threaded mode (R, third party) | Hermes: private-chat topics need Threaded Mode in BotFather, and without it the bot logs "The chat is not a forum". Habr (in Russian; my paraphrase): after turning Threaded mode on, a bot that does not send into a `message_thread_id` most likely stops working. Drafts have no `message_id` and disappear when the app redraws, so the real message must still be sent | **Pairing checks `has_topics_enabled` and stops with the one BotFather step if it is off.** **A draft is only ever decoration**; the reply is always a normal message |

### Patterns worth copying

| # | Source | Pattern | Where it lands |
|---|---|---|---|
| P1 | Brandur Leach, "Transactionally Staged Job Drains in Postgres" (R) | Enqueue-after-commit can lose work: "you run the risk of your program crashing after the commit, but before the job makes it to the queue… you almost certainly won't notice". The fix is to write the work item in the same transaction as the data | The inbound `Message`, its queued `Turn` and the `TelegramInbound` row commit together. Outbound sends are ledger rows, written before the send |
| P2 | Slack Events API docs (R) | Acknowledge within three seconds, do the work afterwards, and expect retries: "we'll retry three times", each with a retry-number header and a stable `event_id` | Answer a button tap at once and do the work after. Dedupe updates by `update_id` |
| P3 | Telegram callback behaviour (S; one developer measured about 15 s) | An unanswered button tap expires and the answer call then fails with "query is too old" | Call `answerCallbackQuery` first. Treat a late-answer error as harmless once the decision is recorded |
| P4 | Write-ups from people who built phone remotes for coding agents (S) | "The phone never talks to the agent directly. It talks to a dumb inbox." Approvals only became usable with push notifications. Notify only for "finish, approval, and error" | The bridge is exactly an inbox and a mirror. **Replies and approval cards notify. Progress, "Thinking…" and mirrored web messages are silent** (§4.1) |
| P5 | Matrix bridges (matterbridge commit notes, S; [Known] for mautrix) | Keep a map from each side's message id to the other's, or edits, replies and deletes cannot be matched | The delivery ledger stores the Telegram `message_id` for every room message it sends |

### What I looked for and did not find
- A public post-mortem of a Telegram bridge losing an **approval** decision. The
  closest are L5 and L6.
- First-hand detail on the Claude Code plugin's polling bugs: I saw issue titles and
  summaries on a mirror site, not the issues themselves.
- Any official statement of Telegram's edit limits (§9).

### Sources

Read directly (R):
- Libera.Chat, "Matrix Bridge Temporary Shutdown, a Retrospective", 2023-08-10:
  `libera.chat/news/matrix-bridge-disabled-retrospective`
- Matrix.org, "Shutting down the Matrix bridge to Libera.Chat", 2023-11-28:
  `matrix.org/blog/2023/11/28/shutting-down-bridge-to-libera-chat/`
- grammY, "Scaling Up III: Reliability" and "Scaling Up IV: Flood Limits":
  `grammy.dev/advanced/reliability`, `grammy.dev/advanced/flood`
- OpenClaw docs: `docs.openclaw.ai/channels/telegram/transports`, `/messaging`,
  `/troubleshooting`, `/threads-and-sessions`, `/rich-messages`, and
  `docs.openclaw.ai/releases/2026.8.1/messaging`
- ai-muninn, "Progressive Streaming on a Slow Model Got My Bot Rate-Limited by
  Telegram": `ai-muninn.com/en/blog/telegram-streaming-flood-control-slow-llm`
- Claude Code channels: `code.claude.com/docs/en/channels`; launch-week write-up at
  `dev.to/ji_ai/claude-code-channels-vs-openclaw-the-tradeoffs-nobodys-talking-about-2h5h`
- GHSA-j4c9-w69r-cw33 report on dev.to (`cverports`); the February 2026 fix list at
  `insiderllm.com/guides/openclaw-security-february-2026/`; Zenity Labs, "OpenClaw or
  OpenDoor?" at `labs.zenity.io`
- Telegram: `core.telegram.org/api/forum`, `core.telegram.org/api/bots/ai`,
  `telegram.org/tos/bot-developers` (§6.2.6), `bugs.telegram.org/c/62822`
- Habr, article 984554 (Threaded mode and drafts)
- Brandur Leach, `brandur.org/job-drain`; Slack, `docs.slack.dev/apis/events-api/`

Search summaries only (S): the Claude Code plugin issue list (a mirror at
`claudeissues.com`), the OpenClaw username-allowlist advisory (GHSA-mj5r-hh7j-4gxf),
phone-remote write-ups (`dev.to/yureki_lab`, `tacticremote.com`), a callback-timing
gist, and matterbridge commit notes.

---

## 3. Architecture

### 3.1 Components

**Two layers.** The delivery layer is the part that must not fail quietly. The live
layer is decoration: if it breaks, the delivery layer is unaffected.

```
Backend/telegram_bridge/            (new Django app; not a connector, not offered to the model)

  Delivery layer (stage 1)
  bot_api.py      Minimal Bot API client: httpx, token never in errors or logs,
                  ok:false parsing, 429 retry_after on every method, one sender per chat.
  poller.py       getUpdates long poll (timeout 50 s): lease, offset saved after commit,
                  webhook check, stale-update cut-off, retry forever, stall watchdog.
  gate.py         The one authorisation gate every update passes first (§5).
  inbound.py      Update → gate → topic binding → command (/pair /unpair /status
                  /stop /help) or chatbot.inbound.submit_user_message.
  mirror.py       The mirror loop: room messages → delivery ledger → Telegram, in
                  order, per topic. Also drives approval cards and the ending safety net.
  approvals.py    Card rendering, tap validation, PIN challenge, decision →
                  submit_user_message("yes"/"no", decision_for=…), or
                  submit_execution_approval for workflows (stage 2).
  pairing.py      Code issue and verify.
  health.py       last-poll and last-send times, /status text, alerts.
  lifespan.py     start() / stop(), called from Backend/asgi.py lifespan when
                  TELEGRAM_BRIDGE_ENABLED.
  management/commands/telegram_pair.py     Prints a one-time code locally.
  management/commands/telegram_unpair.py   Revokes a chat link.

  Live layer (stage 1: "Thinking…" + Stop; stage 3: progress lines)
  working.py      Per running turn: a draft with a Stop button, or "typing…" where
                  drafts don't render. Holds no durable state.

  Media (stage 2)
  files.py        getFile download → chatbot.documents.ingest_room_document.
  voice.py        Voice note → transcript (ear adapter) → submit_user_message.

Backend/chatbot/inbound.py          (new, extracted from ChatConsumer.new_message)
  submit_user_message(room, user, text, *, via, decision_for=None,
                      consent_eligible=True, within_txn=None)
Backend/chatbot/documents.py        (new, extracted from upload_document_to_ai)
  ingest_room_document(room, user, data, filename, mime) -> DocumentUpload
Backend/orchestration/approval_view.py   (new, read-only)
  pending_agent_approval(room_id, user_id) -> {pk, turn_id, short_id, message,
                                               effects, tools, risk}
```

**Why messages come from the database and not from the stream**
- **It is what makes "no silent drops" provable.** A room message either has a
  delivery row marked sent, or it is visibly pending (L1).
- **The stream is at-most-once.** `group_send` drops silently when a channel is full,
  and lite's layer lives in one process's memory (§1.5).
- **It needs nothing that does not exist yet.** Replies, turn notices and reminders
  are already saved as room messages through `persist_kazi_message`
  (`chatbot/kazi_messages.py:125-189`; turn notices at `orchestration/turns.py:952`,
  `orchestration/coordinator.py:332`, `chatbot/turn_runner.py:971`) [Verified].
- **It mirrors everything for free:** replies, endings, reminders, nudges and what you
  typed on the web.

**Why not the connector?**
- Boundary rule 1 forbids connector files from importing six named core modules: `agent_loop`, `tool_executor`, `tool_router`, `security_policy`, `action_receipts`, `workflow_planner` (`scripts/check_boundaries.py:28-35`). The bridge needs several of them.
- `docs/golden-principles/connectors.md:7` forbids calling other apps' models.
- The bridge must drive rooms and turns, which needs both.

**Built from scratch.** `bot_api.py` is new code and does not reuse the v0.4.2
connector. The connector's error handling is not reusable (§1.1). The connector's
future is D13.

### 3.2 Where the bridge runs

- **Recommended: a lifespan task in the ASGI web process**, started next to
  `InProcessScheduler` (`Backend/asgi.py:87-91`) and stopped before
  `TurnRunner.shutdown` (`:96-121`).
  - **Lease.** With several web workers (`--workers 4`, `docker-compose.yml:39`), each
    worker tries to take a database lease row (`TelegramPollerState`, modelled on
    `turn_leases`). Only the holder polls. A lost lease stops polling at once.
  - **Turns** the bridge submits are drained by that worker's `TurnRunner`, the same as
    turns from that worker's WebSocket clients.
  - **Lite** (`./kazi`, one uvicorn worker): works unchanged. There is no new process,
    no compose change and no supervisor change.
- **The alternative and its cost:** see D3.

### 3.3 Data model (new app; migrations are protected, so plan first)

All rows are append-style. Revocation sets `revoked_at` and never deletes.

| Model | Fields | Constraints |
|---|---|---|
| `TelegramChatLink` (the allowlist) | `user` FK, `chat_id` BigInt, `chat_type` (private/supergroup), `telegram_user_id` BigInt (must equal `from.id` on every update), `system_thread_key` BigInt (the bot's own "Kazi" topic for notices), `paired_at`, `revoked_at`, `revoked_reason`, `pin_hash` (Django `make_password`, nullable), `pin_failures`, `locked_until` | unique `(chat_id)` where `revoked_at IS NULL` |
| `TelegramRoomBinding` | `link` FK, `room` FK `Chatroom`, `thread_key` BigInt (the topic's `message_thread_id`), `label` (topic name), `state` (active / paused / broken), `bookmark_message_id` BigInt (every room message up to here is delivered), `created_at`, `revoked_at` | unique `(link, thread_key)` where active; unique `(link, room)` where active |
| `TelegramPairingCode` | `code_hash` (sha256), `user` FK, `issued_at`, `expires_at`, `used_at`, `used_by_chat_id` | single use |
| `TelegramPollerState` (singleton) | `offset` BigInt, `bot_id` BigInt (pinned at first `getMe`), `lease_owner`, `lease_expires_at`, `last_poll_ok_at`, `last_send_ok_at`, `conflict_since`, `last_error` | one row |
| `TelegramInbound` | `update_id` unique, `chat_id`, `message_id`, `kind`, `received_at`, `state` (`accepted` / `rejected:<reason>` / `media_pending` / `media_done` / `media_failed`), `kazi_message_id` | dedupe, audit and the queue for slow media work; holds no message text. An `accepted` row is written only in the same transaction as its `Message` (§4.1) |
| `TelegramDelivery` (the ledger) | `binding` FK, `kind` (`message` / `ending` / `notice`), `kazi_message_id` (nullable), `turn_id` (nullable), `part` (0, 1, … for long messages), `state` (`pending` / `sending` / `sent` / `failed`), `attempts`, `telegram_message_id`, `sent_at`, `last_error` | unique `(binding, kazi_message_id, part)`; unique `(binding, turn_id)` for `ending`. Holds no message text |
| `TelegramApprovalCard` | `token_hash`, `binding` FK, `approval` FK `WorkflowApprovalRecord`, `turn_id` UUID, `short_id`, `chat_id`, `message_id`, `risk`, `render` (buttons/widget), `created_at`, `decided_at`, `decision`, `decided_by_telegram_user_id`, `closed_at`, `outcome_message_id` FK `Message` | conditional UPDATE on `decided_at IS NULL`; at most one open card per `(binding)` |

**Notes**
- **One binding per topic, one topic per room.** There is no `/room` switching.
- **The ledger is the proof of delivery.** It also maps each room message to its
  Telegram `message_id` (P5), which later features need (reply threading, edits).
- **Progress and "Thinking…" have no table.** They are decoration and are rebuilt
  from the `Turn` rows after a restart.
- **No changes to `Chatroom`, `Message` or `WorkflowApprovalRecord`.**
  - "Via Telegram" is recorded as a `via` key inside the user message's encrypted JSON
    payload. `history.decrypt_stored` reads only `content`, so the extra key is
    harmless. [Inference, from `chatbot/history.py:33-67`; confirm during TB-1.]
  - It is also recorded on `TelegramInbound` and `TelegramApprovalCard`.
- **Audit chain:** card row, then `Message`, then `Turn`, then the `ActionReceipt` with
  `basis="human"`.

### 3.4 Config and environment

| Variable | Default | Purpose |
|---|---|---|
| `TELEGRAM_BOT_TOKEN` | (exists) | Bot token. Also add it to `.env.example`, which is a protected path. |
| `TELEGRAM_BRIDGE_ENABLED` | `false` | Start the lifespan task. |
| `TELEGRAM_BRIDGE_POLL_TIMEOUT_SECONDS` | `50` | getUpdates `timeout`. The httpx timeout is this plus 15. |
| `TELEGRAM_BRIDGE_LEASE_SECONDS` | `60` | Poller lease. Renewed on every loop. |
| `TELEGRAM_BRIDGE_STALL_SECONDS` | `120` | No completed poll for this long: rebuild the HTTP client and report (L3). |
| `TELEGRAM_BRIDGE_MAX_UPDATE_AGE_SECONDS` | `600` | Inbound text or taps older than this are not acted on; the user is told. |
| `TELEGRAM_BRIDGE_MIRROR_ACTIVE_SECONDS` | `1` | Mirror tick while any bound room has a queued, running or paused turn. |
| `TELEGRAM_BRIDGE_MIRROR_IDLE_SECONDS` | `5` | Mirror tick otherwise. |
| `TELEGRAM_BRIDGE_CATCHUP_MAX_MESSAGES` | `20` | After downtime, send at most this many waiting messages per topic and summarise the rest (D25). |
| `TELEGRAM_BRIDGE_MIRROR_WEB_MESSAGES` | `true` | Show messages typed on the web in the room's topic. |
| `TELEGRAM_BRIDGE_PAIRING_CODE_TTL_SECONDS` | `600` | Code lifetime. |
| `TELEGRAM_BRIDGE_ALLOWED_TELEGRAM_USER_IDS` | empty (any user may pair) | Optional static allowlist: who may pair at all. |
| `TELEGRAM_BRIDGE_PIN_POLICY` | `high_risk` | `off`, `high_risk` or `all`. Applies once a PIN is set. |
| `TELEGRAM_BRIDGE_CONTENT_MODE` | see D5 | `full`, `summary` or `notify_only`. |
| `TELEGRAM_BRIDGE_WORKING_UI` | `draft` | `draft`, `typing` or `off`. Set from the TB-0 result (D10). |
| `TELEGRAM_BRIDGE_PROGRESS_MIN_SECONDS` | `3` | Least time between progress updates (stage 3). |
| `TELEGRAM_BRIDGE_UI_DEFAULT` | `buttons` | `buttons` or `widgets` for approval cards (D22). |

`kazi_status --json` reports whether the bridge is enabled, the lease holder,
`last_poll_ok_at`, `last_send_ok_at`, the number of pending deliveries and `last_error`,
so `./kazi status` can show it (§4.5).

### 3.5 Multimodal: voice, photos and files

**Rule for this section: build what the platforms handle well today.** Each choice
below rests on a provider or Telegram doc read on 2026-11-08.

**Sources and how far to trust them**
- **Official pages I fetched and read:**
  - OpenAI: `developers.openai.com/api/docs/` — `deprecations`, `guides/text-to-speech`,
    `guides/speech-to-text`, `guides/audio`, `guides/voice-agents`,
    `guides/audio-chat-completions`, `models/gpt-audio`.
  - DeepSeek: `api-docs.deepseek.com` — `guides/vision/`, `quick_start/pricing`,
    `updates/`.
  - Telegram: `core.telegram.org/bots/api`.
- **Anthropic rows:** a research pass fetched and read `platform.claude.com/docs/en/build-with-claude/vision`,
  `/pdf-support` and `/models/overview`.
- **Rows marked (S):** search-result summaries only.

#### What each provider does natively

| Capability | Anthropic | OpenAI | DeepSeek | Kazi today [Verified in code] |
|---|---|---|---|---|
| Image input to the chat model | Yes, all current models; JPEG/PNG/GIF/WebP; base64, URL or file id | Yes (S) | **Only `deepseek-flash`**, via the OpenAI-style `image_url` part (vision guide). `deepseek-v4-pro` lists no image input | **No path.** The user message is always a string (`agent_loop.py:2924`). The picker already offers "DeepSeek Flash Vision" (`model_catalog.py:31`, `tier="vision"`), which cannot receive images through Kazi today. The DeepSeek adapter turns unknown blocks into `str(block)`, which would put base64 into the prompt (`llm_client.py:420-421`) |
| PDF input | Native | Native (S) | Not confirmed | `pypdf` text from the first 10 pages (`chatbot/tasks.py:976-1000`) |
| Audio input to the chat model | No | `gpt-audio-1.5` in Chat Completions (examples use wav). The `gpt-audio-2025-08-28` snapshot is marked Deprecated | No | **Kazi has no OpenAI chat provider.** Providers are Anthropic, DeepSeek and Hugging Face (`llm_client.py:29-34`) |
| Speech-to-text | None | `gpt-transcribe` recommended. Files up to 25 MB; "mp3, mp4, mpeg, mpga, m4a, wav, and webm": **no ogg**. `whisper-1` and `gpt-4o-*-transcribe` are removed **2027-02-26** | None | `whisper-1` (`chatbot/tasks.py:264`). It never starts a turn and stores the transcript in plaintext (`:268-270`) |
| Speech output | None | `tts-1`, `tts-1-hd` and `gpt-4o-mini-tts` are removed **2027-01-06**. The named replacement, `gpt-realtime-2.1-mini`, "isn't a supported model for /v1/audio/speech" (Realtime only) | None | Two paths, both on models being removed. `generate_voice_response` uses `tts-1` and writes mp3 (`chatbot/tasks.py:334-349`). The `generate_speech` connector uses `gpt-4o-mini-tts` and returns base64 that nothing delivers (`orchestration/connectors/tts_connector.py`) |
| Image generation | None | Yes (S) | None | None. Out of scope |

#### What Telegram does natively [Verified: `core.telegram.org/bots/api`]

- **Voice replies.** `sendVoice` plays as a voice bubble if "your audio must be in an .OGG
  file encoded with OPUS, or in .MP3 format, or in .M4A format". The limit is 50 MB.
  - So an MP3 from text-to-speech **should** play as a voice note without conversion
    [Unconfirmed until tested on real clients].
  - Hermes Agent's docs still say MP3 needs ffmpeg for a voice bubble. I could not find
    when Telegram changed this in the changelog, so test it once on real clients.
- **Downloads and photo sends.** `getFile` downloads up to 20 MB. `sendPhoto` takes up
  to 10 MB.
- **Albums** arrive as separate updates that share `media_group_id`.
- **Chat actions.** `sendChatAction` shows `typing`, `record_voice`, `upload_voice` or
  `upload_photo` "for 5 seconds or less", so repeat it about every 4 s while working.
- **Streaming drafts.** `sendMessageDraft` is "a temporary 30-second preview". The final
  text must still be sent with `sendMessage`. The bridge uses a draft only for
  "Thinking…" and progress (§4.5), never for the reply.

#### Architecture: a chained voice pipeline, with voice models at the edges

The agent stays a **text** agent on whichever provider the room uses. Voice is
converted at the two ends:

```
voice note ─► ear (speech-to-text) ─► Kazi turn (any provider, text) ─► text reply ─► mouth (speech) ─► sendVoice
```

**Why not a native speech-to-speech agent for Telegram:**
1. **OpenAI recommends this split for an existing text agent**, which Kazi is
   [Inference for the mapping]. Its audio guide maps "a voice
   interface for an existing text agent" to "speech-to-text, the text-agent workflow,
   then text-to-speech". Its voice-agents guide says the chained path is for when "you
   want to inspect or transform text between" stages.
2. **Kazi's governance runs on text:**
   - exact whole-message approvals (`orchestration/shell/chat_intents.py:77-107`);
   - the injection check on the query (`tool_executor.py:117-123`);
   - history, receipts, and v0.8's verification layer.
3. **Telegram voice notes are recorded clips, not a live call.** Native speech-to-speech
   wins on latency, interruption and full duplex. None of that applies here.
4. **Only OpenAI has native audio models,** and Kazi does not run OpenAI as a chat
   provider. Going native would tie every voice room to one provider and add a new one.
5. **OpenAI's audio models are churning.** Three removal dates fall between
   2027-01-06 and 2027-02-26. Keeping the ear and mouth as small swappable adapters
   absorbs that.

**Where a native voice model fits later: live calls**, the "Live calls (V-5)" track in
#303. OpenAI's voice-agents guide describes GPT-Live with **client delegation**: "Connect
your own agent or workflow, using the backend model and provider you choose… your
application controls permissions and business records." For Kazi, the voice model would
talk and Kazi's agent would stay the backend, with approvals and receipts unchanged.
This does not apply to Telegram, which has no calls in the Bot API [Known].

#### What to build, and what not to

| Piece | Build | Pattern borrowed |
|---|---|---|
| **Voice in** | 1. Download the voice note and send `record_voice` while working. 2. Convert OGG/Opus to WebM by **stream copy, with no re-encode** (`ffmpeg -c:a copy`). WebM is on OpenAI's list and ffmpeg is already in the image (`Dockerfile:13-18`) [Inference: Opus in WebM is a valid stream copy; test it]. 3. Pass it to the **ear adapter**. 4. **Echo the transcript** ("🎙 heard: …"). 5. Submit it through `submit_user_message(via="telegram_voice")`. Cap length at about 5 minutes. On failure, give an honest reply and start no turn. | Hermes Agent: transcribes and injects as text, with STT providers `local` (faster-whisper), `groq` and `openai`. OpenClaw: the transcript "replaces the message body", so commands still work |
| **Ear adapter** | One interface. `STT_PROVIDER=openai` (model from config, default `gpt-transcribe`) or `openai_compatible` with `STT_URL`, which covers a local faster-whisper server or Groq. This is the same shape as the TTS connector's `openai_compatible` provider (`tts_connector.py:29-45`). | Hermes provider switch |
| **Voice is not consent** | A transcribed "yes" must **not** approve or continue a pause, because of mis-hearing and background speech. The seam passes `consent_eligible=False` and the coordinator skips approval and continue matching for it. Buttons are the way to approve | OpenClaw frames voice transcripts as "machine-generated, untrusted text in agent context" |
| **Voice out** | **Reply in kind:** speak the reply only when the user sent a voice note, plus a `/voice on`, `/voice off` or `/voice always` toggle per binding. The **text reply is always sent too**, because text is the record. Speak only short replies (cap about 600 characters); longer ones go as text with a note. The **mouth adapter** (`TTS_PROVIDER` / model from config) sends MP3 straight to `sendVoice`, falling back to an OGG/Opus encode only if clients don't render a bubble. It replaces both existing TTS paths, and audio bytes never enter the model context. | Hermes: `/voice on` "sends voice replies only when you send a voice message" |
| **Photos, stage 1 (bridge)** | Store the photo in the room and answer honestly: "📷 saved. This room's model can't see images yet." Any caption runs as text. Group albums by `media_group_id` (collect for about 1 s, then treat as one message). | — |
| **Photos, stage 2 (core, plan first)** | **Describe-first pre-pass:** one call to a configured `VISION_MODEL` (`claude-haiku-*` or `deepseek-flash`). The description goes into `DocumentUpload.processed_text`, which today holds only "Indexed image metadata." (`chatbot/tasks.py:1003-1018`). It is **framed as untrusted** text, because images can carry instructions, which closes G9 for images. It is cached by `file_unique_id`. It works for every provider and for web uploads too, with no agent-loop change. | OpenClaw: a text-only main model gets a separate image model's description, and descriptions are cached "to reduce repeated vision calls" |
| **Photos, stage 3 (only if stage 2 isn't enough)** | Native image blocks: an attachments parameter through coordinator and `run_agent_loop` (protected), per-provider adapters, `ModelInfo.vision`, and base64 kept out of history and paused state (`agent_loop.py:1269-1300`, `:192-196`). | — |
| **Documents** | The existing PDF path. | — |
| **Not building** | Speech-to-speech agent turns over Telegram; image generation; video and video-note analysis (store and mark "not analysed"); live calls; a local GPU transcription default (possible through `openai_compatible`). | — |

### 3.6 Newer Bot API features we use (no Premium)

Re-checked on 2026-11-13 against `core.telegram.org/bots/api`, `/bots/api-changelog`
and `/bots/widgets`. Bot API 10.4 shipped on 2026-11-09.

**No Premium dependency (D21).** Nothing in this design needs Telegram Premium.
[Verified] Premium adds only:
- custom emoji in the bot's messages and buttons, when the bot's owner has Premium;
- usernames ending in `ai` or `agent` for up to 2 bots (10.4);
- larger uploads from Premium users. The bot still downloads at most 20 MB per file
  through `getFile`.

Voice transcription is a feature of the Telegram app; the Bot API has no transcription
method. Private-chat topics and Business bots don't need Premium.

**Widgets (10.4): optional upgrade for approval cards and status, private chats only.**
[Verified: `/bots/widgets`]
- **What a widget is.** One self-contained HTML document with inline CSS and
  JavaScript: at most 32 KB, and at most 4 KB once zlib-compressed.
- **Sandboxed.** The JavaScript runs in QuickJS, not a browser. `fetch`, WebSocket and
  every other network API throw. External resources never load. The widget can't
  navigate except through `openLink` on a tap.
- **Fixed code.** The code is set when the widget is sent and never changes. Only its
  data changes, through `editMessageWidgetData`: `chat_data` up to 1 KB per message,
  `public_data` up to 1 KB.
- **One way out.** The widget's only output is a request to the bot of at most
  64 bytes, made synchronously in a tap handler, one call per tap. It arrives as a
  `widget_query`, limited to 5 queries per 5 seconds per user. The bot must answer
  it within 15 seconds with `answerWidgetQuery`.
- **Can't be forwarded.** In production, widgets work only in private chats and are
  protected content.

**How the bridge uses widgets:**
- **Approval widget.** One static template ships in the repo. The card state (redacted
  summary, risk, pending/approved/expired) lives in `chat_data`, and the widget renders
  it with `textContent`, never `innerHTML`.
  - Approve/Reject send `ka:<token>:y|n`, the same ≤64-byte value as the button
    `callback_data`. That value goes into the **same handler** as a button tap, so
    every check in §4.2 applies unchanged: `from.id`, chat, `message_id`, token and
    current-row match, PIN in the private chat, one transaction.
  - The handler then calls `answerWidgetQuery`, and updates the card with
    `editMessageWidgetData`.
- **Status widget.** One per run, in place of the "Thinking…" draft, where widgets
  are switched on. Phase, tools and elapsed time live in `chat_data`, updated at most
  every `PROGRESS_MIN_SECONDS`. It is decoration like the draft (§4.5): an error stops
  it and never delays a reply. The final reply is still a normal message, so it
  triggers a notification and stays readable.
- **Fallback is mandatory.** Group mode, bindings with widgets switched off, and
  any `sendWidget` error get the plain inline buttons and the draft or "typing…".
  - [Cannot verify] how well each Telegram app supports widgets yet; the feature is
    four days old. The default is therefore buttons, with widgets opt-in per binding
    (`/ui widgets`) after a test widget renders on your device (D22).
- **Truth.** Telegram still sees everything the widget shows. The data limit and
  redaction rules are the same as for text. "Can't be forwarded" makes it harder to
  leak a card by mistake; it does not hide anything from Telegram.

**Links straight to a room's topic (10.4).** [Verified] `https://t.me/<bot_username>/<message_thread_id>`
opens that topic, but only for the user whose private chat holds it. The web room page
can show "Open in Telegram" for every bound room, since each room is a topic (D4).

**Ephemeral replies to button taps (10.4).** [Verified] A message shown only to the
user who tapped, in any chat except channel direct-message chats. These messages "may
disappear automatically after some time, or if the app is restarted", so they are used
**only for throwaway notes**, never for cards or anything durable. Examples:
- "This approval is no longer current."
- "Locked for 15 minutes after 5 wrong PINs."
- "This card belongs to another user" (group mode).

In groups this keeps those notes from cluttering the chat for everyone else.

**Data model and config changes:**
- `TelegramApprovalCard` has `render` (`buttons` / `widget`).
- `TelegramRoomBinding` gains `ui` (`buttons` / `widgets`).
- New setting `TELEGRAM_BRIDGE_UI_DEFAULT` (default `buttons`).
- `allowed_updates` adds `widget_query`.

---

## 4. Sequences

### 4.1 A message in, and everything out

**In: from the phone to the room**

```
Phone        Telegram          Bridge (poller task in web process)                    Kazi core
  |--text-->|                    |                                                      |
  |         |<--getUpdates(offset, timeout=50, allowed_updates=[message,callback_query,
  |         |                   |              stopped_message_generation])              |
  |         |--update---------->|   one update at a time, in order                      |
  |         |                   | 1 skip if TelegramInbound(update_id) already has a result
  |         |                   |   (a row exists only once its outcome committed)
  |         |                   | 2 THE GATE (§5), same for every update type:
  |         |                   |   link = active TelegramChatLink(chat.id);
  |         |                   |   require from.id == link.telegram_user_id; else drop, silent
  |         |                   | 3 drop if: date older than MAX_UPDATE_AGE (tell the user);
  |         |                   |   from.is_bot; sender_chat set; via_bot; guest_query_id;
  |         |                   |   forward_origin set (D6); no text/caption/file
  |         |                   | 4 binding = (link, message_thread_id) → room
  |         |                   |   no binding and the topic is new → §4.4 (new topic = new room)
  |         |                   | 5 user_has_room_access(link.user, room)  [a DB error raises: catch it and deny]
  |         |                   | 6 bridge rate limit (cache.add, closed on error)
  |         |                   | 7 submit_user_message(room, user, text, via=telegram,  -->|
  |         |                   |     within_txn=record TelegramInbound(accepted, msg id))
  |         |                   |                                  mute, length, encrypt+save,
  |         |                   |                                  moderation, directives,
  |         |                   |                                  [one transaction: Message +
  |         |                   |                                   queued Turn + within_txn]
  |         |                   |                                  then runner.submit after commit
  |         |                   |   (a step 3-6 rejection records TelegramInbound(rejected:…) on its own)
  |         |                   | 8 save offset = update_id + 1                          |
```

**Why the order matters.** The offset is saved after step 7 commits, never before
(L2, L3). So a crash replays the update, and step 1 decides what the replay does:
- **Crash before the commit:** there is no row and no message. The replay processes the
  update normally.
- **Crash after it:** the message and its queued Turn exist, so the replay is skipped.
  The queued Turn is drained or, if this process died, reaped with #303's saved
  "waiting messages weren't answered" notice. That notice is a room message, so the
  mirror delivers it.

This is at-least-once delivery made harmless by an idempotent row (P1).

**The poll loop does only fast work.** Filtering, one transaction and one enqueue.
Anything slow (a file download, a transcription) is recorded as `media_pending` in the
same way and done by a per-topic worker (§4.3), so one large voice note cannot hold up
other topics (L3).

**Out: the mirror loop, from the room to the phone**

```
every tick (1 s while a bound room has a queued, running or paused Turn; else 5 s):
  for each active binding B (room R ⇄ topic T):
    1 access   user_has_room_access(link.user, R). False or any error → set B paused,
               send nothing more, say so once in the system topic
    2 collect  R's messages with id > B.bookmark − 200 that have no TelegramDelivery
               row for B → create one row each:
                 came from this topic (TelegramInbound.kazi_message_id) → row already "sent"
                 a Kazi message (reply, turn notice, reminder)           → "pending"
                 typed on the web (by you or another member)             → "pending",
                                       shown as "🖥 <name>, on the web: …", sent silently
    3 send     pending rows in id order, one sender per chat:
                 set "sending" and commit → sendMessage(message_thread_id = T,
                     plain text, redacted again, parts of ≤ 4000 characters)
                 ok                     → "sent" + telegram_message_id + sent_at, commit
                 429                    → wait retry_after, then the same row again
                 failed before the request left → the same row again, with backoff
                 timed out after the request left (unknown) → stays "sending";
                     re-sent once after 30 s, prefixed "(re-sent)"
                 "message thread not found" → set B broken, stop, say so in the
                     system topic (never post into another topic instead)
                 any other 4xx          → "failed" + last_error; alert; go on to the next row
    4 bookmark B.bookmark = the highest id below which every row is "sent" or "failed"
    5 working  R has a running Turn → keep the "Thinking…" draft alive (§4.5); else stop
    6 cards    pending_agent_approval(R, user) → §4.2
    7 endings  a Turn of R that ended more than 10 s ago with no room message after
               its request → one "⚠️ Ended: <state>" line (ledger row, one per turn)
```

**What this guarantees**
- **At least once.** A room message is not past the bookmark until Telegram has
  confirmed it.
- **In order, per topic.** Rows are sent in id order by one sender.
- **Across restarts, with no special recovery code.** After a restart the same loop
  runs. `pending` rows go out. `sending` rows are the unknown case and are re-sent once
  with the "(re-sent)" prefix.
- **At most one duplicate per crash.** Telegram has no idempotency key for sends
  [Known], so a crash after Telegram accepts a message and before "sent" is saved
  re-sends it. A lost reply is worse than a rare, labelled duplicate (L3).
- **Nothing is skipped by a late commit.** Database ids can become visible out of
  order when two processes write at once [Known]. The loop therefore re-scans the 200
  ids before the bookmark, and the unique `(binding, message, part)` key makes the
  re-scan free.

**What it does not do**
- **It does not replay history at first bind.** The bookmark starts at the room's
  newest message; older history stays on the web.
- **It does not flood you after downtime.** If more than `CATCHUP_MAX_MESSAGES` are
  waiting for a topic, it sends "N earlier messages are on the web" and then the most
  recent ones (D25).
- **Edits made in Telegram are not applied.** `edited_message` is not requested in
  `allowed_updates`. Send a new message instead.

**Notifications** (P4). Replies, turn notices and approval cards notify. Mirrored web
messages, catch-up batches, "Thinking…" and progress are silent
(`disable_notification`).

**Truth.** The text sent is the persisted message, never the concatenated stream.
[Inference] Every turn ending I traced saves a room message, but I did not trace every
path, so step 7 is the safety net: a turn can never end without a line in Telegram.

### 4.2 Approval round trip (agent loop)

Cards are driven by the database, checked on every mirror tick (step 6). No stream
frame is needed.

```
 core:   run pauses → save_pending_confirmation (row R, created_at T0)
 mirror: a = approval_view.pending_agent_approval(room, user)
             → {pk, turn_id, short_id, message (redacted), effects, tools, risk}
         nothing pending:
             an open card for this binding → close it: remove the buttons, set the text
             from the row's final status ("Approved on the web 14:02", "Rejected", "Expired")
         pending, and the open card matches (pk, turn_id, short_id): nothing to do
         pending, and no matching card:
             close any other open card as "No longer current"
             token = secrets.token_urlsafe(12)            # 16 chars, about 96 bits
             store TelegramApprovalCard(token_hash, pk, turn_id, short_id, risk)
             sendMessage(card text, buttons):             # callback_data is 21 ASCII bytes
               low or medium risk      [Approve ka:<token>:y] [Reject ka:<token>:n]
               high risk, stage 1      [Approve on the web → link] [Reject ka:<token>:n]
               high risk, with a PIN   [Approve ka:<token>:y] → PIN step (stage 2, TB-12)

 phone:  taps Approve → callback_query
 bridge: 0 THE GATE (§5): the same check as for messages, before anything else
         1 answerCallbackQuery at once (clears the spinner). If Telegram says the query
           is too old, carry on: that error is harmless (P3)
         2 card by token_hash; decided_at IS NULL; age < MAX_UPDATE_AGE;
           callback.message.message_id == card.message_id and the same chat and topic
         3 current pending row for (room, user): same pk AND same turn_id AND same short_id
           (guards row reuse, agent_loop.py:839-851); else "This approval is no longer
           current" and close the card
         4 user_has_room_access
         5 if risk is high and a PIN is set: ask for the PIN in the private chat, in the
           card's own topic (group mode: in the private chat, and the card says "check
           your DM"); the challenge is bound to this card and its approval pk; the next
           message from that user there is the PIN (deleteMessage it at once, never
           submit it to the room); check_password; 5 failures → lock the link 15 min
           and send an in-app notification; if the private chat can't be reached →
           refuse, nothing approved
         6 re-run step 3 immediately before step 7, because the PIN round trip can take
           far longer than CONSENT_SKEW_SECONDS
         7 in ONE transaction (submit_user_message's within_txn):
             conditional UPDATE card SET decided_at, decision WHERE decided_at IS NULL
             (one winner on a double tap) + the decision Message + its queued Turn;
             runner.submit after commit
           submit_user_message(room, user, "yes" | "no", via=telegram,
             decision_for=(pk, turn_id),          # TB-16; the coordinator refuses a mismatch
             within_txn=decide card)
           → coordinator: consent check (Message.timestamp > T0 + skew), approve or decline
             routing, resume_after_confirmation (hash match, single-consumer UPDATE),
             receipts basis="human"
         8 the next mirror tick sees the row resolved and closes the card
           ("Approved via Telegram 14:02"). The same step closes cards decided on the web.
```

**Rejecting is always allowed from Telegram**, even for high-risk actions, because a
reject cannot cause harm. Only approving high-risk actions waits for the PIN ticket.

**Interrupted submissions.** Because the card decision and the decision message commit
together, there are two cases:
- **Crash before the commit:** the card is still undecided, so the user can tap again.
- **Crash after it:** the queued decision Turn is drained, or reaped with #303's saved
  notice. If it is reaped while the approval is still pending, the next mirror tick
  finds a pending approval with no open card and sends a fresh one. The old token stays
  used, so single use holds.

**Why the consent check alone does not protect a stale tap [Inference].** The "yes"
`Message` is saved at step 7, so its timestamp is when the bridge processes the tap.
Suppose a pause started from the web replaces R between steps 3 and 7, for example
while the user is typing the PIN. The "yes" then postdates the new row's `created_at`,
and `_consent_postdates_prompt` accepts it. The 2-second skew (`coordinator.py:170`,
`settings.py:545`) does not cover that gap. The existing test
`test_a_queued_yes_cannot_approve_a_prompt_it_never_saw` (`chatbot/test_turn_runner.py:680`)
covers a different case: a message saved *before* the prompt existed. So step 6
narrows the gap, and **TB-16 closes it**: the decision names the approval it answers,
and the coordinator refuses it if the pending row differs. **TB-16 is a hard
prerequisite of TB-11.**

**"Continue" buttons** (TB-11b) for budget and quota pauses follow the same card flow
and send `continue`. The consent check is the `paused_at` one from #306.

**Workflow approvals** (TB-15) follow the same card flow. Step 7 becomes
`submit_execution_approval(execution, approval_id=record.pk, reviewer_id=user.id, decision)`,
after `workflow__user=user` and `status="waiting"` checks copied from
`workflows/views.py:426-439`.

### 4.3 Files, photos and voice notes

```
 update.message.document | photo (largest size) | voice     (voice: §3.5; albums: TB-M3)
 on the poll loop (fast):
 1 the gate and filters, as in 4.1 steps 1-6. Nothing is downloaded before they pass (L6)
 2 check the declared file_size against Kazi limits (PDF 10 MB, image 5 MB) before
   getFile; Telegram caps downloads at 20 MB
 3 record TelegramInbound(media_pending) and save the offset
 off the poll loop (one worker per topic, in arrival order):
 4 getFile(file_id) → file_path; download https://api.telegram.org/file/bot<token>/<path>
   - streamed, size-capped, time-capped, URL never logged (it contains the token)
 5 sniff the type from bytes, never trust mime_type or file_name; store under a uuid name
 6 ingest_room_document(room, user, bytes, safe_name, sniffed_mime)
   → upload quota, DocumentUpload(pending), process_document_task
 7 reply "📎 report.pdf added"; if a caption is present: wait (bounded, 30 s) for
   status=completed, then submit_user_message(caption) so the turn's ContextManager
   includes the document
 8 mark media_done; on any failure mark media_failed and say so in the topic
   ("couldn't fetch that file; nothing was run")
 a restart finds media_pending rows and resumes them
```

Messages in one topic stay in arrival order: a text sent after a file waits for that
file's worker.

[Inference] Document text enters the context prompt (`chatbot/context_manager.py:155-167`).
I did not confirm that this path is marked untrusted, or tainted the way tool output is
(`docs/plans/170-taint-flag.md:15-16` lists inbound tainting as a non-goal). Listed as
gap G9, and TB-G9 answers it before files ship.

### 4.4 Pairing, topics and rooms

```
 once, by the bot's owner: in @BotFather turn on Threaded mode for the bot, and leave
         "Disallow users to create new threads" off (§2)

 laptop$ python Backend/manage.py telegram_pair --user alice
         → prints: "Send this to @YourBot within 10 minutes:  /pair K7QF-3MZD"
           (8 chars, Crockford base32, about 40 bits; stored as sha256 only;
           printed to stdout, not logged)
 phone:  opens the bot and sends "/pair K7QF-3MZD"
         (with Threaded mode on, Telegram puts this message in a new topic)
 bridge: 1 chat.type must be "private" (codes are never accepted in groups)
         2 from.id must be in ALLOWED_TELEGRAM_USER_IDS if that is set
         3 attempts ≤ 5 per chat per hour and ≤ 20 per bot per hour (closed on cache error)
         4 compare in constant time against unexpired, unused codes; mark used
           (conditional UPDATE)
         5 getMe.has_topics_enabled must be true; else reply with the one BotFather
           step and stop (L8)
         6 create TelegramChatLink(user, chat_id, telegram_user_id = from.id)
         7 the topic the code arrived in becomes the system topic: rename it "Kazi"
           (editForumTopic), store it as system_thread_key. Notices that belong to no
           room go here: bridge back online, a broken topic, an unexpected pairing
         8 reply there with the disclosure ("Telegram can read everything relayed
           here") and send an in-app notification "Telegram account <name> (id)
           paired", so an unexpected pairing is visible
         9 for each of the user's rooms: createForumTopic(chat_id, name = room name)
           → TelegramRoomBinding(thread_key, bookmark = the room's newest message);
           a test sendMessage into the topic; on "message thread not found" (#905)
           mark the binding broken and say so in the system topic

 a new task, from the phone (D23):
         the user types in the bot's main view → Telegram creates a topic with an
         implicit name → the bridge creates a Kazi room owned by the paired user, binds
         it, renames the topic to the room's title, and submits the message to the room.
         A replayed update creates no second room (the binding is unique per topic).
         If the first message is a bridge command (/status, /help), it is answered in
         the system topic and the empty new topic is deleted; no room is created
 a new room, from the web:
         the next mirror tick sees a room with no binding and creates its topic
 rename (forum_topic_edited): renames the binding's label only; the Kazi room keeps
         its own name unless D23 says otherwise
 closed (forum_topic_closed): the binding is paused; reopened resumes it
 deleted by the user: Telegram sends nothing (§2), so the next send fails "message
         thread not found"; the binding is marked broken, the system topic says so and
         offers /restore, which creates a fresh topic. The Kazi room is never deleted
 commands inside a room's topic: /stop (an exact stop, #306), /status, /help, /unpair

 groups (stage 4, optional): the user adds the bot to a private forum supergroup as
         admin with can_manage_topics; from that group the paired user sends
         /bind <room id> (from.id must equal the link's telegram_user_id) →
         createForumTopic → binding. The group needs its own TelegramChatLink row,
         created only by that command from an already-paired user, which the DM confirms.
```

[Cannot verify] How the Android app behaves in each of these steps. The forum page
says what clients "should" do. TB-0 checks every step on the real phone before any of
this is built. If typing in the main view does not create a topic there, the fallback
is the reverse setting: users may not create topics, General accepts messages, and a
`/new <name>` command starts a room.

### 4.5 "Thinking…", Stop, and knowing the bridge is alive

**While a turn runs** (live layer; no table, no recovery)
```
 mirror tick, step 5, for a room with a running Turn:
   WORKING_UI=draft:  sendMessageDraft(chat, topic, draft_id = one per turn, text = "",
                                       can_stop = true)   → "Thinking…" with a Stop button
                      repeat every 20 s (a draft lasts 30 s, §2)
                      stage 3: the text becomes the current progress lines, at most
                      one update per PROGRESS_MIN_SECONDS, skipped when unchanged
   WORKING_UI=typing: sendChatAction("typing", topic) every 4 s
   any error, including 429: stop decorating this turn. Never retry, never queue (L4)
 stopped_message_generation update (the Stop button) or "/stop":
   the gate → submit_user_message(room, user, "stop") → cancel_requested_at (#306)
 the turn ends: the draft is left to expire; the reply arrives as a normal message
```

**Health** (L1, L5)
- **The poll loop never exits.** Every error is retried with backoff (1 s doubling to
  60 s): network errors, 5xx, 429 and 409. A 409 means another poller holds the token;
  the bridge keeps retrying and raises an alert, and treats it as possible token theft
  (§5).
- **Stall watchdog.** No completed `getUpdates` for `STALL_SECONDS` → rebuild the HTTP
  client and record it.
- **Connected means a recent completed poll**, not "the process is running".
  `last_poll_ok_at` older than 2 × the poll timeout shows as "Telegram: not connected"
  on the web and in `./kazi status`, and sends an in-app notification after 5 minutes.
- **`/status` in any topic** answers: connected since, last message delivered, pending
  deliveries, broken topics.
- **Coming back.** After an outage the bridge posts one line in the system topic:
  "Back online. Offline 14:02–14:19. Messages you sent in that time were not run."
  (older than `MAX_UPDATE_AGE`), then the mirror catches up.
- **Shutdown.** Lifespan stop ends the poll, saves the offset, and stops before the
  turn runner shuts down.

---

## 5. Security analysis

| Threat | What an attacker gets | Mitigations |
|---|---|---|
| **Stolen bot token** | **Reading.** They can call getUpdates and consume or read incoming messages, which races our poller. **Sending.** They can send as the bot, for example fake approval cards. **Denial of service.** They can set a webhook, which stops our polling. [Inference] They cannot forge an update whose `from.id` is the paired user, because updates come from Telegram, and buttons they create carry tokens not in our database. **But they can tamper with a genuine card.** A holder who wins a getUpdates race sees the card's `chat_id`, `message_id` and callback data. With `editMessageText` or `editMessageReplyMarkup` they can change what the card says, or which button approves. They can also read the PIN message. | • Keep the token in env only. It is already excluded from the sandbox (`orchestration/shell_exec/backends.py:57-80`). Warn that the `open` shell profile runs on the host and can read `.env` (`docs/contracts/credential-scoping.md:84-90`). • The new `bot_api.py` never puts the URL in errors (TB-6). Never log file URLs. • Against card tampering: the callback's `message.message_id` must equal `card.message_id`, and the PIN or confirm step shows the action summary read from the database, not the card text. • Treat a 409 or a newly set webhook as possible compromise: disable every open card. • At startup call `getWebhookInfo`. If a webhook URL is set, refuse to poll, do not delete it, and send an in-app alert. • Pin `bot_id` from the first `getMe`; a changed id means the token was swapped, so stop. • Treat repeated 409 Conflict as "another poller holds this token": alert and back off. • Runbook: revoke in BotFather, rotate the env var, restart. |
| **chat_id spoofing** | There is no inbound endpoint (polling only), so nothing on the network can be spoofed. Inside Telegram, anyone can message the bot, and anyone in a group can post in a bound topic. | • Accept only `chat.id` with an active link **and** `from.id == link.telegram_user_id`, on every update and callback. • Reject `sender_chat` (anonymous admins, channels), `from.is_bot`, `via_bot`, guest updates. • Pairing codes: about 40 bits, 10 minutes, single use, hashed, private chats only, rate-limited per chat and globally. • Unpaired chats get no reply at all; only a valid `/pair` code is answered, so the bot's activity is not revealed. • An in-app notification on every pairing. `/unpair` from the phone; `telegram_unpair` locally. • Per-update `user_has_room_access`. |
| **Forwarded-message injection** | Text written by someone else arrives with the owner's authority. | • Reject `forward_origin` and `is_automatic_forward` messages by default, saying why (D6). • Read only `text` or `caption`. Ignore `external_reply`/`quote` content, or pass it as labelled quoted data, never as the instruction. • Downstream protections still apply because the text is the query: `should_block_action` on `raw_query` (`tool_executor.py:117-123`), capability gates, risk bucketing and human approval. Pasted text cannot be told apart from typed text, and that is accepted, as on the web. • File contents go to the document path, not the query. |
| **Approval replay or mis-binding** | Re-using an old tap or callback; a stale card approving a newer action; a double tap; someone else in a group tapping. | • A random 96-bit token per card, stored hashed, single use through a conditional UPDATE. • Bound to approval pk + turn_id + short_id. All three must match the *current* pending row, because the row is reused (`agent_loop.py:839-851`). • `callback.from.id` must be the paired user; agent-loop approvals are requester-only already (`agent_loop.py:3983-3986`). • Cards are acted on only within `MAX_UPDATE_AGE`, and the row's own `expires_at` applies (`_claim_turn_for_resume` refuses expired pauses, `agent_loop.py:3861-3863`). • The decision goes through consent timing (`coordinator.py:127-175`). • Buttons are removed after any outcome, and lazily on a stale tap. |
| **Telegram reads the content** | Telegram's servers see everything relayed: prompts, replies, file contents, approval text, the PIN. Room messages are encrypted at rest in Kazi (`chatbot/models.py:43,49-60`); binding a room undermines that for what is relayed. | • Explicit disclosure at pairing, and in `docs/telegram.md`. • `CONTENT_MODE` (D5): `summary` sends status and short replies; `notify_only` sends "Reply ready in `<room>`" plus approve and reject only. • Outbound text is re-run through `redact_sensitive_text`. Progress lines carry the phase and tool names only, never tool inputs. • `callback_data` carries only the random token, never parameters. • The PIN is described as protection against someone holding your unlocked phone or a hijacked Telegram session, **not** against Telegram. Advise a PIN not reused elsewhere. • Recommend binding only rooms you would put in a cloud chat. |
| **Telegram account takeover (SMS hijack, lost phone)** | The attacker becomes the paired user. | • PIN for high-risk approvals (`PIN_POLICY`), with lockout and an in-app alert. • `/unpair` and the web settings, plus `telegram_unpair` locally. • Tell the user to enable Telegram two-step verification ([Known]: Telegram offers a cloud password). |
| **Stale backlog after downtime** | Telegram keeps updates for 24 h, so a message sent yesterday could run today. | • `MAX_UPDATE_AGE` (default 10 min). Older text is shown as "not run, resend". Older callbacks are refused. |
| **Group exposure (group mode, stage 4)** | Other members read everything and see the cards. | • Recommend a private group with only the user and the bot. • Inbound accepts only the paired `from.id`. • PIN challenges always go to the link's private chat, never the group (§4.2 step 5); no DM, no approval. • Show a warning if `getChatMemberCount` is above 2 ([Inference]: the method exists in the Bot API; I did not check its current wording). |
| **Flooding and cost** | Many messages or files trigger turns and LLM spend. | • The existing queue cap, token quota and upload quota. • The bridge's own closed per-chat limiter. • One sender per chat; `retry_after` is always obeyed (L2). |
| **Formatting injection** | MarkdownV2 or HTML in model output breaks sends or spoofs UI (fake buttons and links). | • Send plain text (no `parse_mode`), or HTML built with `html.escape`. Never pass model text as MarkdownV2. The connector's reminder path has this bug: `chatbot/tasks.py:555-559` uses the default MarkdownV2 without escaping (separate fix, §8). |
| **A handler that skips the check** (L6a) | In another project, button taps reached a handler that the pairing check did not cover, so strangers could change session state. | • **One gate, in `gate.py`, runs before any handler for every update type:** message, callback query, widget query, the Stop update and topic service messages. • A static test lists every `allowed_updates` type and fails if one has no gate test. • An update type the bridge does not handle is dropped, not passed on. |
| **Work done for strangers** (L6b) | Anyone who finds the bot can send it files. If the bridge downloads before checking, a stranger fills the disk or reaches the parser. | • Nothing is downloaded, transcribed or stored before the gate passes. • Unpaired chats get no reply, so they cannot even make the bot send. • With Threaded mode on, a stranger's message creates a topic in *their own* chat with the bot; the bridge ignores it. |
| **The agent re-pairing the bridge** (L6d) | Injected text makes the agent add an attacker's chat. | • The bridge exposes no tools to the model, so there is nothing for the agent to call. • Pairing codes are issued only by a logged-in person (the local command or the web settings page), never from chat text. • Pairing, unpairing and allowlist changes each send an in-app notification. • The `open` shell profile runs on the host and can read `.env` (see the stolen-token row). That risk exists today and the bridge does not widen it. |
| **Silent loss** (L1) | Not an attacker: the bridge itself drops a message and nobody notices. A missed approval card or reply while you are away is the failure that matters most. | • The delivery ledger: every room message is `sent`, `pending` or `failed`, never unknown. • `/status` and `./kazi status` show pending and failed counts. • A failed delivery raises an in-app notification. • The ending safety net (§4.1 step 7). |

**Hooks the bridge must not skip.** The gate and steps 3-7 of §4.1, and steps 0-7 of
§4.2. Everything from the turn runner onward runs unchanged, because the bridge enters
through the same seam as the WebSocket (§1.8).

---

## 6. Gap list: reuse versus build

| # | Item | Status | Size |
|---|---|---|---|
| G1 | Inbound seam `submit_user_message` (extracted from the consumer) | Missing; logic exists inline (`consumers.py:861-1057`) | M |
| G2 | Broadcast human messages to the room group, so web tabs see Telegram-sent text | Missing (`consumers.py:979`) | S |
| G3 | Public read of the pending approval (pk, turn, short id, risk) | Partly there: `pending_approval_prompt` (`agent_loop.py:3774-3801`), private `_pending_approval_record`; risk derivable via `is_high_risk_action` (`action_catalog.py:695`) | S |
| G4 | Typed per-turn frames, a structured approval event and a non-consumer subscription hook | Missing. This is T-A2 (#217), not started. **Needed only for progress lines (stage 3)** | Part of T-A2 |
| G5 | Bot API client with safe error handling and 429 handling | Missing; the connector's client is not reusable (§1.1) | S |
| G6 | Poller with lease, offset, webhook check, retry-forever and watchdog | Missing; lease pattern reusable (`turn_leases.py`, `in_process_scheduler.py:209-258`) | M |
| G7 | Pairing and allowlist models and commands | Missing | M |
| G8 | Document ingest helper | Logic exists inline (`message_actions.py:221-346`) | S |
| G9 | Confirm whether document text is treated as untrusted in the context | Unknown | S (investigate) |
| G10 | Mirror loop and delivery ledger | Missing. Room messages and turn notices already exist as rows (§3.1) | M |
| G11 | PIN | Missing; Django `make_password`/`check_password` reusable | S |
| G12 | One topic per room, new topic = new room, topic lifecycle, #905 handling | Missing | M |
| G13 | Telegram notification channel | Missing; hard-coded branches (`notifications/services.py:116-142`) | M |
| G14 | Bind the approval id into consent | Missing; the timing check covers most of it | M (contract minor) |
| G15 | Docs (`docs/telegram.md`, `.env.example`, architecture `model.js` scenario) | `docs/telegram.md` is wrong today | S |
| G16 | Bridge health: status, watchdog, alerts | Missing; `kazi_status` exists to extend | S |
| Reuse | Turn runner, coordinator, consent, resume, receipts, quotas, gates, redaction, `user_has_room_access`, `persist_kazi_message`, `submit_execution_approval`, test harnesses | Reuse as-is | — |

---

## 7. Decisions

**Decided by you**

| # | Decision | Outcome |
|---|---|---|
| **D1** | When to build | **2026-11-08:** the connector freeze does not apply. Build from scratch on the v0.8 core. |
| **D4** | How rooms map to Telegram | **2026-11-13:** one topic per room in the bot's private chat, from the first version, with no `/room` switching. |
| **D15** | Voice architecture | Chained (ear → text agent → mouth), no native speech model (§3.5). |
| **D21** | Depend on Telegram Premium | **2026-11-13:** no. |
| **D24** | Lean or feature-rich | **2026-11-13:** feature-rich, with reliability and consistency first. This plan builds the rich set in stages on a delivery layer that cannot fail quietly. |

**Recommended by me; yours to overrule**

| # | Decision | Options | Recommendation |
|---|---|---|---|
| **D2** | How a button approves | (a) feed "yes"/"no" through the chat path; (b) call `resume_after_confirmation` directly; (c) a new approve-by-id API | **(a).** It keeps every existing guard. (b) skips consent and the runner. (c) touches protected `agent_loop.py` for no gain. **TB-16 is required before TB-11 ships** (§4.2). |
| **D3** | Where the poller runs | (a) lifespan task in the web process plus a database lease; (b) its own management command and compose service with its own `TurnRunner`; (c) Celery | **(a).** Works in lite and full, and inherits runner shutdown. (b) breaks in lite (in-memory layer, boot sweep at `turns.py:1176-1187`) and edits protected `docker-compose*.yml`. (c) is ruled out (§1.7). |
| **D26** | Where outbound messages come from | (a) the live stream; (b) the database, through a delivery ledger | **(b).** It is the only option that makes drops visible (L1), it survives restarts with no recovery code, and it does not wait for T-A2 (§3.1). The stream is used only for progress lines. |
| **D23** | Can Telegram start a new Kazi room | yes; no (rooms only from the web) | **Yes: typing in the bot's main view starts a new topic and a new room.** This is how Telegram's own clients behave when users may create topics (§2). Renaming a topic changes only the binding's label. Deleting a topic never deletes the room. If TB-0 shows the phone behaves differently, fall back to a `/new <name>` command. |
| **D10** | What shows while Kazi works | (a) a draft with a Stop button; (b) "typing…"; (c) one edited status message | **(a) in private chats, if TB-0 shows it renders on your phone; otherwise (b).** A draft expires by itself, so there is nothing to clean up after a crash, it has a native Stop button, and its rate limit is documented (§2). (c) is the fallback for group mode only, with the L4 rules. This reverses the earlier "drafts later" recommendation. |
| **D25** | After downtime, how much to send | everything; a cap plus a summary | **A cap of 20 per topic plus "N earlier messages are on the web".** A flood of old messages hits rate limits and buries the newest one. |
| **D5** | Default content mode | full; summary; notify_only | **`full`, with a disclosure the user must confirm at pairing**, plus a per-binding override. The goal is remote control, and notify_only would make it useless. |
| **D6** | Forwarded messages | reject; accept as quoted data | **Reject**, with "forwarded messages aren't run; copy the text if you mean it". Simpler and closed. |
| **D7** | Injection-check hit on inbound text | block; pass through (downstream blocks sensitive actions); pass plus taint the room | **Pass through, and show a warning in Telegram.** The owner wrote it. `should_block_action` already blocks sensitive actions on that query (`tool_executor.py:117-123`). Revisit when room taint for inbound messages lands (`docs/plans/170-taint-flag.md:15-16`). |
| **D8** | PIN policy | off; high-risk only; every approval | **High-risk only**, where high-risk means catalog `risk_level=="high"`, or a shell tier of destructive or egress. Set the PIN locally (`telegram_pair --set-pin`) or in web settings, never over Telegram. Until the PIN ticket ships, high-risk approvals are a link to the web. |
| **D9** | Mirror web activity to Telegram | yes; no | **Yes:** web-typed messages are mirrored silently, and cards appear only for the bound user's own approvals. Agent-loop approvals are requester-only anyway. |
| **D11** | Raise `send_telegram_*` risk to require confirmation | yes (separate PR, protected `action_catalog.py`); no | **Yes, separately.** It is unrelated to the bridge, but the low rating is inconsistent with outside-world doctrine. |
| **D12** | Static allowlist env in addition to pairing | yes; no | **Yes, optional.** `ALLOWED_TELEGRAM_USER_IDS` empty means pairing alone. |
| **D13** | What happens to the v0.4.2 Telegram connector | (a) leave it; (b) rebuild it on `telegram_bridge/bot_api.py`; (c) retire it | **(b), after the bridge ships.** One Bot API client, one bot. Retiring it removes agent-initiated sends to arbitrary chats; that is your product call. |
| **D14** | How T-A2's frames reach in-process clients | callbacks (sinks registered with the runner); channel-layer group messages only | **A sink registry in the runner that the consumer and the bridge both register with.** This is T-A2 design input; decide it there. It now matters only for progress lines. |
| **D16** | Can a spoken "yes" approve | yes; no | **No.** Transcripts are `consent_eligible=False` (TB-M0); approve with the buttons. |
| **D17** | Default speech-to-text | OpenAI `gpt-transcribe`; local faster-whisper; Groq | **OpenAI by default, plus `openai_compatible`** so local or Groq is one setting. This adds a disclosure: audio goes to the STT provider as well as Telegram. |
| **D19** | How photos reach the agent | native image blocks; describe-first pre-pass | **Describe-first** (TB-M4). It works on all three providers with no protected-path change. |
| **D20** | Which voice model speaks after 2027-01-06 | an OpenAI Realtime or audio model; a third-party or local TTS through `openai_compatible` | **Decide before January**, and only if voice replies are wanted (D18). |
| **D22** | Default UI for approval cards | inline buttons; widgets (10.4) | **Buttons by default; widgets opt-in per binding** (`/ui widgets`) once a test widget renders on your device. |

**Open: I need your answer**

| # | Question | Why it matters |
|---|---|---|
| **D18** | Do you want spoken replies (TB-M2)? | It is the only ticket that depends on OpenAI's speech models, which are being removed in January (D20). Voice notes *in* don't depend on this. |
| **D27** | Will anyone but you use this bot (group mode, TB-14)? | Group mode is the largest block of extra risk: other people see the cards, and drafts and widgets don't work in groups. If it is only you, I would drop it. |

---

## 8. Staged plan (small, test-first tickets)

Every ticket follows `docs/golden-principles/tests-and-evals.md`:
- show each new test failing for the right reason before the change;
- mock only at the Telegram HTTP boundary (the `bot_api` transport) and the LLM
  (`_FakeLLM`);
- use the real runner, database and channel layer;
- run CI's blocking commands (`:68-80`).

**Nothing here starts until this plan is approved.** After approval, stage 0 runs
first, and each ticket that touches a protected path gets its own plan and human OK.

**Protected paths and contracts touched:**
- migrations (TB-5, TB-15);
- the coordinator and `docs/contracts/approval.md` (TB-16, minor bump; TB-M0);
- `docs/contracts/turn-stream.md`, which is T-A3's own deliverable (stage 3);
- `.env.example` (TB-18).

**Ticket numbers.** Numbers from earlier revisions are kept so review comments still
point at the right thing. TB-10 now means progress lines only; replies moved to TB-20.
There is no TB-17.

### Stage 0: device checks and plans (days, no Kazi code)

| Ticket | What | Done when |
|---|---|---|
| **TB-0** | **Check Telegram's real behaviour on your Android phone and one desktop app**, with a throwaway bot and a throwaway script kept outside the repo. Checks: (1) Threaded mode on, `getMe.has_topics_enabled` true, no Premium; (2) typing in the main view creates a topic, what update arrives, `is_name_implicit`, the bot can rename it; (3) the same with user-created topics disallowed; (4) `createForumTopic` then a send, 20 times, counting #905 failures; (5) what a send into a deleted topic returns; (6) "typing…" appears inside the topic; (7) a draft with empty text and `can_stop` renders, survives its 30 s refresh, and Stop delivers `stopped_message_generation`; (8) button taps carry the topic; (9) an MP3 through `sendVoice` plays as a voice bubble; (10) a topic message notifies with the topic's name, and `disable_notification` is silent; (11) the topic link opens the topic; (12) 30 sends in 30 s to one topic: when 429 arrives and its `retry_after`; (13) a test widget renders | A results table is added to this document, and D10, D22 and D23 are confirmed or changed |
| **Plans** | `docs/plans/` entries for TB-5 (migrations) and TB-16 (coordinator and contract) | Each has a human OK |

### Stage 1: the delivery layer (usable every day; does not wait for T-A2)

| Ticket | What | Tests (each shown failing first) | Size |
|---|---|---|---|
| **TB-1** (core, #216) | `chatbot/inbound.py::submit_user_message(room, user, text, *, via, within_txn=None)`, extracted from `ChatConsumer.new_message`; the consumer becomes transport only, as #216 asks. `within_txn` runs inside the same transaction as the `Message` and queued-`Turn` writes, and `runner.submit` happens after commit | (1) existing `test_turn_runner` WebSocket tests unchanged; (2) a message submitted through the seam, with no socket, runs a turn whose reply is saved; (3) a "yes" through the seam resumes a real paused turn (`_PauseHarness`, `CONSENT_SKEW_SECONDS=0`) and writes a `basis="human"` receipt; (4) a non-member is refused and **no** `Message` row is created (fixes the orphan row at `consumers.py:935` vs `:958`) | M |
| **TB-2** (core, #216) | Broadcast human messages to the room group, so an open web tab shows a Telegram-typed message without a reload (G2). Today's frame shape now; renamed with T-A2 | Two clients; B receives A's message with `via`; the sender does not get it twice | S |
| **TB-16** (core, plan) | A decision submitted for a specific approval (`submit_user_message(..., decision_for=(pk, turn_id))`) is refused by the coordinator if the pending row's id or turn differs. Minor bump to `approval.md` | The §4.2 race, forced with an event at the seam: a stale decision is refused and the new pause stays pending; a typed "yes" with no `decision_for` behaves as today | M |
| **TB-4c** | Read helper `orchestration/approval_view.pending_agent_approval` (G3) | The reused-row case returns the *new* turn_id and short_id; another user's row is invisible; a database error raises (callers deny) | S |
| **TB-5** (plan) | Models and migrations (§3.3) | A second active link for a chat is refused and a revoked one is allowed; conditional-UPDATE helpers have exactly one winner under a barrier race; the ledger's unique keys refuse a second row for the same message and part | M |
| **TB-6** | `bot_api.py`, from scratch | A 429 with `retry_after` waits for it on every method (fake clock); `ok:false` maps to a typed error; the token never appears in `str(error)` or in logs (`assertLogs`); a timeout after the request was written is reported as "unknown", distinct from "not sent" | S |
| **TB-8** | Poller and lifespan wiring (§4.1, §4.5) | A webhook already set → no polling, plus an alert; two pollers → exactly one holds the lease (barrier); restart resumes from the stored offset with no duplicate `Message`; a failure injected inside step 7's transaction leaves neither the `TelegramInbound` row nor the `Message`, and the replay then produces exactly one `Message`; updates older than `MAX_UPDATE_AGE` do not enqueue; a `bot_id` change stops the poller; **a network error, a 5xx and a 409 each leave the loop running** (L5); no completed poll for `STALL_SECONDS` rebuilds the client; lifespan stop happens before runner shutdown | M |
| **TB-7** | The gate, pairing, `telegram_pair` / `telegram_unpair`, `/pair` (§4.4, §5) | Expired, used, wrong, group-chat and over-limit codes are refused; a cache error refuses (closed); success binds `from.id` and emits the in-app notification; **one gate test per update type, each failing when its guard line is removed** (L6a); a static test fails if an `allowed_updates` type has no gate test | M |
| **TB-9** | Topics and routing (D4, D23): the system topic; one topic per room at pairing (`has_topics_enabled` check, `createForumTopic`, test send); routing by `message_thread_id`; a new topic becomes a new room; `/stop` is an exact stop (`cancel_requested_at`, #306); "message thread not found" marks the binding broken | One test per filter, each failing when its guard line is removed: wrong `from.id`, `sender_chat`, `is_bot`, forwarded, guest, revoked link, no room access. Text in topic X reaches room X with `via=telegram`. A new topic yields exactly one room owned by the paired user, even when the update is replayed. A command in a new topic creates no room. Topic mode off → pairing stops with the BotFather instruction. A broken topic is reported and nothing is posted to another topic | M |
| **TB-20** | The mirror loop and delivery ledger (§4.1) | Real runner and `_FakeLLM`: a reply saved in the room is sent once to its topic; a web-typed message is sent with the web label and silently; a message that came from Telegram is not echoed back; **a row committed late with a lower id is still delivered** (two transactions, forced order); a crash injected after the fake transport accepts the send and before "sent" is saved re-sends once with the prefix, and a further restart sends nothing; a 429 delays and never drops; a reply over 4000 characters goes as ordered parts and a crash mid-way resumes at the right part; a fake `sk-…` key in a message never reaches the transport; lost room access pauses the binding and sends nothing; more than the cap waiting → the summary line plus the newest; a turn that ended with no message gets exactly one ending line | M-L |
| **TB-21** | "Thinking…" and Stop (§4.5) | A running turn gets one draft, refreshed before 30 s; the Stop update records a person's cancel through the seam, and from an unpaired user does nothing; an error from the draft call stops decoration for that turn and **the reply is still sent** (L4); `WORKING_UI=typing` sends chat actions instead; a finished turn sends no further draft | S |
| **TB-11** | Approve/Reject cards from the database (§4.2); high-risk approval is a link to the web | Approve resumes and writes a receipt; reject cancels; a double tap gives exactly one decision (barrier); a stale card after row reuse is refused; another Telegram user is refused; an expired card is refused; an approval decided on the web closes the card on the next tick; `answerCallbackQuery` is called first and a "too old" error does not undo a recorded decision; a failure injected between the card UPDATE and the decision `Message` rolls both back so a re-tap works; a reaped decision Turn with the approval still pending gets a fresh card; a high-risk card has no Approve callback button | M |
| **TB-22** | Health (§4.5): `/status`, `kazi_status`, the web "Telegram: connected" line, alerts, the back-online line | A stale `last_poll_ok_at` reports not connected while the process is up; a failed delivery raises one notification; `/status` counts pending and failed rows | S |
| **TB-18** | Docs and architecture: rewrite `docs/telegram.md` (setup, the BotFather step, disclosure, rotation runbook); add the variables to `docs/configuration.md` and `.env.example`; update `docs/architecture/model.js` (an external "Telegram Bot API" node, a bridge module in `web`, edges, a "Telegram approval" scenario); run `python scripts/check_architecture_model.py`. Ship with TB-8 and TB-20, not at the end | — | S |

**At the end of stage 1** you can pair, see every room as a topic, send text, read
every reply and notice, see "Thinking…", stop a run, and approve or reject
low- and medium-risk actions from your phone.

### Stage 2: rich features that need no stream

| Ticket | Needs | What | Tests | Size |
|---|---|---|---|---|
| **TB-12** | TB-11 | PIN for high-risk approvals, asked only in the private chat | A high-risk card asks for the PIN; the PIN message is deleted and never reaches the room; a wrong PIN ×5 locks the link and notifies; an unreachable private chat refuses the approval; a low-risk card skips the PIN; step 6 of §4.2 refuses when the approval changed during the PIN wait | S-M |
| **TB-11b** | TB-11 | "Continue" buttons for budget and quota pauses, using the `paused_at` consent check from #306 | A "continue" queued before the budget pause existed does not resume it (mirror `test_a_queued_yes_cannot_approve_a_prompt_it_never_saw`) | S |
| **TB-9b** | TB-9 | Topic lifecycle: a web-created room gets its topic; `forum_topic_edited` → label; closed / reopened → pause / resume; `/restore` for a broken topic | A room created on the web gets exactly one topic. A closed topic runs nothing until reopened. Deleting a topic never deletes the room. `/restore` binds a fresh topic and the bookmark is kept | S-M |
| **TB-3**, **TB-G9** | — | `chatbot/documents.py::ingest_room_document`, extracted from `upload_document_to_ai`. Before it ships: is document text in `ContextManager` treated as untrusted? Write the answer down and file an issue if not | Quota, type and size limits; the view's existing tests still pass | S |
| **TB-13** | TB-3 | Files (§4.3) | An unpaired sender never triggers `getFile` (L6b); an oversized declared size never calls `getFile`; the type is sniffed (a PDF named `.jpg`); the token URL never appears in logs; a stalled download does not delay another topic's text (L3); a text sent after a file in the same topic runs after it; a restart resumes a `media_pending` row | M |
| **TB-M0** (core, plan) | TB-1 | `consent_eligible` flag on `submit_user_message`. When False, the coordinator does not treat the text as an approval, decline or continue reply (`coordinator.py:1218-1236,1346-1431`) | A transcribed "yes" leaves a real paused Turn paused and its approval pending; a typed "yes" still approves | S |
| **TB-M1** | TB-M0, TB-13 | Voice notes in: download, OGG→WebM stream copy, ear adapter, transcript echo, `via="telegram_voice"`; the transcript is stored encrypted | The fake STT transport receives an allowed format, never raw `.ogg`; the stored message decrypts to the transcript and is not plaintext; over the duration cap → refused with no turn; STT error → honest reply with no turn | M |
| **TB-M3** | TB-13 | Photos stage 1: store, an honest note, caption as text, album grouping by `media_group_id` | Three updates sharing one `media_group_id` become one room message and at most one turn; the note names the room's model | S |
| **TB-15** (plan) | TB-11 | Telegram as a notification channel, and workflow approval cards through `submit_execution_approval`. Owner and status checks; the `ApprovalMismatch` 409 path (reuse `_submission_env`). Adds `delivered_telegram` (a migration) | A workflow card approves only for the workflow's owner and only while waiting; a stale approval id is refused | M |
| **TB-W3** | TB-9 | "Open in Telegram" link on the web room page (`https://t.me/<bot>/<thread_id>`) | The link appears only for the bound user | S |
| **TB-W4** | TB-11 | Ephemeral replies to button taps for throwaway notes (stale card, PIN lockout) | The note goes to the tapping user only; a static test proves cards and replies never use ephemeral parameters | S |

### Stage 3: live progress (needs T-A2, #217)

| Ticket | Lands in | What | Tests | Size |
|---|---|---|---|---|
| **TB-4** (core) | #217 | `approval.requested` carries `approval_pk`, `short_id`, `approval_kind` and a risk class; `approval.resolved` carries `approval_pk`, `decision` and `via`; a frame-sink registry on the runner that the consumer and any in-process client register with (D14) | Every frame carries `turn_id` and a monotonic `seq`; a second registered sink receives the same frames as the consumer; a sink that raises never aborts the turn | M |
| **TB-A3** (core) | #218 | The client contract doc is written for *a client*, not the browser. The duplicate, reorder, reconnect and terminal fixtures run against a generic test sink | Fixtures pass for the consumer and for the test sink | S |
| **TB-10** | after TB-4 | Progress lines in the "Thinking…" draft: the current phase and tool names, never tool inputs. Frames also wake the mirror at once, so replies and cards arrive without waiting for the tick | One update at most every `PROGRESS_MIN_SECONDS`, identical text skipped; duplicate or reordered `seq` ignored; a failed turn never shows "Done"; with the sink removed the reply still arrives on the tick (the delivery layer does not depend on frames) | M |

### Stage 4: extras, each optional and each your call

| Ticket | Needs | What | Size |
|---|---|---|---|
| **TB-M2** | TB-M1, D18, D20 | Spoken replies: one mouth adapter replacing `generate_voice_response` and the `generate_speech` delivery gap; reply in kind plus `/voice`; the text is always sent too | M |
| **TB-M4** (core, plan) | TB-M3 | Photo understanding: a describe-first vision pre-pass into `processed_text`, framed as untrusted, cached by `file_unique_id` | M |
| **TB-W1** | TB-11 | Approval widget: a static template, state in `chat_data`, taps through the same handler as buttons, fallback to buttons (§3.6) | M |
| **TB-W2** | TB-10, TB-W1 | Status widget per run, where widgets are switched on | M |
| **TB-14** | D27 | Group mode: a private forum supergroup with one topic per room; approvals and the PIN stay in the private chat; an edited status message instead of a draft, with the L4 rules | M |
| **TB-19** | — | D13: rebuild the v0.4.2 connector's sends on `bot_api.py`, or retire it | S |
| **TB-M5** (core, later) | T-A2, plan | Native image blocks. Only if TB-M4 proves insufficient | L |

### Separate PRs, not part of the bridge

- **Two security items found during this pass** are handled privately under
  `SECURITY.md` (§1.1, §1.4).
- **The unescaped MarkdownV2 reminder send** in `chatbot/tasks.py:555-559`: send plain
  text.
- **D11:** raise the `send_telegram_*` risk (`action_catalog.py` is protected).
- **AGENTS.md discrepancies** in §1.8: receipts editable, capability gate open on
  missing preferences, `approval.md` drift.
- **Notification email and WhatsApp `execute` call bugs** (§1.6).
- **The DeepSeek default model is a retired name.** `DEEPSEEK_MODEL` defaults to
  `deepseek-chat` (`Backend/settings.py:696`, `orchestration/llm_client.py:282-285`).
  DeepSeek's updates page says that name was discontinued on 2026-07-24. Also,
  `model_catalog.py:30-31` lists `deepseek-v4-flash` and `deepseek-v4-flash-vision-exp`,
  legacy names DeepSeek now routes to `deepseek-flash`. `deepseek-v4-pro` (`:29`) is
  current.
- **Move off OpenAI audio models before they are removed.** `whisper-1`
  (`chatbot/tasks.py:264`, removed 2027-02-26) and `tts-1` / `gpt-4o-mini-tts`
  (`chatbot/tasks.py:334-340`, `tts_connector.py`, removed 2027-01-06). The web voice
  features break on those dates even without the bridge.
- **TTS settings can't be set from the environment.** `tts_connector.py` reads
  `TTS_PROVIDER`/`TTS_URL`/`TTS_MODEL`/`TTS_VOICE` only from Django settings, and
  `settings.py` defines none of them.

---

## 9. What I could not verify

**Telegram behaviour, to be settled by TB-0 on the real phone**
- Whether private-chat topics display for users without Premium. The official pages
  name no Premium requirement; the terms say only that the user's app must support
  topics.
- What the Android app does when you type in the bot's main view (§4.4).
- Whether drafts, the Stop button and "typing…" inside a topic render on Android. An
  open Telegram bug report says "typing…" shows in the wrong place on macOS and iOS.
- Whether an MP3 sent with `sendVoice` plays as a voice bubble.
- How often #905 (a new topic refusing every write) happens.

**Telegram facts with no official source**
- Real `editMessageText` limits. grammY's guide cites a Telegram support-chat message
  for "20 message edits in a minute per group chat"; that is not documentation.
- Whether going over the draft and "typing…" limit also delays normal sends.
- Whether Telegram states anywhere official that bot chats are not end-to-end encrypted.
- The current state of tdlib #854. Only the search-result title was seen.

**Other people's incidents I saw only as summaries**
- The Claude Code Telegram plugin issues (L5): titles and summaries from a mirror
  site. GitHub itself was not reachable for that repository from this session.
- The OpenClaw username-allowlist advisory (L6c).
- The phone-remote write-ups (P4) and the callback timing (P3).

**Kazi**
- Whether every turn ending saves a room message. I traced three call sites (§3.1);
  the safety net in §4.1 step 7 covers the rest.
- Whether existing CI would pass after TB-1. No code was changed or run.
