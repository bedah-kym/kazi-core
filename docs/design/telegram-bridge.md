# Telegram remote-control bridge: design

Status: **proposal, no code**. This is an investigation and design pass. It does not
approve any change. The data model needs migrations (a protected path), so
implementation starts with a plan under `docs/plans/` and a human OK (`AGENTS.md` §0).

Date of investigation: 2026-11-08, against `main` at `121df6d` (includes #303, the turn
runner).

**How to read the labels**

| Label | Meaning |
|---|---|
| **[Verified]** | Read in the code or docs during this pass. A `file:line` follows. |
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

## 0. Summary

The bridge is feasible without touching any protected core module. The work is mostly
plumbing: there is no non-WebSocket way into a room today, and no structured approval
event reaches clients.

- **The existing Telegram connector is outbound only.** It cannot receive anything.
  - The webhook URL its docs tell you to set does not exist.
  - The right home for remote control is a new Django app (`telegram_bridge/`), **not**
    the connector. The model never sees bridge actions, so no tool catalog entry
    changes.
- **Approving from a button should feed the existing chat-reply path, not bypass it.**
  - The button sends the user's "yes" or "no" through the same inbound seam as a typed
    reply.
  - The existing consent-timing, single-consumer and argument-hash checks then run
    unchanged.
  - The button carries only a random single-use token. That token must match the
    approval row's id, turn and short id, because the row can be reused by a later
    pause. The decision is also bound to that approval inside the coordinator (TB-16),
    because the consent-timing check alone does not cover a tap that waits on a PIN
    (§4.2).
- **The poller should run inside the ASGI web process as a lifespan task, held by a
  lease.** It should not be a Celery task or (in lite) a separate process. Turns must
  be drained by a long-lived event loop that owns them, and lite's channel layer is
  in-memory.
- **Freeze: does not apply.** The maintainer decided on 2026-11-08 that the "no new
  connectors" line does not apply to this work.
- **Built new.** The bridge is built from scratch on the v0.8 core. It does not reuse
  the v0.4.2 connector, and it does not use today's stream frames (§0.1).

---

## 0.1 Where this fits in the roadmap

The roadmap (`docs/roadmap.md:28-33`) has v0.8, "turns that own the truth", in flight.
Its epic is #227, and its build order is A, then B, then C, with "do NOT jump ahead".

**Status on 2026-11-08** [Verified: GitHub PRs and issues, `CHANGELOG.md`, the code]

**We are in the middle of T-A1 (#216), not near its end.** #216 is delivered as a series
of slices. The latest, 216b-2 (the turn runner, PR #303), merged on 2026-11-07. It is
"216b-2 from the approved #216b plan". At least one slice, 216b-3, is still to come.

| Slice | PR | State | What it did |
|---|---|---|---|
| 216a: durable Turn rows, honest endings | #286 | Merged | `Turn` model, one writer, CAS transitions (`CHANGELOG.md`, "Added") |
| P3-3: durable pauses | #289 | Merged | Pauses on `Turn.paused_state_enc` (`CHANGELOG.md:237-264`) |
| 216b-0: consent postdates the prompt | #291 | Merged | `CONSENT_SKEW_SECONDS` (`CHANGELOG.md:229-236`) |
| 216b-1: turn leases, self-fence, one reap routine | #292 | Merged | `CHANGELOG.md:266-286` |
| 216b-2: the turn runner | #303 | Merged 2026-11-07 | Queue, FIFO claim, pause continuation, shutdown endings (`CHANGELOG.md:10-37`) |
| **216b-3** | — | **No PR found** | Named in #303 for: a person's cancel as `cancel_requested_at` → `cancelled(user_cancel)`; consent-postdates for **budget** pauses (`paused_at`). Today a queued "ok" can resume a budget pause without that check (#303, round 2, item 13). In code it appears only as a docstring (`orchestration/coordinator.py:183`) |

Other v0.8 tickets: T-A4 #219 and T-B3 #222 are closed. T-A2 #217, T-A3 #218 and
T-B1/B2/B4 and T-C1-3 (#220-221, #223-226) are open. There are no T-A2 emitters in the
code.

**What I cannot see.** The approved #216b plan is not in `docs/plans/` or on GitHub. Nor
is the roadmap the plans cite, with its T-D, T-F, P3, E and V items
(`docs/plans/2026-10-per-message-work.md:9`). Local planning docs are gitignored
(`.gitignore:172-182`). So the placement below covers the slices I can see. Anything
after 216b-3 in the #216b plan may move it.

**What #303 says about callers like the bridge** [Verified: the #303 description, "Live
calls (V-5)"]. The runner's only public contract is `submit(room_id, user_id)` and the
claimed `Turn`. The message-to-turn assumption lives in `enqueue_turn` and the
consumer. A future voice-call caller would submit a claimed Turn directly, keyed by
session rather than (room, user). The bridge is a message-based caller, so it uses
`enqueue_turn` + `submit` exactly as the consumer does, which needs no runner change.

**Placement.** The bridge is the **second client of the turn**. Phase A is the work that
makes a turn safe to watch from anywhere. So the bridge sits on Phase A, runs in
parallel with Phase B, and does not wait for Phase C.

```
 T-A1 #216:  216a ✓ ─ P3-3 ✓ ─ 216b-0 ✓ ─ 216b-1 ✓ ─ 216b-2 ✓ (#303) ─► 216b-3 ─► (rest of #216b plan?)
                                                                        │
 bridge needs from it:                                person cancel (/stop), budget-pause consent
                                                                        │
 then:   inbound seam (TB-1) ─► T-A2 #217 (+TB-2/TB-4/TB-16) ─► T-A3 #218 (+TB-A3) ─► Phase B ─► C
             │                         │                              │
         Track 2a (bridge app,     Track 2b (relay, cards)        bridge in the
         no relay)                                                contract fixtures
```

**Why this order.** Three core needs coincide with what the bridge needs. Each is
v0.8 work the web UI needs too.

1. **A transport-neutral inbound door.** #216's rule is "consumers only transport
   frames". After #303 the consumer still owns persistence, routing gates and
   enqueueing (`consumers.py:861-1057`). Moving that into `chatbot/inbound.py` is in
   T-A1's spirit, and it is the bridge's entry point. Do it **after 216b-3**: 216b-3
   changes how a cancel is recorded, and that logic lives in the same consumer path.
   Otherwise you refactor it twice.
2. **Typed frames with `turn_id`, `seq`, `approval.requested` and one terminal event.**
   That is #217. For the bridge, this means:
   - the per-run status message is a direct mapping (`turn.started` sends it, tool
     events edit it, the terminal event closes it);
   - approval cards come from `approval.requested`, instead of reading the coordinator's
     prose (`coordinator.py:903-906`);
   - restart recovery uses `turn_id` + last `seq`.
   Building on today's `ai_step_event` text would mean building twice.
3. **Truth on the phone.** Phase B makes the terminal event honest: a claim with no
   matching write-set ends as `turn.failed{partial}` (#221). A bridge built on v2
   frames inherits that. A "✅ Done" on the phone then means the record says done, which
   is the epic's success metric.

**What 216b-3 gives the bridge.** Two bridge features wait on it:
- **`/stop` from the phone.** It should record a person's cancel. #303 moved that to
  `cancel_requested_at` in 216b-3. A socket close, or a poller restart, is no longer a
  cancel.
- **Budget- and quota-pause "Continue" buttons.** Until 216b-3 adds `paused_at`, a
  queued "ok" can resume a budget pause without the consent-postdates check, and a
  button would inherit that gap. Approval-pause cards are not affected: 216b-0 already
  covers them.

**What to fold into existing v0.8 work** (proposed; not filed):
- **#216, after 216b-3** (or as a slice of the #216b plan if it has room): the
  transport-neutral `submit_user_message` (TB-1).
- **#217 (T-A2):** these three should live with that ticket:
  - `approval.requested` carries `approval_pk`, `short_id`, `approval_kind` and a risk
    class (TB-4). Today's `confirmation` event uses the name `approval_id` for the
    8-hex short id (`agent_loop.py:3596`), so name both fields explicitly;
  - frames reach any in-process subscriber, not only the consumer, through a
    subscription hook (TB-4);
  - `approval.resolved` carries `decision` and `via` (TB-4), and a decision can be bound
    to a specific approval (TB-16).
- **#218 (T-A3):** the client contract doc is written for "a client", and its
  reconnect and duplicate fixtures run against the bridge as well as the web consumer.
  A second client keeps the contract from encoding web-only assumptions.

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
  consumes v2 frames only (§0.1).

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
  in 10.3).** It streams partial messages. This is a possible later alternative to
  repeated `editMessageText`.
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

---

## 3. Proposed architecture

### 3.1 Components

```
Backend/telegram_bridge/            (new Django app; not a connector, not offered to the model)
  bot_api.py      Minimal Bot API client: httpx, token never in errors or logs,
                  ok:false parsing, 429 retry_after, per-chat send pacing.
  poller.py       getUpdates long poll (timeout 50 s): lease, offset persistence,
                  webhook check, stale-update cut-off.
  inbound.py      Update → filters → binding lookup → commands (/pair /room /rooms
                  /unpair /status /stop) or chatbot.inbound.submit_user_message.
  relay.py        Subscriber to stream protocol v2 frames (T-A2) per bound room:
                  status message (edit), final reply (send), approval cards.
                  No adapter for today's ai_step_event / ai_stream_chunk frames.
  approvals.py    Card rendering, callback validation, PIN challenge, decision
                  → submit_user_message("yes"/"no"), or submit_execution_approval
                  for workflows (later).
  files.py        getFile download → chatbot.documents.ingest_room_document.
  pairing.py      Code issue and verify.
  lifespan.py     start() / stop(), called from Backend/asgi.py lifespan when
                  TELEGRAM_BRIDGE_ENABLED.
  management/commands/telegram_pair.py     Prints a one-time code locally.
  management/commands/telegram_unpair.py   Revokes a chat link.

Backend/chatbot/inbound.py          (new, extracted from ChatConsumer.new_message)
  submit_user_message(room, user, text, *, via="web"|"telegram", reply_to=None)
Backend/chatbot/documents.py        (new, extracted from upload_document_to_ai)
  ingest_room_document(room, user, data, filename, mime) -> DocumentUpload
Backend/orchestration/approval_view.py   (new, read-only, not protected)
  pending_agent_approval(room_id, user_id) -> {pk, turn_id, short_id, message,
                                               effects, tools, risk}
```

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
| `TelegramChatLink` (the allowlist) | `user` FK, `chat_id` BigInt, `chat_type` (private/supergroup), `telegram_user_id` BigInt (must equal `from.id` on every update), `paired_at`, `revoked_at`, `revoked_reason`, `pin_hash` (Django `make_password`, nullable), `pin_failures`, `locked_until` | unique `(chat_id)` where `revoked_at IS NULL` |
| `TelegramRoomBinding` | `link` FK, `room` FK `Chatroom`, `thread_key` BigInt (0 = no thread, else `message_thread_id`), `mode` (dm_switch / dm_topic / group_topic), `created_at`, `revoked_at` | unique `(link, thread_key)` where active |
| `TelegramPairingCode` | `code_hash` (sha256), `user` FK, `issued_at`, `expires_at`, `used_at`, `used_by_chat_id` | single use |
| `TelegramPollerState` (singleton) | `offset` BigInt, `bot_id` BigInt (pinned at first `getMe`), `lease_owner`, `lease_expires_at`, `last_ok_at`, `last_error` | one row |
| `TelegramApprovalCard` | `token_hash`, `binding` FK, `approval` FK `WorkflowApprovalRecord`, `turn_id` UUID, `short_id`, `chat_id`, `message_id`, `risk`, `created_at`, `decided_at`, `decision`, `decided_by_telegram_user_id`, `outcome_message_id` FK `Message` | conditional UPDATE on `decided_at IS NULL` |
| `TelegramOutbound` | `binding` FK, `kind` (status/reply/card), `step_id`, `turn_id`, `telegram_message_id`, `last_text_hash`, `last_edit_at`, `closed_at`, `reply_sent_at` | for edits, throttling and restart reconciliation (§4.1 Recovery) |
| `TelegramInbound` | `update_id` unique, `chat_id`, `message_id`, `kind`, `received_at`, `result` (`accepted` / `rejected:<reason>`), `kazi_message_id` | dedupe and audit; holds no message text. An `accepted` row is written only in the same transaction as its `Message` (§4.1 Ordering) |

**Notes**
- **Mode A** (one DM, `/room` switching) is one `TelegramRoomBinding` with
  `thread_key=0`. `/room <id>` revokes it and creates a new one. **Modes B and C** have
  one binding per topic.
- **Why a 0 sentinel?** Postgres treats NULLs as distinct in unique constraints, so a
  NULL thread id would allow duplicates.
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
| `TELEGRAM_BRIDGE_MAX_UPDATE_AGE_SECONDS` | `600` | Inbound text or callbacks older than this are not acted on; the user is told. |
| `TELEGRAM_BRIDGE_PAIRING_CODE_TTL_SECONDS` | `600` | Code lifetime. |
| `TELEGRAM_BRIDGE_ALLOWED_TELEGRAM_USER_IDS` | empty (any user may pair) | Optional static allowlist: who may pair at all. |
| `TELEGRAM_BRIDGE_PIN_POLICY` | `high_risk` | `off`, `high_risk` or `all`. Applies once a PIN is set. |
| `TELEGRAM_BRIDGE_CONTENT_MODE` | see D5 | `full`, `summary` or `notify_only`. |
| `TELEGRAM_BRIDGE_STATUS_EDIT_MIN_SECONDS` | `3` | Status-edit throttle. |
| `TELEGRAM_BRIDGE_SHOW_WEB_RUNS` | `true` | Mirror runs started from the web UI in that room's Telegram space. |

`kazi_status --json` should report whether the bridge is enabled, the lease holder,
`last_ok_at` and `last_error`, so `./kazi status` can show it.

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
  text must still be sent with `sendMessage`.

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
- **Status widget.** One per run, in place of the edited text status in private chats.
  Phase, tools and elapsed time live in `chat_data`, edited at the existing 3-second
  throttle. The terminal state and the `closed_at`/`reply_sent_at` rules of §4.1 are
  unchanged. The final reply is still a normal message, so it triggers a notification
  and stays readable.
- **Fallback is mandatory.** Groups (mode C), bindings with widgets switched off, and
  any `sendWidget` error get the plain inline buttons and text status.
  - [Cannot verify] how well each Telegram app supports widgets yet; the feature is
    four days old. The default is therefore buttons, with widgets opt-in per binding
    (`/ui widgets`) after a test widget renders on your device (D22).
- **Truth.** Telegram still sees everything the widget shows. The data limit and
  redaction rules are the same as for text. "Can't be forwarded" makes it harder to
  leak a card by mistake; it does not hide anything from Telegram.

**Links straight to a room's topic (10.4).** [Verified] `https://t.me/<bot_username>/<message_thread_id>`
opens that topic, but only for the user whose private chat holds it. The web room page
can show "Open in Telegram" for bindings that use per-room topics (mode B).

**Ephemeral replies to button taps (10.4).** [Verified] A message shown only to the
user who tapped, in any chat except channel direct-message chats. These messages "may
disappear automatically after some time, or if the app is restarted", so they are used
**only for throwaway notes**, never for cards or anything durable. Examples:
- "This approval is no longer current."
- "Locked for 15 minutes after 5 wrong PINs."
- "This card belongs to another user" (mode C).

In groups this keeps those notes from cluttering the chat for everyone else.

**Data model and config changes:**
- `TelegramApprovalCard` and `TelegramOutbound` gain `render` (`buttons` / `widget`).
- `TelegramRoomBinding` gains `ui` (`buttons` / `widgets`).
- New setting `TELEGRAM_BRIDGE_UI_DEFAULT` (default `buttons`).
- `allowed_updates` adds `widget_query`.

---

## 4. Sequences

### 4.1 Inbound message

```
Phone        Telegram          Bridge (poller task in web process)                    Kazi core
  |--text-->|                    |                                                      |
  |         |<--getUpdates(offset, timeout=50, allowed_updates=[message,callback_query])--|
  |         |--update---------->|                                                      |
  |         |                   | 1 skip if TelegramInbound(update_id) already has a result
  |         |                   |   (a row exists only once its outcome committed; see Ordering)
  |         |                   | 2 drop if: date older than MAX_UPDATE_AGE; from.is_bot;
  |         |                   |   sender_chat set; via_bot; guest_query_id;
  |         |                   |   forward_origin set (D6); no text/caption/file
  |         |                   | 3 link = active TelegramChatLink(chat.id),
  |         |                   |   require from.id == link.telegram_user_id (else drop, silent)
  |         |                   | 4 binding = (link, thread_key) → room; none → reply
  |         |                   |   "not linked, use /room"
  |         |                   | 5 user_has_room_access(link.user, room)  [a DB error raises: catch it and deny]
  |         |                   | 6 bridge rate limit (cache.add, closed on error)
  |         |                   | 7 submit_user_message(room, user, text, via=telegram,  -->|
  |         |                   |     within_txn=record TelegramInbound(accepted, msg id))
  |         |                   |                                  mute, length, encrypt+save,
  |         |                   |                                  moderation, directives,
  |         |                   |                                  [one transaction: Message +
  |         |                   |                                   queued Turn + within_txn]
  |         |                   |                                  then runner.submit after commit
  |         |                   |   (a step 2-6 rejection records TelegramInbound(rejected:…) on its own)
  |         |                   | 8 commit offset = update_id + 1                      |
  |         |                   |                                                      |
  |         |                   |<== turn.started {turn_id, message_id}  (v2, T-A2)    |
  |         |<--sendMessage "⏳ Thinking…" (status, stored in TelegramOutbound by turn_id)
  |         |                   |<== tool.started / tool.result {turn_id, seq, name}   |
  |         |<--editMessageText "🔧 Running web_search…" (≥3 s apart, skip if same)  |
  |         |                   |<== turn.completed {turn_id, final_message_id}        |
  |         |                   |    (or turn.failed{partial} / cancelled / superseded)
  |         |<--editMessageText "✅ Done · 2 tools · 14 s"  or  "⚠️ Ended: <reason>"   |
  |         |<--sendMessage reply = persisted final message (read by final_message_id,
  |         |                   |    redact again, plain text, split at 4096)          |
```

**Ordering.** The offset is committed after step 7 returns, so a crash replays the
update. The `TelegramInbound` row is written **in the same transaction** as the user
`Message` and its queued `Turn` (step 7's `within_txn`), never before it. That gives two
cases:
- **Crash before that commit:** there is no row and no message, so the replay processes
  the update normally.
- **Crash after it:** the message and queued Turn exist, so the replay is skipped. The
  queued Turn is then drained or, if this process died, reaped with #303's saved
  "waiting messages weren't answered" notice, so the user sees an honest ending
  rather than silence.

Together that is at-least-once delivery with idempotent handling, and no silent drop.

**Turn ownership.** Every v2 frame carries `turn_id`. Step 7 returns the queued
turn, so the relay knows which status message belongs to which run. `seq` lets it
ignore duplicate or reordered frames.

**Recovery.** After a bridge restart, it scans `TelegramOutbound` status rows with no
`closed_at`, not only non-terminal turns, and reconciles each against its `Turn`:
- **Still running or paused:** resume by `turn_id` + last `seq`, per the T-A3 contract.
- **Ended while the bridge was down:** edit the status to its ending. Then, if
  `reply_sent_at` is empty, send the persisted final reply.

  **`closed_at` is written last, in the same database write as `reply_sent_at`**, and
  only after the reply has been sent. A turn that ended with no reply to send (for
  example cancelled) writes `closed_at` alone. So a crash anywhere before that write
  leaves the row in the scan. Re-editing the status is harmless: Telegram's "message
  is not modified" error is ignored. The same ordering applies on the live path, not
  only on recovery.

  This is **at-least-once**, not exactly-once. Telegram has no idempotency key for
  sends [Known], so a crash after Telegram accepts the reply but before
  `reply_sent_at` is saved re-sends it on restart. The bound is at most one duplicate
  per crash in that window. Repeated crashes before the resend is recorded can each
  add one more. The resend is prefixed "(re-sent after a restart)" so a duplicate reads
  as one. Losing a reply is worse than a rare duplicate.

Web-started runs in the room are mirrored if `SHOW_WEB_RUNS` is on.

**Truth.** The reply sent is the persisted final message, not the concatenated stream.
v2 makes the persisted text authoritative (proposal §4.2).

### 4.2 Approval round trip (agent loop)

```
 core: run pauses → save_pending_confirmation (row R, created_at T0) → frame:
       approval.requested {turn_id, approval_pk, short_id, approval_kind, risk}  (v2, T-A2 + TB-4)
 relay: on approval.requested:
        a = approval_view.pending_agent_approval(room, user) → {pk, turn_id, short_id,
            message (redacted), effects, tools, risk}; require a.pk == approval_pk and
            a.turn_id == frame.turn_id (the database is the truth, the frame is the trigger)
        token = secrets.token_urlsafe(12)            # 16 chars, about 96 bits
        store TelegramApprovalCard(token_hash, pk, turn_id, short_id, risk)
        sendMessage(card text, inline_keyboard=[[Approve ka:<token>:y],[Reject ka:<token>:n]])
                                                     # callback_data is 21 ASCII bytes
 phone: taps Approve → callback_query
 bridge: answerCallbackQuery immediately (clears the spinner)
        1 callback.from.id == link.telegram_user_id; callback.message.chat.id/thread
          == card.binding
        2 card by token_hash; decided_at IS NULL; age < MAX_UPDATE_AGE
        3 current pending row for (room, user): same pk AND same turn_id AND same short_id
          (guards row reuse, agent_loop.py:839-851); else "This approval is no longer
          current" and disable the card
        4 user_has_room_access
        5 if risk is high and a PIN is set: send the PIN challenge in the link's **private
          chat**, never in a group or topic (mode C: the card says "check your DM"); the
          challenge is bound to this card and its approval pk; the next message from that
          user in the DM is the PIN (deleteMessage it at once); check_password;
          5 failures → lock the link 15 min and send an in-app notification;
          if the DM can't be reached (bot blocked, chat gone) → refuse, nothing approved
        6b re-run step 3 (same pk, turn_id and short_id) immediately before step 7,
          because the PIN round trip can take far longer than CONSENT_SKEW_SECONDS
        6+7 in ONE transaction (submit_user_message's within_txn):
          conditional UPDATE card SET decided_at, decision WHERE decided_at IS NULL
          (one winner on double tap) + the decision Message + its queued Turn;
          runner.submit after commit
        7 submit_user_message(room, user, "yes" | "no", via=telegram,
          decision_for=(pk, turn_id),          # TB-16; the coordinator refuses a mismatch
          within_txn=decide card)
          → coordinator: consent check (Message.timestamp > T0 + skew), approve or decline
            routing, resume_after_confirmation (hash match, single-consumer UPDATE),
            receipts basis="human"
        8 on approval.resolved {turn_id, approval_pk, decision, via}: editMessageReplyMarkup to
          remove buttons, and edit the text to "Approved via Telegram HH:MM". The same frame
          also closes cards when the decision was made on the web.
```

**Interrupted submissions.** Because the card decision and the decision message commit
together, there are two cases:
- **Crash before the commit:** the card is still undecided, so the user can tap again.
- **Crash after it:** the queued decision Turn is drained, or reaped with #303's saved
  notice. If it is reaped while the approval is still pending, the bridge's restart
  scan sends a fresh card. The old token stays used, so single use holds.

**Why the consent check alone does not protect a stale tap [Inference].** The "yes"
`Message` is saved at step 7, so its timestamp is when the bridge processes the tap.
Suppose a pause started from the web replaces R between steps 3 and 7, for example
while the user is typing the PIN. The "yes" then postdates the new row's `created_at`,
and `_consent_postdates_prompt` accepts it. The 2-second skew (`coordinator.py:170`,
`settings.py:545`) does not cover that gap. The existing test
`test_a_queued_yes_cannot_approve_a_prompt_it_never_saw` (`chatbot/test_turn_runner.py:680`)
covers a different case: a message saved *before* the prompt existed. So step 6b
narrows the gap, and **TB-16 closes it**: the decision names the approval it answers,
and the coordinator refuses it if the pending row differs. **TB-16 is a hard
prerequisite of TB-11.**

**Workflow approvals** (TB-15) follow the same card flow. Step 7 becomes
`submit_execution_approval(execution, approval_id=record.pk, reviewer_id=user.id, decision)`,
after `workflow__user=user` and `status="waiting"` checks copied from
`workflows/views.py:426-439`.

### 4.3 File upload

```
 update.message.document | photo (largest size)     (voice notes: §3.5, TB-M1; albums: TB-M3)
 1 filters as in 4.1 steps 1-6
 2 check the declared file_size against Kazi limits (PDF 10 MB, image 5 MB) before
   getFile; Telegram caps downloads at 20 MB
 3 getFile(file_id) → file_path; download https://api.telegram.org/file/bot<token>/<path>
   - streamed, size-capped, URL never logged (it contains the token)
 4 sniff the type from bytes, never trust mime_type or file_name; store under a uuid name
 5 ingest_room_document(room, user, bytes, safe_name, sniffed_mime)
   → upload quota, DocumentUpload(pending), process_document_task
 6 reply "📎 report.pdf added to <room>"; if a caption is present:
   wait (bounded, 30 s) for status=completed, then submit_user_message(caption)
   so the turn's ContextManager includes the document
```

[Inference] Document text enters the context prompt (`chatbot/context_manager.py:155-167`).
I did not confirm that this path is marked untrusted, or tainted the way tool output is
(`docs/plans/170-taint-flag.md:15-16` lists inbound tainting as a non-goal). Listed as
gap G9.

### 4.4 Pairing

```
 laptop$ python Backend/manage.py telegram_pair --user alice
         → prints: "Send this to @YourBot within 10 minutes:  /pair K7QF-3MZD"
           (8 chars, Crockford base32, about 40 bits; stored as sha256 only;
           printed to stdout, not logged)
 phone:  opens a private chat with the bot, sends "/pair K7QF-3MZD"
 bridge: 1 chat.type must be "private" (codes are never accepted in groups)
         2 from.id must be in ALLOWED_TELEGRAM_USER_IDS if that is set
         3 attempts ≤ 5 per chat per hour and ≤ 20 per bot per hour (closed on cache error)
         4 compare in constant time against unexpired, unused codes; mark used
           (conditional UPDATE)
         5 create TelegramChatLink(user, chat_id, telegram_user_id = from.id)
         6 reply with a disclosure ("Telegram can read everything relayed here") and the
           rooms list; send an in-app notification "Telegram account <name> (id) paired"
           so an unexpected pairing is visible
 mode A: /rooms lists the user's rooms; /room <id> binds thread_key=0
 mode B: /newtopic <room id> → bot checks getMe.has_topics_enabled → createForumTopic
         (private chat) → binding(thread_key = returned message_thread_id); a test
         sendMessage into the topic; on "message thread not found" (#905) mark the
         binding broken and tell the user in the main chat; never fall back silently
         to General
 mode C: user adds the bot to a private forum supergroup as admin with
         can_manage_topics; from that group the paired user sends /bind <room id>
         (from.id must equal the link's telegram_user_id) → createForumTopic → binding.
         The group needs its own TelegramChatLink row, created only by that command
         from an already-paired user, which the DM confirms.
```

---

## 5. Security analysis

| Threat | What an attacker gets | Mitigations |
|---|---|---|
| **Stolen bot token** | **Reading.** They can call getUpdates and consume or read incoming messages, which races our poller. **Sending.** They can send as the bot, for example fake approval cards. **Denial of service.** They can set a webhook, which stops our polling. [Inference] They cannot forge an update whose `from.id` is the paired user, because updates come from Telegram, and buttons they create carry tokens not in our database. **But they can tamper with a genuine card.** A holder who wins a getUpdates race sees the card's `chat_id`, `message_id` and callback data. With `editMessageText` or `editMessageReplyMarkup` they can change what the card says, or which button approves. They can also read the PIN message. | • Keep the token in env only. It is already excluded from the sandbox (`orchestration/shell_exec/backends.py:57-80`). Warn that the `open` shell profile runs on the host and can read `.env` (`docs/contracts/credential-scoping.md:84-90`). • The new `bot_api.py` never puts the URL in errors (TB-6). Never log file URLs. • Against card tampering: the callback's `message.message_id` must equal `card.message_id`, and the PIN or confirm step shows the action summary read from the database, not the card text. • Treat a 409 or a newly set webhook as possible compromise: disable every open card. • At startup call `getWebhookInfo`. If a webhook URL is set, refuse to poll, do not delete it, and send an in-app alert. • Pin `bot_id` from the first `getMe`; a changed id means the token was swapped, so stop. • Treat repeated 409 Conflict as "another poller holds this token": alert and back off. • Runbook: revoke in BotFather, rotate the env var, restart. |
| **chat_id spoofing** | There is no inbound endpoint (polling only), so nothing on the network can be spoofed. Inside Telegram, anyone can message the bot, and anyone in a group can post in a bound topic. | • Accept only `chat.id` with an active link **and** `from.id == link.telegram_user_id`, on every update and callback. • Reject `sender_chat` (anonymous admins, channels), `from.is_bot`, `via_bot`, guest updates. • Pairing codes: about 40 bits, 10 minutes, single use, hashed, private chats only, rate-limited per chat and globally. • Unpaired chats get no reply, or one generic reply per day, so the bot's activity is not revealed. • An in-app notification on every pairing. `/unpair` from the phone; `telegram_unpair` locally. • Per-update `user_has_room_access`. |
| **Forwarded-message injection** | Text written by someone else arrives with the owner's authority. | • Reject `forward_origin` and `is_automatic_forward` messages by default, saying why (D6). • Read only `text` or `caption`. Ignore `external_reply`/`quote` content, or pass it as labelled quoted data, never as the instruction. • Downstream protections still apply because the text is the query: `should_block_action` on `raw_query` (`tool_executor.py:117-123`), capability gates, risk bucketing and human approval. Pasted text cannot be told apart from typed text, and that is accepted, as on the web. • File contents go to the document path, not the query. |
| **Approval replay or mis-binding** | Re-using an old tap or callback; a stale card approving a newer action; a double tap; someone else in a group tapping. | • A random 96-bit token per card, stored hashed, single use through a conditional UPDATE. • Bound to approval pk + turn_id + short_id. All three must match the *current* pending row, because the row is reused (`agent_loop.py:839-851`). • `callback.from.id` must be the paired user; agent-loop approvals are requester-only already (`agent_loop.py:3983-3986`). • Cards are acted on only within `MAX_UPDATE_AGE`, and the row's own `expires_at` applies (`_claim_turn_for_resume` refuses expired pauses, `agent_loop.py:3861-3863`). • The decision goes through consent timing (`coordinator.py:127-175`). • Buttons are removed after any outcome, and lazily on a stale tap. |
| **Telegram reads the content** | Telegram's servers see everything relayed: prompts, replies, file contents, approval text, the PIN. Room messages are encrypted at rest in Kazi (`chatbot/models.py:43,49-60`); binding a room undermines that for what is relayed. | • Explicit disclosure at pairing, and in `docs/telegram.md`. • `CONTENT_MODE` (D5): `summary` sends status and short replies; `notify_only` sends "Reply ready in `<room>`" plus approve and reject only. • Outbound text is re-run through `redact_sensitive_text`. Status messages carry phase only, never tool inputs. • `callback_data` carries only the random token, never parameters. • The PIN is described as protection against someone holding your unlocked phone or a hijacked Telegram session, **not** against Telegram. Advise a PIN not reused elsewhere. • Recommend binding only rooms you would put in a cloud chat. |
| **Telegram account takeover (SMS hijack, lost phone)** | The attacker becomes the paired user. | • PIN for high-risk approvals (`PIN_POLICY`), with lockout and an in-app alert. • `/unpair` and the web settings, plus `telegram_unpair` locally. • Tell the user to enable Telegram two-step verification ([Known]: Telegram offers a cloud password). |
| **Stale backlog after downtime** | Telegram keeps updates for 24 h, so a message sent yesterday could run today. | • `MAX_UPDATE_AGE` (default 10 min). Older text is shown as "not run, resend". Older callbacks are refused. |
| **Group exposure (mode C)** | Other members read everything and see the cards. | • Recommend a private group with only the user and the bot. • Inbound accepts only the paired `from.id`. • PIN challenges always go to the link's private chat, never the group (§4.2 step 5); no DM, no approval. • Show a warning if `getChatMemberCount` is above 2 ([Inference]: the method exists in the Bot API; I did not check its current wording). |
| **Flooding and cost** | Many messages or files trigger turns and LLM spend. | • The existing queue cap, token quota and upload quota. • The bridge's own closed per-chat limiter. • Outbound pacing at one per second per chat; honour `retry_after`. |
| **Formatting injection** | MarkdownV2 or HTML in model output breaks sends or spoofs UI (fake buttons and links). | • Send plain text (no `parse_mode`), or HTML built with `html.escape`. Never pass model text as MarkdownV2. The connector's reminder path has this bug: `chatbot/tasks.py:555-559` uses the default MarkdownV2 without escaping (separate fix, §8). |

**Hooks the bridge must not skip.** Steps 3-7 of §4.1 and steps 1-7 of §4.2. Everything
from the turn runner onward runs unchanged, because the bridge enters through the same
seam as the WebSocket (§1.8).

---

## 6. Gap list: reuse versus build

| # | Item | Status | Size |
|---|---|---|---|
| G1 | Inbound seam `submit_user_message` (extracted from the consumer) | Missing; logic exists inline (`consumers.py:861-1057`) | M |
| G2 | Broadcast human messages to the room group, so web tabs see Telegram-sent text | Missing (`consumers.py:979`) | S |
| G3 | Public read of the pending approval (pk, turn, short id, risk) | Partly there: `pending_approval_prompt` (`agent_loop.py:3774-3801`), private `_pending_approval_record`; risk derivable via `is_high_risk_action` (`action_catalog.py:695`) | S |
| G4 | Structured approval event to clients | Missing; dropped at `coordinator.py:905`. Comes with v0.8 T-A2 (`approval.requested` / `approval.resolved`) | Part of T-A2 (+ TB-4 fields) |
| G4b | Typed per-turn frames (`turn.started`, tool events, one terminal event, `seq`) and a non-consumer subscription hook | Missing. This is T-A2 (#217), not started | Part of T-A2 |
| G5 | Bot API client with safe error handling and 429 handling | Missing; the connector's client is not reusable (§1.1) | S |
| G6 | Poller with lease, offset and webhook check | Missing; lease pattern reusable (`turn_leases.py`, `in_process_scheduler.py:209-258`) | M |
| G7 | Pairing and allowlist models and commands | Missing | M |
| G8 | Document ingest helper | Logic exists inline (`message_actions.py:221-346`) | S |
| G9 | Confirm whether document text is treated as untrusted in the context | Unknown | S (investigate) |
| G10 | Relay: status message, final reply, cards | Missing; the channel layer is reusable | M-L |
| G11 | PIN | Missing; Django `make_password`/`check_password` reusable | S |
| G12 | Topic modes B/C, including #905 handling | Missing | M |
| G13 | Telegram notification channel | Missing; hard-coded branches (`notifications/services.py:116-142`) | M |
| G14 | Bind the approval id into consent | Missing; the timing check covers most of it | M (contract minor) |
| G15 | Docs (`docs/telegram.md`, `.env.example`, architecture `model.js` scenario) | `docs/telegram.md` is wrong today | S |
| Reuse | Turn runner, coordinator, consent, resume, receipts, quotas, gates, redaction, `user_has_room_access`, `persist_kazi_message`, channel layer, `submit_execution_approval`, test harnesses | Reuse as-is | — |

---

## 7. Open decisions (each with my recommendation)

| # | Decision | Options | Recommendation |
|---|---|---|---|
| **D1** | When to build | **Decided (2026-11-08):** the freeze does not apply. Build from scratch on the v0.8 core | Order: seams inside T-A1/T-A2, then the bridge app once T-A2's frames exist (§0.1, §8). |
| **D2** | How a button approves | (a) feed "yes"/"no" through the chat path; (b) call `resume_after_confirmation` directly; (c) a new approve-by-id API | **(a).** No core or contract change, and it keeps every existing guard. (b) skips consent and the runner. (c) touches protected `agent_loop.py` for no gain. **TB-16 is required before TB-11 ships**, because without it a pause that replaces the card's approval can absorb the tap (§4.2). |
| **D3** | Where the poller runs | (a) lifespan task in the web process plus a database lease; (b) its own management command and compose service with its own `TurnRunner`; (c) Celery | **(a).** Works in lite and full, and inherits runner shutdown. (b) breaks in lite (in-memory layer, boot sweep at `turns.py:1176-1187`) and edits protected `docker-compose*.yml`. (c) is ruled out (§1.7). |
| **D4** | Which modes in v1 | A only; A+B; A+B+C | **A first, then B.** B depends on BotFather topic mode, and on Telegram fixing per-topic failures (#905, open). C adds group-exposure risk. Ship C last, with the member-count warning. |
| **D5** | Default content mode | full; summary; notify_only | **`full`, with a disclosure the user must confirm at pairing**, plus a per-binding override. The goal is remote control, and notify_only would make it useless. Because the disclosure is explicit, the default is an informed choice. |
| **D6** | Forwarded messages | reject; accept as quoted data | **Reject**, with "forwarded messages aren't run; copy the text if you mean it". Simpler and closed. |
| **D7** | Injection-check hit on inbound text | block; pass through (downstream blocks sensitive actions); pass plus taint the room | **Pass through, and show a warning chip in Telegram.** The owner wrote it. `should_block_action` already blocks sensitive actions on that query (`tool_executor.py:117-123`). Hard-blocking the owner's own text would differ from the web. Revisit when room taint for inbound messages lands (`docs/plans/170-taint-flag.md:15-16`). |
| **D8** | PIN policy | off; high-risk only; every approval | **High-risk only**, where high-risk means catalog `risk_level=="high"`, or a shell tier of destructive or egress. Set the PIN locally (`telegram_pair --set-pin`) or in web settings, never over Telegram. |
| **D9** | Mirror web-started runs to Telegram | yes; no | **Yes for status, and cards only for the bound user's own approvals.** Agent-loop approvals are requester-only anyway. |
| **D10** | Use `sendMessageDraft` instead of status edits | now; later | **Later.** Edits are well understood and rate-limit-safe at 3 s. Drafts are newer (9.3/9.5/10.3) and stream reply text, which this design does not need for v1. |
| **D11** | Raise `send_telegram_*` risk to require confirmation | yes (separate PR, protected `action_catalog.py`); no | **Yes, separately.** It is unrelated to the bridge, but the low rating is inconsistent with outside-world doctrine. |
| **D12** | Static allowlist env in addition to pairing | yes; no | **Yes, optional.** `ALLOWED_TELEGRAM_USER_IDS` empty means pairing alone. Cheap defence in depth. |
| **D13** | What happens to the v0.4.2 Telegram connector | (a) leave it; (b) rebuild it on `telegram_bridge/bot_api.py`; (c) retire it | **(b), after the bridge ships.** Its sends go through the new client, so the connector's error-handling and MarkdownV2 problems go with it. One Bot API client, one bot. Until then, keep it. Retiring it removes agent-initiated sends to arbitrary chats; that is your product call. |
| **D14** | Should T-A2's frames be in-process callbacks or channel-layer group messages | callbacks (sinks registered with the runner); group messages only | **Both: a sink registry in the runner that the consumer and the bridge both register with.** The channel layer stays the fan-out to browser sockets. In-process sinks avoid channels_redis capacity drops and expiry for the bridge, which runs in the same process (§1.5). This is T-A2 design input; decide it there. |
| **D15** | Voice architecture for Telegram | native speech-to-speech agent; chained (ear → text agent → mouth) | **Chained** (§3.5). Native voice belongs to live calls later: GPT-Live with Kazi as the delegated backend. |
| **D16** | Can a spoken "yes" approve | yes; no | **No.** Transcripts are `consent_eligible=False` (TB-M0); approve with the buttons. |
| **D17** | Default speech-to-text | OpenAI `gpt-transcribe`; local faster-whisper; Groq | **OpenAI by default, plus `openai_compatible`** so local or Groq is one setting. This adds a disclosure: audio goes to the STT provider as well as Telegram. |
| **D18** | When to reply with voice | always; reply in kind; never | **Reply in kind, plus `/voice`.** Text is always sent as well. |
| **D19** | How photos reach the agent | native image blocks; describe-first pre-pass | **Describe-first** (TB-M4). It works on all three providers with no protected-path change. Native blocks only if it proves insufficient. |
| **D20** | Which voice model speaks after 2027-01-06 | an OpenAI Realtime or audio model; a third-party or local TTS through `openai_compatible` | **Decide before January.** The mouth adapter takes the model from config; test an option before OpenAI removes `tts-1` and `gpt-4o-mini-tts`. |
| **D21** | Depend on Telegram Premium | yes; no | **Decided (2026-11-13): no.** Premium adds only cosmetic bot features (§3.6). |
| **D22** | Default UI for approval cards and status in private chats | inline buttons and text status; widgets (10.4) | **Buttons by default; widgets opt-in per binding** (`/ui widgets`) once a test widget renders on your device. Groups always use buttons. Revisit the default once widget support across Telegram apps is known. |

---

## 8. Phased implementation plan (small, test-first tickets)

Every ticket follows `docs/golden-principles/tests-and-evals.md`:
- show each new test failing for the right reason before the change;
- mock only at the Telegram HTTP boundary (the `bot_api` transport) and the LLM
  (`_FakeLLM`);
- use the real runner, database and channel layer;
- run CI's blocking commands (`:68-80`).

**Approach.** The freeze does not apply, and the bridge is built from scratch on the v0.8
core. There is no interim build on today's frames or on the v0.4.2 connector.

**Three tracks.**
- **Track 1** is core work. It belongs inside the v0.8 Phase A tickets you are on now.
- **Track 2a** needs only TB-1. TB-1 itself waits for 216b-3, so 2a starts once
  TB-1 merges.
- **Track 2b** needs T-A2's frames.

**Protected paths and contracts touched:**
- migrations (TB-5, TB-15);
- `docs/contracts/approval.md` (TB-16, minor bump);
- `docs/contracts/turn-stream.md`, which is T-A3's own deliverable;
- `.env.example` (TB-18).

All of these need a plan and a human OK.

### Track 1: core strengthening (inside v0.8 Phase A)

| Ticket | Lands in | What | Tests (each shown failing first) | Size |
|---|---|---|---|---|
| **TB-1** | #216, after 216b-3 | `chatbot/inbound.py::submit_user_message(room, user, text, *, via, within_txn=None)`, extracted from `ChatConsumer.new_message`; the consumer becomes transport only, as #216 asks. `within_txn` runs inside the same transaction as the `Message` and queued-`Turn` writes, and `runner.submit` happens after commit (used by TB-8 and TB-11 for crash-safe dedupe and card decisions) | (1) existing `test_turn_runner` WebSocket tests unchanged; (2) a message submitted through the seam, with no socket, runs a turn whose reply is saved; (3) a "yes" through the seam resumes a real paused turn (`_PauseHarness`, `CONSENT_SKEW_SECONDS=0`) and writes a `basis="human"` receipt; (4) a non-member is refused and **no** `Message` row is created (fixes the orphan row at `consumers.py:935` vs `:958`) | M |
| **TB-4** | #217 T-A2 | (a) `approval.requested` carries `approval_pk`, `short_id`, `approval_kind` and a risk class; `approval.resolved` carries `approval_pk`, `decision` and `via` (today's `confirmation` event calls the short id `approval_id`, `agent_loop.py:3596`); (b) a frame-sink registry on the runner that the consumer registers with, and any in-process client can too (D14); (c) read helper `orchestration/approval_view.pending_agent_approval` | Every frame carries `turn_id` and a monotonic `seq`; a second registered sink receives the same frames as the consumer; a sink that raises never aborts the turn (T-A2's best-effort rule); the reused-row case returns the *new* turn_id and short_id; another user's row is invisible | M |
| **TB-2** | #217 T-A2 | A user-message frame (for example `message.received {turn_id, message_id, via}`) so every client sees human messages from other surfaces | Two clients; B receives A's message with `via` | S |
| **TB-16** | #217 T-A2, contract in #218 | A decision submitted for a specific `approval_id` (through `submit_user_message(..., decision_for=approval_id)`) is refused by the coordinator if the pending row's id or turn differs. Minor bump to `approval.md` | The §4.2 race, forced with an event at the seam: a stale decision is refused and the new pause stays pending | M |
| **TB-A3** | #218 T-A3 | Write the client contract doc for *a client*, not the browser. The duplicate, reorder, reconnect and terminal fixtures run against a generic test sink, so the bridge plugs in later | Fixtures pass for the consumer and for the test sink | S (on top of T-A3) |
| **TB-3** | independent | `chatbot/documents.py::ingest_room_document`, extracted from `upload_document_to_ai` | Quota, type and size limits; the view's existing tests still pass | S |
| **TB-G9** | independent (investigation) | Does document text in `ContextManager` count as untrusted or tainted? Write the answer down and file an issue if not | — | S |

### Track 2a: bridge foundation (needs TB-1 only; new app, plan first because of migrations)

| Ticket | What | Tests | Size |
|---|---|---|---|
| **TB-5** | Models and migrations (§3.3) | A second active link for a chat is refused and a revoked one is allowed; conditional-UPDATE helpers have exactly one winner under a barrier race | M |
| **TB-6** | `bot_api.py`, from scratch | A 429 with `retry_after` waits for it (fake clock); `ok:false` maps to a typed error; the token never appears in `str(error)` or in logs (`assertLogs`); per-chat pacing allows at most one send per second | S |
| **TB-7** | Pairing: `telegram_pair` / `telegram_unpair` commands and the `/pair` handler | Expired, used, wrong, group-chat and over-limit codes are refused; a cache error refuses (closed); success binds `from.id` and emits the in-app notification | M |
| **TB-8** | Poller and lifespan wiring | A webhook already set → no polling, plus an alert; two pollers → exactly one holds the lease (barrier); restart resumes from the stored offset with no duplicate `Message`; a failure injected inside step 7's transaction leaves neither the `TelegramInbound` row nor the `Message`, and the replay then produces exactly one `Message`; updates older than `MAX_UPDATE_AGE` do not enqueue; a `bot_id` change stops the poller; lifespan stop happens before runner shutdown | M |
| **TB-9** | Inbound routing, mode A (`/rooms`, `/room`, `/status`, `/stop` recorded as a person's cancel through 216b-3's `cancel_requested_at`) | One test per filter, each failing when its guard line is removed: wrong `from.id`, `sender_chat`, `is_bot`, forwarded, guest, revoked link, no room access. Text reaches `submit_user_message` with `via=telegram` | M |
| **TB-13** | Files: `getFile` → `ingest_room_document` (needs TB-3) | An oversized declared size never calls `getFile`; the type is sniffed (a PDF named `.jpg`); the token URL never appears in logs; a caption turn starts after the document completes (event-based wait) | M |

At the end of 2a you can talk to Kazi from the phone. Replies come back only once the
relay ships in 2b. A minimal "reply ready" fallback is possible by watching
`Turn` terminal states in the database, but I don't recommend building it, since 2b
replaces it.

### Track 2b: relay and approvals (needs T-A2 merged)

| Ticket | What | Tests | Size |
|---|---|---|---|
| **TB-10** | Relay registered as a frame sink (TB-4b): status message per `turn_id`, edits on tool events, terminal frame closes it, final reply read from the persisted message | Real runner and `_FakeLLM`: one status message per turn; edits at most every N seconds with identical text skipped; `turn.failed{partial}` renders as "Ended", never "Done"; duplicate or reordered `seq` is ignored; the reply is split at 4096, plain text; a fake `sk-…` key in model text never reaches the fake transport; restart resumes by `turn_id` + `seq`; the bridge stopped after "Thinking" while the turn completes → on restart the status closes with its ending and the reply is sent once; a crash injected after the fake transport accepts the send but before `reply_sent_at` is saved re-sends it once with the restart prefix, and once that resend is recorded a further restart sends nothing; a crash after the status edit but before the reply write leaves `closed_at` empty, so the reply is still sent on restart | M |
| **TB-11** | Approval cards from `approval.requested`; decision through `submit_user_message(..., decision_for=…)` (TB-16) | Approve resumes and writes a receipt; reject cancels; a double tap gives exactly one decision (barrier); a stale card after row reuse is refused; another Telegram user is refused; an expired card is refused; `approval.resolved` from the web closes the card; `answerCallbackQuery` is always called; a failure injected between the card UPDATE and the decision `Message` rolls both back so a re-tap works; a reaped decision Turn with the approval still pending gets a fresh card on restart | M |
| **TB-11b** | "Continue" buttons for budget and quota pauses. Needs 216b-3's `paused_at` consent check | A "continue" queued before the budget pause existed does not resume it (mirror `test_a_queued_yes_cannot_approve_a_prompt_it_never_saw`) | S |
| **TB-12** | PIN for high-risk approvals, challenged only in the link's private chat | A high-risk card asks for the PIN in the DM even when the card is in a group topic; an unreachable DM refuses the approval; a PIN typed in the group is ignored and deleted; a wrong PIN ×5 locks the link and notifies; the PIN message is deleted; a low-risk card skips the PIN | S-M |

### Track 3: after the bridge

(There is no TB-17: it was the interim adapter for today's frames, dropped when the bridge
moved to v2 frames only.)

| Ticket | What | Size |
|---|---|---|
| **TB-14** | Modes B and C: `/newtopic` and `/bind`, the `has_topics_enabled` check, and handling of "message thread not found" (#905): mark the binding broken and never post to General | M |
| **TB-15** | Telegram notification channel and workflow approval cards through `submit_execution_approval`. Owner and status checks; the `ApprovalMismatch` 409 path (reuse `_submission_env`). Adds `delivered_telegram` (a migration) | M |
| **TB-19** | D13: rebuild the v0.4.2 connector's sends on `bot_api.py`, or retire it | S |
| **TB-18** | Docs and architecture: rewrite `docs/telegram.md` (setup, modes, disclosure, rotation runbook); add the variables to `docs/configuration.md` and `.env.example`; update `docs/architecture/model.js` (an external "Telegram Bot API" node, a bridge module in `web`, edges, a "Telegram approval" scenario); run `python scripts/check_architecture_model.py`; add the bridge to `kazi_status`. Ship this with TB-8/TB-10, not at the end | S |

### Track M: multimodal (§3.5)

| Ticket | Needs | What | Tests (each shown failing first) | Size |
|---|---|---|---|---|
| **TB-M0** (core, small) | TB-1 | `consent_eligible` flag on `submit_user_message`. When it is False, the coordinator does not treat the text as an approval, decline or continue reply (`coordinator.py:1218-1236,1346-1431`) | A transcribed "yes" leaves a real paused Turn paused and its approval pending; a typed "yes" still approves | S |
| **TB-M1** | TB-M0, TB-9 | Voice in: download, OGG→WebM stream copy, ear adapter, transcript echo, `via="telegram_voice"`; the transcript is stored encrypted | The fake STT transport receives an allowed format, never raw `.ogg`; the stored message decrypts to the transcript and is not plaintext; over the duration cap → refused with no turn; STT error → honest reply with no turn | M |
| **TB-M2** | TB-10 | Mouth adapter (one module replacing `generate_voice_response` and the `generate_speech` delivery gap); reply in kind plus `/voice`; the text is always sent; MP3 to `sendVoice`; a character cap | A text-only reply when the user typed; a voice plus text reply when the user sent voice; over the cap → text only; audio bytes never reach the model context (the connector result has no base64) | M |
| **TB-M3** | TB-9, TB-3 | Photos stage 1: store, honest note, caption as text, album grouping by `media_group_id` | Three updates sharing one `media_group_id` become one room message and at most one turn; the note names the room's model | S |
| **TB-M4** (core, plan) | TB-3 | Photos stage 2: describe-first vision pre-pass into `processed_text`, framed untrusted in the context prompt, cached by `file_unique_id`, `VISION_MODEL` config | The description reaches the context inside an untrusted frame; an image whose text says "ignore previous instructions" does not unlock a sensitive action; a second identical photo makes no second vision call | M |
| **TB-M5** (core, later) | T-A2, plan | Photos stage 3: native image blocks. Only if TB-M4 proves insufficient | — | L |

### Track W: newer Bot API features (§3.6; after the matching 2b ticket)

| Ticket | Needs | What | Tests (each shown failing first) | Size |
|---|---|---|---|---|
| **TB-W1** | TB-11 | Approval widget. A static template in the repo; card state in `chat_data`; taps go through the same handler as buttons; `answerWidgetQuery` within 15 s; fallback to buttons | The template is ≤32 KB and ≤4 KB at zlib level 9 (CI test). The template uses `textContent` and never `innerHTML` on data. A widget tap and a button tap with the same token produce the same decision through one handler. A double tap gives one winner. A stale card is refused. A `sendWidget` error falls back to buttons. A mode C binding always gets buttons | M |
| **TB-W2** | TB-10, TB-W1 | Status widget per run in private chats, replacing the edited text status where the binding uses widgets | One widget per turn; `chat_data` edits throttled; `turn.failed{partial}` renders "Ended", never "Done"; restart recovery follows the same `closed_at`/`reply_sent_at` rules; the final reply is still a normal message | M |
| **TB-W3** | TB-14 | "Open in Telegram" link on the web room page for mode B bindings (`https://t.me/<bot>/<thread_id>`) | The link appears only for the bound user and only for topic bindings | S |
| **TB-W4** | TB-11 | Ephemeral replies to button taps for throwaway notes (stale card, PIN lockout, not your card) | The note goes to the tapping user only; nothing durable is sent ephemerally (a static test that cards and replies never use ephemeral parameters) | S |

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

- Whether private-chat topics display for users without Premium.
- Telegram's real `editMessageText` limits.
- Whether Telegram states anywhere official that bot chats are not end-to-end encrypted.
- The current state of tdlib #854. Only the search-result title was seen.
- Whether existing CI would pass after TB-1. No code was changed or run.
