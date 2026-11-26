# Encryption at rest: review, small fixes, and the crypto for blind search

**Date:** 2026-11-26
**Status:** proposal, not filed. No code changed.
**Targets:** home labs first, small VPS hosts second.
**Basis labels:** `[Verified]` = read in this repo at commit `961596e` or measured;
`[Known]` = from general crypto knowledge, not re-checked here; `[Analysis]` = design judgment.

This proposal extends **F-80** (keyed word index) in
[`2026-11-frontend-redesign.md`](2026-11-frontend-redesign.md). It answers F-80's open
questions on keys and rotation. It does not repeat F-80's search UX or build steps.

---

## 1. Verdict

Keep the current design. Do not replace it.

Kazi already uses envelope encryption `[Verified]`:

- A master key `ENCRYPTION_KEY` (Fernet) wraps one random AES-256-GCM key per room
  (`Chatroom.encryption_key = "enc:" + Fernet(room_key)`, `Backend/chatbot/models.py:49-59`).
- Message bodies, tool-call bodies and paused loop state are encrypted with the room key,
  using random 12-byte nonces. Tool calls and paused state also bind associated data (AAD),
  in a versioned `{"v":1,...}` envelope (`orchestration/tool_calls.py:135-170`,
  `orchestration/turns.py:775-818`).

For a home lab or a VPS, this is the right shape. It protects the likely loss: a database
dump, a backup or a disk that leaks without the `.env`. It is cheap, it needs no KMS, and
it works on SQLite. It also makes master-key rotation cheap: you rewrap one key per room
and re-encrypt no messages `[Analysis]`.

The gaps are around the scheme, not in it:

- Plaintext copies of message content sit next to the ciphertext.
- Key rotation can't be done, and the code that claims to do it is a no-op.
- There are a few fail-open paths and doc mismatches.

None of these needs a redesign.

**Threat model, stated plainly `[Analysis]`:**

- This is encryption at rest, not end-to-end. The server holds every key.
- It does **not** protect against someone who controls the running host, or who has both the
  DB and `.env`.
- On a home lab, the bigger practical risks are:
  - losing the key, which loses every room;
  - one backup that holds both the Postgres volume and `.env`, which defeats the encryption.

## 2. Findings

| # | Finding | Where | Severity |
|---|---|---|---|
| E1 | Voice messages overwrite the encrypted `Message.content` with the plaintext transcript. `voice_transcript` is plaintext too. History then passes non-JSON content through as "legacy plaintext". | `chatbot/tasks.py:268-270`, `chatbot/history.py:43-47` `[Verified]` | High: chat text readable in a dump |
| E2 | Pinning a message stores its decrypted text in `RoomNote.source_message_content`. | `chatbot/message_actions.py:116-123` `[Verified]` | High |
| E3 | Derived memory is plaintext: `RoomContext.summary` and `memory_*`, `RoomNote.content`, `DailySummary`, `Reminder.content`, `DocumentUpload.processed_text`, `AIConversation.context`. | `chatbot/models.py` `[Verified]` | Medium: leaks the gist of rooms. Fixing it is a larger job (§4, Phase 3). |
| E4 | "Key rotation" in the consumer only reloads the same room key. | `chatbot/consumers.py:117-118, 1578-1588` `[Verified]` | Low risk, but misleading code |
| E5 | No way to rotate `ENCRYPTION_KEY`: one key, no `MultiFernet`, no rewrap command. | `users/encryption.py` `[Verified]` | Medium for VPS (a leaked `.env` can't be recovered from) |
| E6 | `Chatroom.save` swallows a wrap failure (`except Exception: pass`) and saves the legacy room key in plaintext. This breaks the fail-closed rule. | `chatbot/models.py:55-58` `[Verified]` | Low (legacy rows only) |
| E7 | Integration credentials still fall back on read to a Fernet key derived from `SECRET_KEY` (`or 'changeme'`). | `users/integrations_views.py:22-55` `[Verified]` | Low; remove after a one-shot rewrap |
| E8 | Chat messages use no AAD and no version field. Ciphertext can be swapped between rows *of the same room* without failing decryption. Moving it to another room already fails, because each room has its own key. | `chatbot/kazi_messages.py:106`, `consumers.py:437` `[Verified]` | Low |
| E9 | The room-key unwrap is copied in 4–5 places. | `kazi_messages.py:78`, `tool_calls.py:123`, `turns.py:744-761`, `consumers.py:402` `[Verified]` | Maintenance: the copies will drift when rotation lands |
| E10 | `docs/security.md:44` says encryption is "Optional". The code always encrypts, and no setting turns it off. | `[Verified]` | Docs |
| E11 | `WorkflowTrigger.webhook_secret` is stored in plaintext, and nothing reads it. | `workflows/models.py:205`, read only by admin `[Verified]` | Low |
| E12 | No unit test does a `TokenEncryption` round trip or covers the legacy fallback. | `[Verified]` via grep | Tests |

Fernet is AES-128-CBC plus HMAC-SHA256 `[Known]`. That is fine for wrapping keys. The
docstring that calls it "32 bytes (256 bits) for AES-128" is confusing: the 32 bytes are
16 for signing and 16 for encryption `[Known]`. Don't change the algorithm.

## 3. Coordinating with v0.8

These v0.8 issues are open as of 2026-11-26 `[Verified]`:

- #216–#218 (T-A1–A3): turn and stream work in `consumers.py`, `coordinator.py`, `turns.py`.
- #220 and #221 (T-B1, T-B2).
- #223 (T-B4): reminder delivery, `chatbot/tasks.py`.
- #224–#226 (T-C1–C3): commitments and `RoomNote`/`RoomContext` staleness.

Rules that follow from this:

- **Now (no conflict):** only `users/encryption.py`, a new management command, tests and docs.
- **After T-A2/T-A3 merge:** anything that touches message envelopes in `consumers.py` or
  `kazi_messages.py`.
- **After T-C1/T-C3 merge:** anything that changes `RoomNote` or `RoomContext` storage.
- **Not in v0.8:** the F-80 index. It is Phase 4b of the frontend redesign and needs a
  migration and a plan.

## 4. Phased plan (each step one small PR)

### Phase 0 — now, no conflict with v0.8

1. **Docs (E10).**
   - `security.md`: "always on". Say what it does and doesn't protect.
   - Add a backup rule: back up `ENCRYPTION_KEY` separately from the DB volume. Restore
     needs both.
2. **Tests (E12).**
   - `TokenEncryption` round trip, a tampered token, and the wrong key.
   - Each test is shown failing first, per non-negotiable 9.
3. **Master-key rotation (E5).**
   - Read an optional `ENCRYPTION_KEY_PREVIOUS` (a comma-separated list). Build
     `MultiFernet([current, *previous])`: it encrypts with the first key and decrypts with
     any `[Known]`.
   - Add `manage.py rotate_encryption_key`. It calls `MultiFernet.rotate()` on every
     `Chatroom.encryption_key`, `UserIntegration.encrypted_credentials` and
     `CalendlyProfile.*_token`, in batches, and is idempotent.
   - Cost: one small value per room, not per message.
   - The same command also rewraps the legacy `SECRET_KEY`-encrypted rows (E7). The
     fallback can be deleted one release later.
4. **Fail closed in `Chatroom.save` (E6).** A wrap failure raises an error instead of
   saving the key in plaintext.

### Phase 1 — after T-A2/T-A3 land

5. **Close the plaintext leaks (E1, E2).**
   - Write the transcript into the encrypted payload (`{"content": transcript,
     "voice": true}`) with `encrypt_content_for_room`.
   - Stop writing `voice_transcript` and `source_message_content` in plaintext. Read them
     from the decrypted message instead.
   - The data fix for existing rows needs a migration, so a plan and a human OK first.
   - Test: a stored row contains no transcript words.
6. **One room-cipher helper (E9).**
   - Add `room_aead(encryption_key) -> AESGCM | None` in `users/encryption.py`. It is
     boundary-safe, because orchestration already imports `users.encryption`
     (`tool_calls.py:21`).
   - Replace the copies.
   - Delete the fake rotation (E4).

### Phase 2 — the blind index (F-80), when search is scheduled

See §5. It needs no change to message encryption. It adds one table and one derived key.

### Phase 3 — later, when there are more people

7. **Encrypt derived memory (E3).** `RoomNote`, `RoomContext`, summaries, reminders and
   document text move to the room key. `search_notes` then switches from `icontains` to the
   §5 index, so do this after Phase 2 and after T-C1/T-C3.
8. **Message envelope v1 (E8).** `{"v":1}` with AAD `room_id`, for new writes only. Reads
   accept both formats. Do it when the envelope is touched anyway.
9. **Room-key rotation.** Adds a key version on `Chatroom`, background re-encryption and
   re-indexing. Only worth it if a single room key is ever suspected leaked. Home labs
   don't need it.

### Not recommended `[Analysis]`

- **End-to-end or client-held keys.** The agent must read messages to work, so this breaks
  the product.
- **Order-preserving or order-revealing encryption.** It leaks far more than keyed hashes
  and isn't needed for word search.
- **Forward-private SSE schemes (Sophos, Diana and similar), ORAM, FHE.** These are
  research-grade, with large cost and complexity, for a threat a home lab does not face.
- **`pgcrypto` or Postgres TDE.** These break SQLite and lite mode, and put keys in the
  database's reach.
- **Vault or a KMS as a requirement.** At most an optional key source later. A home lab
  should not need one.

## 5. Blind index: the crypto spec F-80 left open

**Key separation.**

- `search_key = HKDF-SHA256(room_key, salt=None, info=b"kazi/search/v1")` `[Known]`.
- Never use the room key itself as the HMAC key.
- The `info` string is versioned, so a future v2 tokeniser gets a fresh key space.

**Token.**

- `tok = int64(HMAC-SHA256(search_key, normalize(word))[:8])`, stored as a signed 64-bit
  integer.
- `normalize` = NFKC, then `casefold`, then split on `\w{2,}`, then drop the stop-words.
- No stemming in v1: English and Swahili need different stemmers, and exact match is
  predictable.

**Why 64 bits and an integer, not a 32-byte blob:**

- **Size.** It is 2.3× smaller (§5.1).
- **Portability.** It is a plain `INTEGER` / `BIGINT` B-tree on both SQLite and Postgres.
- **Collisions are harmless.** For a room with 10⁵ distinct words, a collision is about
  10⁵ / 2⁶⁴ per query `[Analysis]`. A false hit costs one extra decrypt and is dropped
  after decryption. The search shows only the decrypted page anyway.

**Table.** Primary key `(room_id, tok, message_id)` plus `source` (message, voice,
document, receipt) and `key_version` (smallint).

- No timestamps: newest-first ordering comes from `message_id`.
- Rows are deleted with the message or room (cascade), so deleted chats leave no index.

**Prefixes (search-as-you-type).** Off in v1.

- Each prefix adds rows and leaks more: a dump shows which messages share a 3-letter
  prefix.
- Add them behind a per-install setting once the size is measured on Pi-class hardware.

**Rotation.**

- Rows carry `key_version`.
- A room re-key (Phase 3) writes new rows in a background batch job, then deletes the old
  version. Queries cover both versions while the job runs.
- Master-key rotation (Phase 0) does not touch the index, because the search key comes from
  the room key, not from `ENCRYPTION_KEY`.

**Room membership.** Searching across rooms checks membership first, then hashes the query
once per allowed room. A user who left a room has no access to it, which is F-80's
acceptance test. If the membership check errors, the search is denied (fail closed).

**Leakage (write this into `security.md`).** Someone holding a dump, without the key, can
see:

- how many distinct words each message has;
- which messages in a room share a word;
- word frequencies per room.

They cannot read the words. Because the token is keyed per room, the same word gives
unrelated tokens in different rooms, so no cross-room links. With partial known plaintext,
frequency analysis can guess common words; published attacks on deterministic searchable
encryption show this `[Known]` (Naveed–Kamara–Wright 2015; Cash et al. 2015). For a home
lab whose threat is a leaked backup, that is acceptable. It is still much less than today,
where E1–E3 leak full text.

### 5.1 Measurement `[Verified]`

Run in this session: 4 vCPU container, Python 3, `cryptography` HKDF + `hmac`, SQLite, 50,000
synthetic messages of 60 Zipf-distributed words over a 20k vocabulary, top 50 words treated
as stop-words. The script is not committed.

| What | Result |
|---|---|
| Tokenise + HMAC per message on save | 0.059 ms |
| Index rows | 1.70 M |
| SQLite size, int64 tokens | 33.5 MB |
| SQLite size, 32-byte blob tokens | 78.7 MB |
| One-word lookup, newest 20 hits | < 0.01 ms (both) |

These numbers are compatible with F-80's own benchmark (0.15 ms/message, 0.3 ms per query
including decrypting 20 hits). Lookups are not the cost; storage is. That is why the
integer token and prefixes-off are the defaults. I have no Raspberry Pi numbers.

## 6. Guardrails this respects

- **Migrations.** Phase 1 data fix, the Phase 2 table and Phase 3 fields all need a
  `docs/plans/` plan and a human OK. `Backend/**/migrations/*` is protected.
- **No new dependencies.** HKDF, HMAC and `MultiFernet` are in `cryptography` and the
  standard library, both already pinned. `pycryptodome` is pinned but never imported, so a
  removal candidate.
- **Fail closed.** Missing keys, unwrap errors and membership-check errors deny or store
  nothing, never plaintext.
- **Tests.**
  - Every new test is shown failing first.
  - "A dump has no readable words" is asserted on the raw stored rows, not through the
    decrypting helpers.
