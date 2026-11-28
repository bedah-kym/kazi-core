# Turn Stream Contract

**Version:** 2.0 (draft) · **Status:** draft · **Tier:** documented only

The frames a chat WebSocket sends a client that opts in to protocol v2, and
the commands the client may send back. v1 stays as it is for clients that
don't opt in.

Planned consumers: the redesigned web client (#308) and the Telegram bridge
(`docs/design/telegram-bridge.md`). P3-7 adds the JSON Schema and a reference
reducer, and marks this contract stable. While it is a draft, a field may
still change without a deprecation cycle (see the README's draft rule).

## Choosing a protocol

A socket starts on v1. It moves to v2 in one of two ways:

- **`hello`, the default.** The client sends
  `{"type": "hello", "protocols": [2, 1]}`, in order of preference.
- **Subprotocol.** The client offers `["kazi.v2", "kazi.v1"]` in the WebSocket
  handshake. The server always accepts with one of them, because a browser
  fails a handshake that offered a subprotocol and got none back.

The server answers once per socket:

```json
{"v": 2, "type": "session.ready", "protocol": 2, "resumable": false}
```

- `protocol` is the one chosen: `2`, or `1` when v2 is switched off.
- `resumable` is `false` in 2.0. It becomes `true` when `turn.resume` exists
  (217a-3).
- Frames sent before `hello` is handled are v1. A second `hello` is ignored.
- **Kill switch:** `KAZI_STREAM_V2=false` makes the server choose v1 for every
  new socket: `hello` gets `protocol: 1`, and a subprotocol offer gets
  `kazi.v1`. The setting is read on startup. Open v2 sockets stay on v2 until
  they reconnect.

## Envelope

Every v2 frame has `v: 2` and a `type`. Clients **must ignore** a `type` or a
field they don't know.

Frames about a turn (*turn frames*) also carry:

- **`turn_id`** (string UUID): the turn's durable id. A turn paused for
  approval keeps its id when the user answers "yes".
- **`seq`** (integer): the frame's place in the turn.

### `seq` rules

- While a turn runs, its frames are numbered `0, 1, 2, …` with no gaps and no
  repeats, and numbering carries on across a pause and resume.
- **A terminal frame is always applied, whatever its `seq`.** A turn can be
  ended by a writer outside the run that holds it (the reaper, a stop, a reset,
  a newer turn). That writer takes the next number the server has recorded,
  which can repeat or skip one the run already sent if the run died mid-turn.
  A client must never drop a terminal frame as a duplicate or hold it back for
  a gap.
- A turn that ends before it ever runs (for example, a queued turn that
  expires) may have only its terminal frame.

Frames of different turns can interleave on one socket. A client keys its
state on `turn_id` and orders it by `seq`.

**A gap in `seq`** means a frame was lost (the server could not deliver it,
or the socket dropped). 2.0 has no replay, so the client should treat the
turn's live text as incomplete and show the saved message that the terminal
frame names. 217a-3 adds `turn.resume`.

## Who receives turn frames

Every socket subscribed to the room gets the room's turn frames, in its own
negotiated protocol. A v1 socket in the same room keeps getting v1 frames.
Only the turn's owner can answer an approval or cancel the turn. The server
enforces this, whatever a client shows.

## Turn frames

### `turn.started`

```json
{"v": 2, "type": "turn.started", "turn_id": "…", "seq": 0,
 "request_message_id": 812, "model": "claude-…"}
```

The first frame of a turn that runs. `request_message_id` is the user's
message that started it. `model` may be `""` when no model is chosen yet.

### `turn.resumed`

```json
{"v": 2, "type": "turn.resumed", "turn_id": "…", "seq": 9, "request_message_id": 815}
```

A paused turn is running again after its owner's answer. `request_message_id`,
when present, is the message that resumed it.

### Parts: `part.started`, `text.delta`, `part.ended`

```json
{"v": 2, "type": "part.started", "turn_id": "…", "seq": 1, "part_id": "p0", "channel": "narration"}
{"v": 2, "type": "text.delta",   "turn_id": "…", "seq": 2, "part_id": "p0", "text": "Let me check."}
{"v": 2, "type": "part.ended",   "turn_id": "…", "seq": 3, "part_id": "p0"}
```

- Model text arrives in parts. `channel` is `narration` (text before a tool
  call) or `answer` (text the turn ends with).
- A new part starts whenever the channel changes or a tool call comes between
  two pieces of text. `part_id` is unique within the turn, including across a
  resume.
- `text` is appended as-is. v2 adds no separators; a client that shows parts
  together chooses its own spacing.
- A part normally ends with `part.ended` before `turn.paused` or the terminal
  frame. **A terminal frame closes any part still open**, because a writer
  outside the run cannot send the missing `part.ended`.
- Only the answer is saved and replayed to the model. Narration is not shown
  again after a reload.

### `notice`

```json
{"v": 2, "type": "notice", "turn_id": "…", "seq": 7, "kind": "info", "text": "…"}
```

Text from Kazi itself, not the model: an approval prompt, a budget or quota
message, an error notice. In 2.0 `kind` is always `info`; later versions add
specific kinds, and clients must accept any. A notice that is saved into the
turn's reply also appears in that saved message.

### `turn.paused`

```json
{"v": 2, "type": "turn.paused", "turn_id": "…", "seq": 8,
 "pause_kind": "approval", "expires_at": "2026-11-28T07:40:00Z"}
```

The turn is waiting for its owner. `pause_kind` is `approval`, `budget` or
`quota`. `expires_at` (ISO 8601, UTC) is when the pause lapses; it may be
`null`. The prompt arrives as a `notice` before this frame. A paused turn later
gets `turn.resumed` and more frames, or a terminal frame.

### Terminal frames

A turn gets **exactly one** terminal frame, whoever ends it. Nothing for that
turn follows it. The one exception: a turn whose room was deleted has nobody
left to tell, so it gets none.

| `type` | Fields | When |
|---|---|---|
| `turn.completed` | `final_message_id`, `final_text?`, `end_reason` | The turn finished and its reply is saved. |
| `turn.failed` | `reason_code`, `partial`, `final_message_id?` | The turn could not finish, including when the server shut down mid-turn (`shutdown`). |
| `turn.cancelled` | `reason`, `partial` | The owner stopped or declined it, or a reset ended it. |
| `turn.superseded` | `by_turn_id` | Another turn carries on instead: a newer pause replaced this one, or this turn was the owner's "yes" and the paused turn it answered took over (`by_turn_id` is that paused turn). |
| `turn.expired` | `reason` | It waited too long while paused (`pause_expired`), or it was still queued when the server restarted (`restart`). |

- `final_message_id` is the saved reply's message id.
- `final_text`, when present, is that message's text: the answer plus any
  saved notices, never narration. A writer outside the run may leave it out;
  the client then shows the saved message `final_message_id` names.
- `partial` is the flag the server recorded: `true` means it knows some of the
  turn's work ran or some text was shown before the end. `false` means none was
  recorded, not proof that nothing ran.
- `end_reason`, `reason_code` and `reason` are short snake_case codes, such as
  `finished`, `error`, `shutdown`, `user_cancel`, `reset`, `continued_pause`
  or `pause_expired`. Clients must accept codes they don't know.
- **A failed delivery never hangs a turn.** If the server cannot send a frame,
  the turn still ends, and its terminal frame is still attempted.

## Other frames

### `error`

```json
{"v": 2, "type": "error", "code": "cancel_denied", "message": "…", "turn_id": "…"}
```

Something the client sent was refused, or the server has a problem it can
report. It has no `seq`. `turn_id` is present when the error is about a turn.
`message` is fixed server text. It never repeats client input or an
exception's text. This replaces v1's system frames that carry no `command`.

### Frames this version does not define

In 2.0, other frames still reach a v2 socket in their v1 shape: presence,
typing, quota and saved-message announcements, among others. They have
`command`, not `v`, so a client tells them apart by the `v` field. 217a-2b
wraps them as v2 frames and adds tool calls, approvals, `turn.queued` and
`message.created`.

## Client commands

### `hello`

See *Choosing a protocol*.

### `turn.cancel`

```json
{"type": "turn.cancel", "turn_id": "…"}
```

- **Checks, in this order:** the turn must be in this socket's room, and the
  sender must own it. If either fails, or the check itself errors, the server
  answers `error {code: "cancel_denied", turn_id}` and changes nothing. So a
  refusal never reveals whether a turn exists.
- **A turn that has already ended** gets `error {code: "turn_ended", turn_id}`.
- **A running turn** is stopped exactly as if the owner typed `stop`. Like
  `stop`, this also cancels the owner's queued turns in this room.
- **A paused turn** is cancelled, as if the owner declined it.
- **A queued turn** is cancelled on its own.
- On success there is no separate reply. Each affected turn's terminal frame
  (`turn.cancelled`, or whatever ending won the race) follows.

## Changes since v1

v1 is the chat stream used before this contract: `ai_stream` chunks with
`is_final`, step events and `ai_message_saved`. It has no turn id and no
order. This contract does not change v1, which stays the default.

## Breaking changes

None. This is the first v2 version.
