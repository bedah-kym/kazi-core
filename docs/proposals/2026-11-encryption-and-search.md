# Encryption at rest: review, small fixes, and the crypto for blind search

**Date:** 2026-11-26
**Status:** proposal, not filed. No code changed.
**Targets:** home labs first, small VPS hosts second.
**Basis labels:** `[Verified]` = read in this repo at commit `961596e` or measured;
`[Known]` = from general crypto knowledge, not re-checked here; `[Analysis]` = design judgment.

This proposal extends **F-80** (keyed word index) in
[`2026-11-frontend-redesign.md`](2026-11-frontend-redesign.md). It answers F-80's open
questions on keys and rotation. It **amends** F-80 in two places, both called out in §5:
prefixes start off, and a source is indexed only once it is encrypted at rest.

---

## 1. Verdict

Keep the current design. Do not replace it.

Kazi already uses envelope encryption `[Verified]`:

- A master key `ENCRYPTION_KEY` (Fernet) wraps one random AES-256-GCM key per room
  (`Chatroom.encryption_key = "enc:" + Fernet(room_key)`, `Backend/chatbot/models.py:49-60`).
- Message bodies, tool-call bodies and paused loop state are encrypted with the room key,
  using random 12-byte nonces.
- Tool calls and paused state also bind associated data (AAD) in a `{"v":1,...}` envelope
  (`orchestration/tool_calls.py:135-170`, `orchestration/turns.py:775-818`). `v` is written
  but not yet checked on decrypt.
- Write paths fail closed: when encryption returns nothing, nothing is stored
  (`consumers.py:979`, `kazi_messages.py:193`).
- The room key never reaches clients.

For a home lab or a VPS, this is the right shape. It protects the likely loss: a database
dump, a backup or a disk that leaks without the key. It is cheap, needs no KMS, and works on
SQLite `[Analysis]`.

The gaps are around the scheme, not in it:

- Plaintext copies of content sit next to the ciphertext.
- Key rotation is missing.
- The read path accepts plaintext rows as genuine.
- A few docs and code comments are wrong.

**Threat model, stated plainly `[Analysis]`:**

- This is encryption at rest, not end-to-end. The server holds every key.
- It does **not** protect against someone who controls the running host, or who has both the
  data and the key.
- On a home lab, the bigger practical risks are:
  - **Losing the key**, which loses every room.
  - **Backing up the data and the key together.**
    - On Postgres, that means the volume and `.env` in one backup.
    - On the SQLite/dev default, both files sit in `Backend/`: `db.sqlite3`
      (`settings.py:339`) and `.encryption.key` (`users/encryption.py:86`). Any backup of the
      app directory takes both.
- Someone with database **write** access can insert rows the app reads as genuine history
  (see E8).

## 2. Findings

| # | Finding | Where | Severity |
|---|---|---|---|
| E1 | Voice messages overwrite the encrypted `Message.content` with the plaintext transcript, and `voice_transcript` is plaintext too. History passes non-JSON content through as legacy plaintext. | `chatbot/tasks.py:268-270`, `chatbot/history.py:43-47` `[Verified]` | High: chat text readable in a dump |
| E2 | Pinning stores client-supplied message text in plaintext: in full in `RoomNote.source_message_content`, and the first 100 characters in `RoomNote.content`. The text comes from `request.data`. It is never checked against the stored message, and the message is not checked to belong to the room. | `chatbot/message_actions.py:93-123` `[Verified]` | High |
| E3 | Derived data is plaintext: `RoomContext.summary` and `memory_*`, `RoomNote.content`, `DailySummary`, `Reminder.content`, `DocumentUpload.processed_text`, `AIConversation.context`. | `chatbot/models.py` `[Verified]` | Medium: leaks the gist of rooms. Larger job (Phase 3). |
| E3b | `ActionReceipt.params/result` are plaintext, cut to 160 characters (`action_receipts.py:116-139`). They duplicate what `TurnToolCall.input_enc` encrypts. Receipts are append-only (non-negotiable 4), so any fix is forward-only. | `orchestration/models.py:196-202` | Medium; needs a maintainer decision |
| E3c | Media on disk is plaintext: voice notes, TTS audio and uploaded documents. | `chatbot/voice_views.py:40-48`, `chatbot/tasks.py:351` (per review; not opened by hand) | Medium; out of scope here, named so it isn't forgotten |
| E4 | The consumer's "key rotation" only reloads the same room key. | `chatbot/consumers.py:116-117, 124-125, 1578-1588` `[Verified]` | Low risk; misleading code |
| E5 | No way to rotate `ENCRYPTION_KEY`: one key, no `MultiFernet`, no rewrap command. | `users/encryption.py` `[Verified]` | Medium (see §4 on what rotation does and doesn't fix) |
| E6 | `Chatroom.save` swallows a wrap failure (`except Exception: pass`). That leaves a legacy room key in plaintext, as it already was. It writes no new plaintext. | `chatbot/models.py:55-59` `[Verified]` | Low |
| E7 | Integration credentials fall back on read to a Fernet key derived from `SECRET_KEY` (`or 'changeme'`). | `users/integrations_views.py:22-55` `[Verified]` | Low; remove after a rewrap |
| E8 | Chat messages use no AAD and no version. Any non-JSON `content` is read as a genuine plaintext message. Someone with DB write access can insert or swap rows within a room and the agent reads them as history. Rows in another room already fail, because each room has its own key. | `chatbot/kazi_messages.py:106`, `consumers.py:437`, `history.py:41-47` `[Verified]` | Low–Medium (integrity, not confidentiality) |
| E9 | The room-key unwrap is copied in 4 places. | `consumers.py:408`, `kazi_messages.py:82`, `tool_calls.py:126`, `turns.py:750` `[Verified]` | Maintenance |
| E10 | `docs/security.md:44` calls encryption "Optional". It is always on, and no setting turns it off. | `[Verified]` | Docs |
| E11 | `WorkflowTrigger.webhook_secret` is plaintext and read only by admin. | `workflows/models.py:205`, `workflows/admin.py:220` `[Verified]` | Low |
| E12 | Key wrap and unwrap are exercised indirectly by many tests. No direct test covers a tampered token, the wrong key, or the legacy `SECRET_KEY` fallback. | `[Verified]` | Tests |

Fernet is AES-128-CBC plus HMAC-SHA256: 16 bytes of the 32-byte key sign, 16 encrypt
`[Known]`. That is fine for wrapping keys. The docstring "32 bytes (256 bits) for AES-128"
should be corrected; the algorithm should not change.

## 3. Coordinating with v0.8

These v0.8 issues are open as of 2026-11-26 `[Verified]`:

- #216–#218 (T-A1–A3): turn and stream work in `consumers.py`, `coordinator.py`, `turns.py`.
- #220 and #221 (T-B1, T-B2).
- #223 (T-B4): reminder delivery, `chatbot/tasks.py`.
- #224–#226 (T-C1–C3): commitments and `RoomNote`/`RoomContext`.

When each step can land:

| Step | Files | Can land |
|---|---|---|
| Phase 0 | `users/encryption.py`, `users/integrations_views.py`, one fail-closed fix in `chatbot/models.py` (`Chatroom.save`), a new management command, tests, docs | Now: no open v0.8 issue touches these |
| E1 fix | `chatbot/tasks.py`, `kazi_messages.py` | After T-A2/T-A3 and T-B4 |
| E2 fix | `message_actions.py`, `RoomNote` | After T-C1/T-C3 |
| F-80 index | new table | Not in v0.8: it is Phase 4b of the redesign and needs a migration and a plan |

## 4. Phased plan (each step one small PR)

### Phase 0 — now, no conflict with v0.8

**1. Docs (E10).**

- `security.md`: say it is always on, and what it does and doesn't protect.
- Backup rule: store the key apart from the data, and restore needs both. Name the SQLite
  case: `Backend/db.sqlite3` and `Backend/.encryption.key` sit side by side.

**2. Tests (E12).** Tampered token, wrong key, legacy fallback. Each is shown failing first
(non-negotiable 9).

**3. Master-key rotation (E5).**

- Read an optional `ENCRYPTION_KEY_PREVIOUS` (comma-separated). Build
  `MultiFernet([current, *previous, legacy_secret_key_fernet])`: it encrypts with the first
  key and decrypts with any `[Known]`.
- Add `manage.py rotate_encryption_key` for `Chatroom.encryption_key`,
  `UserIntegration.encrypted_credentials` and `CalendlyProfile.*_token`.
- Cost: one small value per room, not per message.

The command has to handle these hazards:

- (a) **Legacy rows.** Rows under the `SECRET_KEY`-derived key need that key in the
  `MultiFernet`, or `rotate()` raises `InvalidToken`.
- (b) **Never overwrite on failure.** Use `rotate()` directly, never `decrypt_data` (returns
  `{}`) or `safe_decrypt` (returns `None`). An undecryptable row is skipped and reported, so
  it can't be overwritten with an encrypted `{}`.
- (c) **Restart first.** `TokenEncryption` caches its key per process. Web, worker and beat
  must all restart on the new key list before rotating, or old processes keep writing under
  the old key.
- (d) **Verify before dropping old keys.** A `--verify` pass counts rows the primary key alone
  can't decrypt. Remove `ENCRYPTION_KEY_PREVIOUS` only at zero. Dropping it early loses every
  room.
- (e) **Dev key file.** Document how the command treats `Backend/.encryption.key` (the dev
  key) as the current key.

Once `--verify` reports zero legacy rows, the `SECRET_KEY` fallback (E7) can be deleted.

**What rotation does and doesn't fix.**

- Rewrapping changes only the master key; room keys stay the same.
- It protects backups taken *after* rotation from the old key.
- If a leaked key **and** any dump or backup are both exposed, every room key is exposed,
  including for messages written later.
- The incident response for that case is:
  1. Rotate the room keys (Phase 3, step 9).
  2. Revoke and reissue OAuth and integration tokens at each provider.

**4. Fail closed in `Chatroom.save` (E6).**

- Raise on a wrap failure.
- `upgrade_legacy_room_key` (`consumers.py:1554-1562`, on the message path) must catch the
  error, log it, and keep serving the room with the key as it is. A wrap failure must not
  take down chat.

### Phase 1 — gated as in §3

**5. Close E1.**

- Put the transcript in the encrypted payload (`{"content": transcript, "voice": true}`) via
  `encrypt_content_for_room`.
- Stop writing plaintext `voice_transcript`.
- Fix existing rows with a management command, in batches. A data migration also works but
  is a protected path, so it needs a plan.

**6. Close E2.**

- Pinning loads the message filtered by both id and the authorised room.
- It reads the content by decrypting the stored message server-side and ignores the text
  the client sent.
- It stores only the message id, with no plaintext copy in `content` or
  `source_message_content`. `content` holds a neutral label; any preview is decrypted from
  the message when shown.
- Fix existing pinned notes with a batched management command: blank
  `source_message_content` and rewrite `content` to the neutral label, keyed on
  `source_message_id`. E2 is closed only once that has run.
- Test: no pinned text appears in any stored `RoomNote` column, for new and cleaned rows.

**7. One room-cipher helper (E9).**

- Add `room_aead(encryption_key) -> AESGCM | None` in `users/encryption.py`. It is
  boundary-safe: orchestration already imports `users.encryption` (`tool_calls.py:21`).
- Replace the 4 copies.
- Delete the fake rotation (E4).

### Phase 2 — the blind index (F-80), when search is scheduled

See §5. It changes nothing in message encryption: one table, one derived key.

**Hard precondition:** a source is indexed only once it is encrypted at rest.

- Messages qualify once E1 is closed.
- Documents qualify after E3 (`DocumentUpload.processed_text`).
- Receipts are not indexed while E3b stands.
- Image descriptions qualify once they are stored encrypted.

The reason: if any indexed text also sits in plaintext, someone with a dump can tokenise it
with the public `normalize`. Matching those tokens to index rows yields token-to-word pairs
for the whole room, which then reveal the words of encrypted messages too.

### Phase 3 — later, when there are more people

**8. Encrypt derived data (E3).**

- Move `RoomNote`, `RoomContext`, summaries, reminders and document text to the room key.
- `search_notes` switches from `icontains` to the §5 index.
- Do this after Phase 2 and after T-C1/T-C3.
- Receipts (E3b) need their own forward-only decision, because rows can't be rewritten.

**9. Room-key rotation.**

- Add a key version on `Chatroom`, keeping every key version wrapped until no data uses it.
- Re-encrypt, in the background, everything under the room key:
  - `Message.content`;
  - `TurnToolCall.input_enc` and `shown_enc`;
  - `Turn.paused_state_enc`;
  - the Phase 3 fields once they exist.
- Rebuild the search index under the new version.
- Each envelope records the key version it was written with. Reads pick the key by that
  version.
- The old key version is deleted only after a verify pass finds no data still using it.
- Day to day, a home lab doesn't need it.
- It is the **incident** response when a key and a backup have both leaked (see step 3).
  Build it before anyone needs it in a hurry.

**10. Message envelope v1 (E8).**

- `{"v":1}` for new writes, with AAD `f"{room_id}:{message_uid}"`.
  - `message_uid` is a new `Message.uid` UUID column, generated by the application before the
    insert (`default=uuid4`), so it exists at encryption time.
  - The UUID is stored in its own column, not inside the envelope. Swapping `content` between
    two rows then fails decryption, as `TurnToolCall` does with `turn_id:seq`.
  - Decryption rebuilds the AAD from the row's columns.
  - Adding the column is a migration, so it needs a plan.
- Add an install-level cutoff: after a recorded date, or once a rewrite command has run,
  legacy and plaintext rows are refused rather than shown.
- Without the cutoff, v1 adds nothing, because an attacker would write a legacy row instead.
- Also check `v` on decrypt for tool calls and paused state.

### Not recommended `[Analysis]`

- **End-to-end or client-held keys.** The agent must read messages to work.
- **Order-preserving or order-revealing encryption.** It leaks far more than keyed hashes and
  isn't needed for word search.
- **Forward-private searchable-encryption schemes, ORAM, FHE.** Research-grade cost for a
  threat a home lab doesn't face.
- **`pgcrypto` or Postgres TDE.** These break SQLite and lite mode, and put keys within the
  database's reach.
- **Vault or a KMS as a requirement.** At most an optional key source later.

## 5. Blind index: the crypto spec F-80 left open

**Key separation.**

- `search_key = HKDF-SHA256(room_key, salt=None, info=b"kazi/search/v1")` `[Known]`.
- Never HMAC with the room key itself.
- `info` is versioned, and so is everything that changes tokens.

**Token.**

- Take the first 8 bytes of `HMAC-SHA256(search_key, word)` and read them as a big-endian
  signed 64-bit integer.
- Words:
  1. `unicodedata.normalize("NFKC", unicodedata.normalize("NFKC", text).casefold())`.
  2. Take every `re.findall(r"\w{2,}", ...)` match.
  3. Drop the stop-words.
- The stop-word list is fixed and versioned with `info`. Changing it means `v2`.
- No stemming in v1: English and Swahili need different stemmers.

**Why 64-bit integers:**

- **Portable.** A plain `INTEGER`/`BIGINT` B-tree on SQLite and Postgres.
- **About 2.1× smaller** than 32-byte blobs (§5.1).
- **Harmless collisions.**
  - A false hit on a query is about N/2⁶⁴ for N distinct words per room. The chance of any
    collision among 10⁵ words is about 3×10⁻¹⁰ `[Analysis]`.
  - A false hit costs one extra decrypt and is dropped afterwards.

**Table.**

- Columns: `room_id`, `tok`, `source`, `key_version` (smallint), and two nullable foreign keys,
  `message_id` and `document_id`.
  - A check constraint requires exactly one of the two foreign keys.
  - `DocumentUpload` has no link to a message, so documents are keyed by their own id
    rather than by a message created for each upload.
- Both foreign keys `CASCADE`. Deleting a message, a document or the room removes its rows.
  Room membership is checked at query time, so a user who left the room gets no hits.
- Lookup index `(room_id, tok)`. Ordering comes from the message or document id.
- No timestamps.
- §5.1's `WITHOUT ROWID` composite key only fits a single source. With two sources, the size
  falls between §5.1's rows; measure it in the Phase 2 plan.

**Prefixes (amends F-80).** Off in v1.

- F-80 stores hashed 3–6 letter prefixes. Each prefix adds rows and leaks which messages share
  a prefix.
- Add them behind a setting once size is measured on Pi-class hardware.

**Rotation.**

- Rows carry `key_version`. A room re-key writes the new version in batches, queries cover
  both versions meanwhile, then the old rows are deleted.
- Master-key rotation doesn't touch the index: the search key comes from the room key.

**Room membership.**

- Check membership first, then hash the query once per allowed room.
- A user who left a room gets nothing from it.
- An error in the check denies the search (fail closed).

**Leakage (for `security.md`).** Someone holding a dump, without the key, can see:

- which messages in a room share a word;
- word frequencies per room;
- index rows per message, which roughly gives message length;
- the `source` column (message, voice, document);
- across successive backups, which tokens are new.

They cannot read the words, **provided the precondition in Phase 2 holds**. Tokens are keyed
per room, so they don't link across rooms. With some known plaintext, frequency analysis can
guess common words; published attacks on deterministic searchable encryption show this
`[Known]` (Naveed, Kamara and Wright, CCS 2015; Cash et al., CCS 2015). For a home lab whose
threat is a leaked backup, that is acceptable, and far less than E1–E3 leak today.

### 5.1 Measurement

Setup: 50,000 synthetic messages × 60 Zipf-distributed words, a 20k vocabulary, top 50 words
dropped, giving 1.70 M index rows. Run on SQLite in a 4-vCPU container. The script is not
committed.

| Table shape (SQLite) | int64 tokens | 32-byte blobs |
|---|---|---|
| `WITHOUT ROWID`, composite PK `[Verified]` | 33.5 MB | 78.7 MB |
| Same, re-measured after VACUUM (reviewer) | 33.0 MB | 74.1 MB |
| Ordinary rowid table, composite PK (reviewer) | 74.6 MB | 156.9 MB |
| Django default: auto id + unique constraint (reviewer) | 78.4 MB | — |

- Tokenising and HMAC cost 0.059 ms per message on save. A one-word lookup took under
  0.01 ms `[Verified]`.
- Lookups aren't the cost; storage is.
- Django's schema editor creates the ordinary rowid shape, as far as is known, so a
  Django-managed table lands at about 75–78 MB here.
- Getting the `WITHOUT ROWID` size on SQLite needs a `RunSQL` migration. The Phase 2 plan
  decides whether that is worth it.
- Not measured: Postgres, and Raspberry Pi-class hardware.
- F-80's own benchmark (0.15 ms/message, 0.3 ms per query including 20 decrypts) is consistent
  with these numbers.

## 6. Guardrails this respects

- **Migrations.** Phase 2's table and Phase 3's fields need a `docs/plans/` plan and a human
  OK, because `Backend/**/migrations/*` is protected. Data fixes prefer management commands.
- **No new dependencies.** HKDF, HMAC and `MultiFernet` come from `cryptography` and the
  standard library. `pycryptodome` is pinned but never imported, so it is a removal
  candidate.
- **Fail closed.**
  - On write: missing keys or unwrap errors store nothing, never plaintext.
  - On read, until step 10 lands: legacy plaintext rows are still accepted. Say so in
    `security.md`.
  - Membership-check errors deny.
- **Tests.**
  - Every new test is shown failing first.
  - "A dump has no readable words" is asserted on raw stored rows of *every* table that holds
    room text, not only the index and not through the decrypting helpers.
