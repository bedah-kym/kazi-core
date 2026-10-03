# Plan: taint outlives the run (follow-up to #170 and shell auto mode)

Status: approved by Bedan on 2026-10-03 (15-minute default accepted) and built.

## Goal

Once untrusted text enters a conversation, the taint gate stays up for the
following turns in that room, instead of resetting on the next message.

## Why

Taint lives on `LoopState`, which is created fresh for every message. Found in
a live test on the `open` profile (2026-10-03): a tainted run paused on a
command; an unrelated second message dropped the pause, started an untainted
run, and the model re-issued the same command, which ran with no prompt
(receipt basis `open_profile`). The untrusted text is still in the chat history
and the workspace, so the reset has no justification.

## Non-goals

- No change to what counts as untrusted, or to which actions taint gates.
- No manual "clear taint" command. It expires.
- No change to the autopilot window: on `open` it remains the explicit way to
  work without prompts.

## Touches

- Paths: `Backend/orchestration/agent_loop.py`, `docs/configuration.md`,
  `Backend/orchestration/test_taint_persistence.py`.
- Protected paths? yes — `agent_loop.py`.
- Contracts, migrations, approvals, payments, secrets? none. State is one cache
  key per room.

## Risk class of any new action

No new action. This only raises prompts.

## Approach

1. `_taint_run(state, room_id)` replaces the three `state.tainted = True`
   sites: it taints the run and sets `agent_loop_taint:{room}` in the cache for
   `AGENT_TAINT_TTL_SECONDS` (default 900). The window restarts each time new
   untrusted output arrives; plain chat does not extend it.
2. The key is **room-wide**: chat history is shared by every member, so one
   member's tainted run taints everyone's next run in that room.
3. A new run starts with `tainted=_conversation_tainted(room_id)`; a resumed
   run re-checks it, in case another member tainted the room during the pause.
4. Fail closed on read: a cache error counts as tainted. A failed write cannot
   be made safe, so it is logged as an error and emits `taint_persist_failed`.
5. `0` disables persistence and restores per-run taint. The setting is read
   from the environment in `settings.py`.

## What changes for the user

- `standard` / `locked`: nothing for local commands (they auto-run when tainted
  anyway). Unlisted network already asks; an **allowlisted** host now also asks
  for the window, not just for the rest of the run.
- `open`: for 15 minutes after any shell or web-search output, every shell
  command asks unless autopilot is armed. Today each new message gets one free
  batch.
- Non-shell: a stored "auto" rule for send / pay style actions is escalated to
  a prompt for the same window, not just for the rest of the run.

## Verification (executable)

- `manage.py test orchestration.test_taint_persistence` — unit (per room, TTL 0,
  junk TTL, read and write failure) and two-turn loop tests: the next turn's
  first command asks, also for another member; another room is unaffected;
  sandboxed local commands and an armed window still run; a stored auto rule
  for `send_email` is escalated.
- Full suite, flake8, bandit, `check_boundaries.py`.
- Injection corpus cases added: none (the corpus scores message-level
  detection only).

## Rollback

Set `AGENT_TAINT_TTL_SECONDS=0`, or revert the commit. No stored data to clean.

## Open questions for the human

1. Is 15 minutes the right default window?
2. In-memory dev mode keeps the key per process; production needs the shared
   Redis cache it already uses for loop state. Acceptable?
3. The cache must not evict the key early; an evicted key reads as untainted.
