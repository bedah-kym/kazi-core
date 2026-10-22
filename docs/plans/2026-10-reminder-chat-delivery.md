# Plan: reminders set from chat are delivered in chat
Status: approved by Bedan on 2026-10-21 (found in T-F5 live QA on PR #274, 2026-10-17)

## Goal
A reminder set from a chat room always arrives in that room. Email, WhatsApp and Telegram
are extra channels that are added only when the user asks for them and they work on this
install. The reply says which channels will be used, and a failed extra channel is written
on the row without losing the chat delivery.

## What happens now (traced on `main` `181f1f7`)
- `ReminderConnector.execute` (`tool_router.py` ~900): no `delivery` from the model means
  `auto`, stored as `via_email=True, via_whatsapp=True`. `urgent` forces `email`. The reply
  ("I've set a reminder: ... for 10:02 AM") names no channel. No check that a channel works.
- `_deliver_reminder` (`chatbot/tasks.py` ~598), mode `auto`: posts to the room **only if
  presence says the user is online**, else Telegram, else email. In the lite stack presence
  is in-process (`chatbot/presence.py` `_local_user_online`), so the worker that fires the
  reminder sees the user as offline. No Mailgun keys means a mock send, which counts as a
  failure, so `send_reminder` dead-letters the row after one attempt ("dead letter after 1
  delivery attempt(s) on email"). The in-app notification was created, but the room gets
  nothing and the row says `failed`.
- Mode `in_app` (both flags False) creates the in-app notification and returns `sent`; it
  does not post a room message.
- Mode `whatsapp`: `_send_message_sync` returns `{"status": "sent", "mock": True}` under
  DEBUG without Twilio, and `_deliver_reminder` counts that as delivered.

## Non-goals
- No new reminder status, no migration (the flag encoding of modes stays).
- No change to the presence service, to quiet hours, or to the missed-window policy
  (T-H item in `docs/proposals/2026-10-turn-state-and-verification.md`).
- No new channels; Telegram stays as it is today (sent when the user is away).

## Touches
- Paths: `Backend/orchestration/tool_router.py` (ReminderConnector only),
  `Backend/orchestration/action_catalog.py` (`set_reminder` descriptions of `delivery` and
  `urgent`), `Backend/chatbot/tasks.py`, one new helper module
  `Backend/chatbot/reminder_channels.py`, tests in
  `Backend/orchestration/test_reminder_delivery_policy.py`, `docs/connector-api-reference.md`
  if it describes reminder delivery.
- Protected paths? **yes**: `tool_router.py`, `action_catalog.py`.
- Contracts, migrations, approvals, payments, secrets? None. It changes who gets a message
  (AGENTS.md §5 "anything that sends a message"): fewer outside sends, never more.

## Risk class of any new action
No new action. Delivery to the user's own room is reversible-in-workspace; email,
WhatsApp and Telegram stay outside-world and are sent less often than today.

## Approach
1. `reminder_channels.py`: `channel_ready(user, channel) -> (bool, reason)` for `email`
   (real Mailgun keys, or Gmail app credentials **and** the user's connected Gmail; the user
   has an address), `whatsapp` (Twilio client and a user phone), `telegram` (bot token and a
   connected chat id). A demo-mode mock is never "ready".
2. Creation (`tool_router.py`): `auto` is stored as the auto marker (both flags) without
   adding email; it names chat only (plus Telegram when that is set up). A requested
   email/WhatsApp channel that is not ready is dropped and the row is stored chat-only. The
   reply always names the channels, e.g. "I'll remind you here in chat", "here and by
   email", or "Email isn't set up on this install, so I'll remind you here in chat." The
   result carries `channels: [...]` and the stored `delivery`. A reminder with no room says
   "as an in-app notification". The dedupe update path does the same.
3. Delivery (`tasks.py`): every mode posts to the room when there is one (no presence
   check); with no room the in-app notification is the base. The base decides `sent`; the
   room post happens once, not on retries. `auto` never adds email: it adds Telegram only
   when the user is away at fire time. Other extra channels go to a separate task that
   re-checks `channel_ready` at fire time, retries only urgent ones in the queued worker
   profile (existing backoff), and on final failure appends to `error_log`, e.g. "email
   failed after 6 attempt(s): not sent; delivered in chat". A mock reply is a failure on
   every channel.
4. Dead letter only when the base fails (room post and notification both fail).
5. `action_catalog.py`: `delivery` says the room is always used; `urgent` "adds email with
   retries when email is set up", not "forces email".

## Verification (executable)
- Tests written first; each must fail on `main` (run against a scratch copy of `main`):
  - D1: `auto` reminder, presence offline, no email keys, room set: `send_reminder` posts a
    room `Message`, status `sent`, no dead letter. Same for `in_app` mode.
  - D2: `set_reminder` with no `delivery`: reply names chat only, even with Mailgun keys
    ready. `delivery="email"` with Mailgun keys patched in: reply names chat and email;
    `delivery="email"` and `urgent=True` without email keys: row stored chat-only, reply
    says email isn't set up. Demo-mode mock is not ready.
  - D3: email mode, keys present, send fails: room message posted once, status `sent`,
    `error_log` names the email failure. Urgent: email retried in the queued worker profile;
    in the eager/lite profile one attempt is made (the tick is not held for the backoff),
    room posted once, final `error_log` says delivered in chat. WhatsApp mock reply is not
    counted as delivered.
- Existing tests that encode the old routing change on purpose, each named in the PR:
  `test_auto_offline_prefers_telegram_then_email`, `test_urgent_forces_high_priority_and_email`,
  `test_urgent_with_auto_delivery_resolves_email_only`, `test_dead_letter_marks_failed_and_notifies`,
  `test_auto_delivers_in_chat_when_online` (presence no longer gates the room post).
- Commands (serial, Windows): `python Backend/manage.py test chatbot orchestration`,
  `flake8 Backend --max-line-length=127 --max-complexity=10`, `bandit -r Backend --skip B101,B110`,
  `python scripts/check_boundaries.py`.
- Live, lite stack without Mailgun, room 2: "@kazi remind me in 2 minutes to stretch"
  (reply names chat; message arrives in the room; row `sent`); "remind me by email in 2
  minutes to drink water" (reply says email isn't set up; arrives in chat); with fake
  Mailgun keys that fail, "urgent: remind me in 2 minutes" (arrives in chat once; row `sent`
  with the email failure in `error_log`); tab closed during the wait (message is in the room
  on reload).
- Failure paths covered: no room, notification failure, channel unconfigured at fire time,
  mock sends, retries.
- Injection corpus cases added: none (no new untrusted input path).

## Rollback
Revert the PR. No schema or data change; rows created under the new code read the same
flags as before.

## Decisions (Bedan, 2026-10-21)
1. Urgent reminder without email: deliver in chat and say email isn't set up.
2. Telegram stays an automatic extra when the user is away.
3. Chat delivered, extra channel failed: status `sent`, failure in `error_log` (no migration).
4. DeepSeek codes it from this plan; the lead does QA.

## Departures
- For a reminder created in a room, the persisted room message is the base delivery. A
  realtime `group_send` failure is logged but no longer turns a stored message into a failed
  delivery (that used to cause retries and duplicate room posts). The in-app notification is
  still created, but it does not mask a failed room post: the "always arrives in the room"
  goal wins.
- `agent_prompts.py` reminder guidance was updated alongside the tool descriptions so the
  model stops describing presence-based online/offline routing and "urgent forces email".
- `auto` stays a stored marker but no longer adds email at creation or fire time. It names
  chat only (adding "and on Telegram when you're away" when Telegram is ready) and adds
  Telegram only when the user is away at fire time. Explicit `email`/`whatsapp` still add
  those channels when ready.
- In the lite/eager profiles the extra-channel task runs with `.apply` (same convention as
  `_dispatch_due_reminder`); otherwise it is queued normally. Urgent extras are limited to
  one attempt there because an inline `self.retry` would run every backoff attempt inside
  the scheduler tick with no real delay; the failure is written to `error_log` the same way
  as the final failure in the queued profile.
