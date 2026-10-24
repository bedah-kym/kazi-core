# Configuration

Every environment variable Kazi reads, grouped by what it controls. Defaults
are the values used when the variable is unset. Put these in `.env` in the repo
root (one level above `Backend/`).

## Bootstrap (required)

| Variable | Default | Purpose |
|---|---|---|
| `DJANGO_SECRET_KEY` | — | **Required in production.** In dev, an unset value is generated and persisted to a git-ignored file. |
| `DJANGO_DEV_SECRET_KEY_FILE` | `.dev_secret_key` (repo root) | Where the generated dev secret is persisted. |
| `DJANGO_DEBUG` | `False` | Enables debug mode (dev only). |
| `DJANGO_ALLOWED_HOSTS` | `localhost,127.0.0.1` | Comma-separated allowed hosts. |
| `DJANGO_CSRF_TRUSTED_ORIGINS` | empty | Comma-separated trusted origins. Required in production. |
| `DATABASE_URL` | SQLite fallback | Database URL. Use Postgres in production. |
| `DB_POOL_MAX_SIZE` | unset (no pool) | Postgres only. A whole number caps each process at that many connections in a reused pool (`CONN_MAX_AGE` becomes 0, health checks off, 10 s wait for a free connection). Unset or `0` keeps persistent per-thread connections (600 s). Set it when the server has a small client limit, such as a hosted session pooler. The limit is per process: set it above that process's concurrent requests (web needs the most), and keep the sum across processes under the server's limit. A request that finds the pool full waits, then fails. Any other value refuses to boot. |
| `REDIS_URL` | `redis://redis:6379/0` | Redis for Channels + cache + Celery. Required in production. |
| `REDIS_CACHE_IGNORE_EXCEPTIONS` | `True` | Fail-soft on Redis cache read errors (real-time features degrade, HTTP still serves). |
| `CELERY_BROKER_URL` | `REDIS_URL` | Celery broker URL. |
| `ENCRYPTION_KEY` | — | **Required in production.** Fernet key (URL-safe base64, 32 bytes) for per-room message encryption. In dev it's auto-generated and persisted to `Backend/.encryption.key`. A changed/lost key makes existing rooms undecryptable. |

## Dev without Redis/Postgres (in-memory fallback)

With `DJANGO_DEBUG` on, if `DATABASE_URL` points at the Compose network (host
`db`/`postgres`) and Redis is unreachable at boot, settings fall back to an
in-memory channel layer + LocMem cache and SQLite, and Celery runs tasks inline
(`CELERY_TASK_ALWAYS_EAGER`). This lets `manage.py runserver` serve the chat UI
natively from a venv with no Docker and no services. Auto-fallback replaces only
a Compose `DATABASE_URL` with SQLite; with no `DATABASE_URL`, debug mode does
**not** fall back. Production (`DEBUG=False`) never falls back. Force it with
`KAZI_DEV_INMEMORY=1` (debug only — preserves other database URLs).

Inline means in the process that asked: the room summary, document extraction
and voice tasks run there, with real model calls if a key is set. This mode has
no worker and no scheduler, so a task that is due later is not run at all: idle
nudges are never sent and a reminder set for a future time is stored but not
delivered. A retry is the exception: a task that asks to be retried later runs
again at once, up to its retry limit. Test runs are different again: they never
run a task body inline and never reach a broker.

| Variable | Default | Purpose |
|---|---|---|
| `KAZI_DEV_INMEMORY` | auto (DEBUG + Compose `DATABASE_URL` + Redis unreachable) | Force the in-memory/SQLite dev fallback (ignored unless `DEBUG`). |

## Lite profile (chat-only, no Redis or Celery)

A small install can run the same web app with LocMem cache, in-memory Channels,
no Temporal and no Celery worker or beat. It is selected independently of
`DEBUG`, so it is safe on a server:

```bash
KAZI_LITE_PROFILE=1
```

The web process must be served by **uvicorn with exactly one worker**. In-memory
Channels do not fan a room out across processes: a second worker would split a
room's sockets. The ASGI app refuses to load when it can see one of
`KAZI_WEB_WORKERS`, `WEB_CONCURRENCY`, `GUNICORN_WORKERS` or `UVICORN_WORKERS`
set above 1. Uvicorn's `--workers` flag does not export a count, so the run page
states plainly that one worker is required. The sidecar can run separately with
`python -m orchestration.shell_exec`, which never imports Django.

The lite profile turns on the in-process scheduler (`IN_PROCESS_SCHEDULER`).
It runs the `CELERY_BEAT_SCHEDULE` entries inside the web process, claiming each
due run in the database so several workers cannot run it twice. Last-run times
are durable. It uses the same periodic task rows as beat, so do **not** start
Celery beat in this profile; beat refuses to start when the setting is on.
`REMINDER_SWEEP_SECONDS` defaults to 30 in this profile (3600 otherwise) so a
reminder set two minutes ahead is delivered from chat without an ETA broker.

See [Run the lite profile](run-lite.md).

| Variable | Default | Purpose |
|---|---|---|
| `KAZI_LITE_PROFILE` | `False` | Chat-only profile: LocMem cache, in-memory Channels, no Temporal, no Celery, in-process scheduler, one web worker. |
| `KAZI_IN_PROCESS_SCHEDULER` | `True` in lite, `False` otherwise | Run the periodic schedule inside the web process. Never combine with Celery beat. |
| `KAZI_WEB_WORKERS` | `1` | Worker count the lite guard reads when the platform does not set `WEB_CONCURRENCY`/`GUNICORN_WORKERS`/`UVICORN_WORKERS`. More than 1 is refused when the ASGI app loads; set it when your launcher only has a `--workers` flag. |
| `KAZI_INSECURE_LOCALHOST` | `False` | One-machine trial only: allow plain HTTP on loopback by turning off `SECURE_SSL_REDIRECT`, secure cookies and HSTS. Refused when `DJANGO_ALLOWED_HOSTS` names a non-loopback host. Never set it on a server; the default keeps the production TLS behaviour. |
| `REMINDER_SWEEP_SECONDS` | `30` in lite, `3600` otherwise | How often the reminder sweep runs. In lite this is the delivery path for due reminders. |

With `DJANGO_DEBUG=False` the web process must sit behind a TLS-terminating
reverse proxy that sets `X-Forwarded-Proto: https`; a plain-HTTP request
otherwise answers `301`. The run page has a minimal Caddy and nginx example.

Moderation needs the Redis profile: message buffering and the moderation batch
store live in Redis. With `KAZI_LITE_PROFILE=1`:

- an explicit `MODERATION_ENABLED=1` refuses to start, with a message that says
  moderation needs the Redis profile;
- moderation implied by `HF_API_TOKEN` is turned off, and start-up says so
  once;
- `MODERATION_ENABLED=0` is accepted and stays off.

The full profile keeps the existing behaviour: `MODERATION_ENABLED` defaults to
on when `HF_API_TOKEN` is set.

## LLM providers & routing

| Variable | Default | Purpose |
|---|---|---|
| `ANTHROPIC_API_KEY` | — | Claude API key. |
| `HF_API_TOKEN` | — | Hugging Face token. |
| `DEEPSEEK_API_KEY` | — | DeepSeek API key (OpenAI-compatible, full tool-calling). |
| `DEEPSEEK_MODEL` | `deepseek-chat` | Model name for DeepSeek. |
| `DEEPSEEK_BASE_URL` | `https://api.deepseek.com/v1/chat/completions` | DeepSeek endpoint. |
| `LLM_DEFAULT_PROVIDER` | empty | Provider that answers a chat turn when the room has no model override. |
| `LLM_EXECUTOR_PROVIDER` | `huggingface` | Provider for tool execution. |
| `LLM_EXECUTOR_MODEL` | empty | Override the model used for tool execution. |
| `LLM_REVIEWER_PROVIDER` | `anthropic` | Provider for the workflow reviewer. |
| `LLM_REVIEWER_MODEL` | empty | Reviewer model. The scheduled reviewer runs only when this names a model different from the authoring models; empty keeps it off. |
| `LLM_MAX_TOKENS` | `700` | Hard per-call token ceiling. |
| `LLM_PROMPT_CHAR_LIMIT` | `4000` | User prompt truncation cap. |
| `LLM_CACHE_ENABLED` | `True` | Response caching toggle. |
| `LLM_CACHE_TTL_SECONDS` | `600` | Cache TTL. |
| `LLM_CACHE_MIN_TEMP` | `0.3` | Don't cache calls below this temperature. |

A chat turn is answered by the **operator's default provider**: set
`LLM_DEFAULT_PROVIDER=deepseek` (or `anthropic` / `huggingface`) and adding a
second provider's key later does not change which provider answers — the extra
key only adds fallbacks and the models the per-room picker can offer. With
`LLM_DEFAULT_PROVIDER` unset, the first configured provider in the order
anthropic, deepseek, huggingface answers, and the boot log states which one. The
harness never picks a model from the message shape; the provider resolves its own
default model unless the room chose one.

The **model catalog** (`orchestration/model_catalog.py`) is a fixed list of
models the chatroom picker offers; which ones are available is gated purely by
which provider keys are set. Users override the model **per room** from the
chatroom header (or `GET/POST /api/rooms/<id>/model/`) — the override is stored
as a Redis preference, not an env var. Leave it on *Auto* to use the default
provider above.

## Quotas

| Variable | Default | Purpose |
|---|---|---|
| `LLM_TOKEN_QUOTA_ENABLED` | `True` | Per-user token quota enforcement. |
| `LLM_TOKEN_LIMIT_PER_USER_PER_HOUR` | `200000` | Rolling-hour **work-token** budget per user. |

The hourly quota counts **work tokens** — what the model generates plus the
input it newly reads — in the same unit as `AGENT_TURN_TOKEN_BUDGET` (T-F3b). The
system prompt, tool list and history resent on every call are *not* charged, so
a long conversation does not spend the hour's limit just by being resent. A
single-shot call has nothing resent, so it counts what it actually sent and
received. Staff and superusers are exempt.

Usage is kept in short-lived buckets and read back over a true **rolling hour**:
usage older than an hour stops counting even while the user keeps calling.
Recordings are atomic, so concurrent calls (two rooms, parallel delegations,
several worker processes for one user) are never dropped, and every bucket keeps
a TTL so it always ages out. The default limit is at least twice the default turn
budget, so a user can finish one full default turn and start another within the
hour.

When the hour is full, a call is refused with a message that says the hourly
limit was reached and roughly when capacity frees up. A turn blocked mid-way ends
like a budget stop: its state is kept, so the user can reply **continue** once it
frees up and pick up where it stopped without redoing finished work. Resuming a
quota stop is **not** a budget grant: it never raises the turn's budget or its
extension count (a turn stopped by its own budget still extends as before).
Replying **continue** while the hour is still full says so and keeps the paused
turn, instead of claiming the limit freed.

The check **fails closed**: if usage cannot be read, a call that needs the quota
is refused rather than allowed. This matters for self-hosters because the
production cache (`REDIS_CACHE_IGNORE_EXCEPTIONS`, default on) deliberately
swallows cache errors so HTTP views keep serving during a Redis outage. The quota
read bypasses that and surfaces the outage, so during a Redis outage a non-staff
user's quota-bound calls are refused with a "could not be checked" message;
every other cache user keeps today's ignore-and-serve behaviour. If Redis is down
for an extended time and you would rather fail open for the quota too, set
`LLM_TOKEN_QUOTA_ENABLED=False` — but that lifts the limit for everyone.

## Memory & context budgets

| Variable | Default | Purpose |
|---|---|---|
| `CONTEXT_PROMPT_MAX_CHARS` | `8000` | Cap on room context injected into the system prompt. |
| `HISTORY_MAX_CHARS` | `60000` | History budget before compaction. |
| `HISTORY_MAX_MESSAGES` | `50` | Max history turns kept per agent loop. |
| `HISTORY_COMPACTION_ENABLED` | `True` | Trim the oldest turns when the budget is exceeded (opt-out). |

## Turn budget (T-F3)

One **work-token budget** ends a normal agent turn. "Work tokens" count what the
model generates plus the input it newly reads (new messages, tool results); the
system prompt, tool list and history resent on every call are *not* counted, so
a long conversation does not spend the budget just by being resent.

| Variable | Default | Purpose |
|---|---|---|
| `AGENT_TURN_TOKEN_BUDGET` | `100000` | Work-token bound for one agent turn. |
| `AGENT_TURN_TOKEN_BUDGET_MAX` | `500000` | Largest bound an operator may choose; also the bound a legacy caps-off install maps to. |

The model sees the budget used and left from its very first call (the note rides
on the new user message; the cached system + tools prefix is untouched) and on
every call after, and can end a turn by calling `request_more_budget` (which
reads to the user as its own message). The user grants more with a whole-message
reply — **more budget**, or a plain **yes**/**ok**, or **continue**,
**go on**, **carry on** — and the work resumes where it stopped, without
repeating a finished step. A whole-message **no** or **stop** ends the paused
turn. Any other message drops the paused work (the reply says so once) and
starts a new turn as usual.

Reaching the bound without the model asking ends the turn the same way: the
state is kept, the harness note says plainly what ran (how many tool calls and
which tools) and offers the same reply, and a grant resumes from where it
stopped. The note is the harness's words, separate from the model's, and claims
no success.

A delegated run gets a share of what the parent turn has left, and what the
sub-run spends is charged back to the parent, so a second delegation sees less
and the delegations cannot together exceed the bound. A parent with too little
left refuses to delegate. A grant never raises the turn above
`AGENT_TURN_TOKEN_BUDGET_MAX`; once the turn is at that maximum and runs out
again it ends for good, the harness note says so plainly, no further grant is
offered and no state is kept to resume.

The old all-or-nothing **Enforce agent budget caps** switch (Settings >
Capabilities) is gone; the per-turn bound is the setting above. A stored off
value from an earlier install is left untouched and maps to
`AGENT_TURN_TOKEN_BUDGET_MAX`, so a former caps-off install never silently gets
a smaller bound. Hard runaway backstops remain far above the bound: 10k
iterations, 10k tool calls and a 30-minute wall clock.

## Turn approvals

| Variable | Default | Purpose |
|---|---|---|
| `CONSENT_SKEW_SECONDS` | `2` | A reply counts as approval only when its message arrived more than this many seconds after the pending prompt was created. |

A whole-message **yes** or **autopilot** is consent only if it was sent after
the prompt it answers. A reply that waited on a running turn, or came from a
second tab, cannot approve a prompt created after it was sent: the action stays
pending and the message runs as a new turn, with the prompt shown again last. A
**no** or **stop** still cancels, whichever timestamp it carries.

The margin absorbs small clock differences between app hosts, so keep every
host on NTP. A missing or unreadable timestamp fails closed and runs nothing.

## Skills

| Variable | Default | Purpose |
|---|---|---|
| `SKILL_MAX_CHARS` | `8000` | Cap on a loaded skill's instruction body. |

## Identity & demo mode

| Variable | Default | Purpose |
|---|---|---|
| `KAZI_AGENT_NAME` | `Kazi` | The agent's display name. |
| `KAZI_DEMO_MODE` | unset | Boot with example connectors and no real credentials. See [Demo Mode](demo-mode.md). |
| `ORCHESTRATION_STRICT_STARTUP_CHECKS` | `True` | Fail fast on malformed connector catalog entries at boot rather than warn-and-skip. |

## Messaging

| Variable | Default | Purpose |
|---|---|---|
| `TELEGRAM_BOT_TOKEN` | empty | Enables the Telegram bot connector. |

## Presence

The server keeps the heartbeat; the page sends nothing.

| Variable | Default | Purpose |
|---|---|---|
| `PRESENCE_BEAT_SECONDS` | `30` | How often each open chat connection refreshes its presence entry. Values below `1` are raised to `1`. |
| `PRESENCE_WINDOW_SECONDS` | `75` | How long after its last beat a connection still counts as online. This bounds how long a user stays "online" after a web process dies without closing its sockets. Values below `2 × beat + 15` are raised to that, so one late beat does not drop a connected user. |

## Moderation & scheduled sweeps

| Variable | Default | Purpose |
|---|---|---|
| `MODERATION_ENABLED` | auto (on if `HF_API_TOKEN` is set) | Content moderation toggle. Needs Redis; the lite profile refuses an explicit `1` and ignores the `HF_API_TOKEN` default. |
| `MODERATION_FLUSH_SECONDS` | `600` | Moderation batch flush interval. |
| `REMINDER_SWEEP_SECONDS` | `3600` | Reminder sweep interval. |
| `WORKFLOW_REPLAY_SCHEDULE_SECONDS` | `300` | Replay-safety watchdog interval. |

## Integrations (all optional)

| Variable | Default | Purpose |
|---|---|---|
| `OPENWEATHER_API_KEY` | empty | Weather connector. |
| `GIPHY_API_KEY` | empty | GIF connector. |
| `EXCHANGE_RATE_API_KEY` | empty | Currency conversion. |
| `CALENDLY_CLIENT_ID` / `CALENDLY_CLIENT_SECRET` | — | Calendly OAuth. |
| `GMAIL_OAUTH_CLIENT_ID` / `GMAIL_OAUTH_CLIENT_SECRET` | — | Gmail OAuth (aliases `GOOGLE_CLIENT_ID` / `GOOGLE_CLIENT_SECRET`). |
| `GMAIL_OAUTH_REDIRECT_URI` | empty | Gmail OAuth redirect. |
| `INTASEND_WEBHOOK_SECRET` | — | IntaSend webhook verification. |
| `GOOGLE_*` / `GITHUB_*` / `LINKEDIN_*` / `TWITTER_*` | — | Social login via django-allauth. |

## Which tools are offered (T-F2)

A turn is offered only the tools that can work for this user in this room. Three
things cut the list, and nothing adds or removes a tool based on the words of a
message.

1. **Configured at boot.** An action whose connector did not load is never
   offered. A connector is skipped when `validate_config()` fails — a missing
   key (`TELEGRAM_BOT_TOKEN`, `SHELL_EXEC_TOKEN`, `OPENWEATHER_API_KEY`,
   `GIPHY_API_KEY`, `EXCHANGE_RATE_API_KEY`, `CALENDLY_CLIENT_ID` /
   `CALENDLY_CLIENT_SECRET`, `TWILIO_ACCOUNT_SID` /
   `TWILIO_AUTH_TOKEN`, `MAILGUN_API_KEY` / `MAILGUN_DOMAIN`,
   `GMAIL_OAUTH_CLIENT_ID` / `GMAIL_OAUTH_CLIENT_SECRET`), a missing library
   (the IntaSend SDK), and so on. A connector that can only mock a send without
   credentials (Mailgun, WhatsApp) is offered only when `KAZI_DEMO_MODE` is set;
   outside demo mode a missing key means the group is not offered. In
   `manage.py shell`: `get_skipped_connectors()` lists what was skipped (with
   the actions it would own); `get_available_actions()` is the live set.
2. **Capability flags.** Each group has a gate; turning it off removes its
   tools. The sensitive gates fail closed: a missing flag means off, as at
   execution. Every other gate keeps the old default — missing means on.

   | Gate | Group | Default |
   |---|---|---|
   | `allow_email` | Email | on (sensitive) |
   | `allow_whatsapp` | WhatsApp | on (sensitive) |
   | `allow_payments` | Payments (wallet, invoices, IntaSend) | on (sensitive) |
   | `allow_calendar` | Calendly | on |
   | `allow_travel` | Travel search & itineraries | on |
   | `allow_web_search` | Search, weather, GIF, currency | on |
   | `allow_reminders` | Reminders & notifications | on |
   | `allow_telegram` | Telegram | on (newly gated) |
   | `allow_voice` | `generate_speech` | on (newly gated) |

   Telegram and voice default **on**, so an install already using them keeps
   them; the gates only let a user turn a group off.
3. **Persona scope.** In a room whose active persona has a `tool_scope`, tools
   outside it are not offered. The execution-time refusal stays as a second line.

The prompt follows the tools: the shell "Where you are running" section is sent
only with `run_command` (and says whether the room's profile is sandboxed, plus
the network that profile gives the shell — open: full; standard: none unless a
command asks and is approved; locked: none), and the memory/contact/reminder/
voice guidance only when its group is offered. With no search tool the prompt
says only that there is no web-search tool, not that the network is unreachable.
The model sees one `run_command` description, with a `network` parameter,
whichever shell profile the room uses.

## Voice / modality

`generate_speech` turns text into a voice note (v0.6 §4.2). Per-channel flags
(`supports_voice`, `supports_image`, `max_audio_bytes`) live in
`notifications/capabilities.py`; a channel without voice degrades to text + a
note.

The connector is **provider-agnostic**: it speaks the OpenAI-compatible
`/audio/speech` shape, so OpenAI, a self-hosted Llama/Kokoro/LocalAI server, or
any compatible endpoint all work. Pick a provider and (for self-hosted) point
`TTS_URL` at it.

| Variable | Default | Purpose |
|---|---|---|
| `TTS_PROVIDER` | `openai` | Provider preset: `openai` or `openai_compatible` (self-hosted / any compatible endpoint). |
| `TTS_URL` | provider default | Speech endpoint. Required for `openai_compatible`. |
| `TTS_API_KEY` | empty | Credential override; else the provider's key (e.g. `OPENAI_API_KEY`). Optional for keyless self-hosted endpoints. |
| `TTS_MODEL` | provider default | TTS model. |
| `TTS_VOICE` | provider default | Default voice. |

## Object storage (Cloudflare R2 / S3)

| Variable | Default | Purpose |
|---|---|---|
| `R2_ENABLED` | `False` | Use R2/S3-compatible storage. |
| `R2_ACCESS_KEY_ID` / `R2_SECRET_ACCESS_KEY` | — | Credentials. |
| `R2_BUCKET_NAME` | — | Bucket. |
| `R2_ENDPOINT_URL` | — | S3 endpoint. |
| `R2_REGION` | `auto` | Region. |
| `R2_PUBLIC_BASE_URL` | empty | Public base URL for stored files. |

## Workflows & Temporal

| Variable | Default | Purpose |
|---|---|---|
| `TEMPORAL_HOST` | `localhost:7233` | Temporal server address. |
| `TEMPORAL_NAMESPACE` | `default` | Temporal namespace. |
| `TEMPORAL_TASK_QUEUE` | `user-workflows` | Task queue. |
| `TEMPORAL_DISABLED` | `False` | Disable Temporal entirely. |
| `WORKFLOW_WITHDRAW_MAX` | `10000` | Max withdraw amount for gated workflow steps. |
| `WORKFLOW_APPROVALS_UPDATE_API` | `False` | Deliver approval decisions via the Temporal Workflow Update API instead of fire-and-forget signals. Reversible. |
| `WORKFLOW_APPROVAL_SWEEP_SECONDS` | `300` | Interval for the stuck-approval sweeper beat job. |
| `WORKFLOW_APPROVAL_SWEEP_BATCH_LIMIT` | `100` | Max approvals the sweeper handles per pass. |
| `WORKFLOW_APPROVAL_MAX_PENDING_AGE_SECONDS` | `86400` | Age after which a pending approval is dead-lettered. |
| `TRAVEL_ALLOW_FALLBACK` | `DEBUG` | Allow fallback travel search results. |

### Skills, routines, grants and health (v0.7)

| Variable | Default | Purpose |
|---|---|---|
| `PROMOTION_MIN_OCCURRENCES` | `3` | Times a tool sequence must repeat before it is mined as a skill candidate. |
| `PROMOTION_MIN_SUCCESS_RATE` | `0.8` | Minimum success rate of those runs. |
| `ROUTINE_ABSENCE_IDLE_DAYS` | `14` | Owner idle time before the keep-running prompt for schedule and webhook workflows. |
| `ROUTINE_ABSENCE_PROMPT_WINDOW_DAYS` | `3` | Days to answer that prompt before the workflows are paused. |
| `ROUTINE_HISTORY_LIMIT` | `20` | Routine history entries kept. |
| `SKILL_STALE_AFTER_DAYS` | `90` | Unused days before the curator marks a skill stale. |
| `SKILL_ARCHIVE_AFTER_DAYS` | `180` | Unused days before the curator archives it. |
| `STANDING_GRANT_LIFETIME_DAYS` | `30` | Lifetime of a standing grant before it lapses. |
| `APPROVAL_TELEMETRY_WINDOW_DAYS` | `1` | Window for the nightly approval telemetry rollup. |
| `APPROVAL_PROMOTE_THRESHOLD_RATE` | `0.95` | Approval rate at which a rule becomes a promotion candidate. |
| `APPROVAL_PROMOTE_THRESHOLD_COUNT` | `50` | Minimum decisions before a rule can be a candidate. |
| `WORKFLOW_HEALTH_WINDOW_HOURS` | `24` | Window the hourly health check looks at. |
| `WORKFLOW_HEALTH_FAILURE_SPIKE` | `3` | Failed runs in the window that auto-pause a workflow. |
| `WORKFLOW_HEALTH_DEGRADED_RATE` | `0.3` | Failure rate at which a workflow is reported degraded. |
| `SHADOW_REPLAY_WINDOW` | `10` | Recorded runs a shadow replay uses, unless the definition sets its own. |
| `WORKFLOW_REVIEWER_ENABLED` | `true` | Master switch for the weekly reviewer. It still needs `LLM_REVIEWER_MODEL`. |
| `WORKFLOW_REVIEWER_WINDOW_DAYS` | `30` | Execution history window the reviewer reads. |
| `HANDOFF_DEFAULT_BUDGET` | `20` | Default tool-call budget for a handoff. |
| `HANDOFF_MAX_BUDGET` | `50` | Upper bound on a handoff's budget. |

## Governed shell (v0.6)

`run_command` executes one command through a deliberately dumb sidecar process
in a sandboxed container (non-root, read-only rootfs, `--cap-drop=ALL`,
network off by default, memory/PID caps). Start the sidecar with
`python Backend/manage.py run_shell_exec`, or with
`python -m orchestration.shell_exec` from `Backend/`. Both run the same server
with the same settings; the `-m` entry point does not start a Django process
and reads `.env` directly. A `SHELL_EXEC_TOKEN` already present in the process
environment takes precedence over the `.env` value, so a launcher can rotate the
token without editing the file; the rest of `.env` keeps its usual precedence.
The sandbox is the security boundary; the command classifier is UX and a
tripwire. See the
[credential-scoping contract](contracts/credential-scoping.md).

Isolation profiles (roadmap §3), resolved per room (override) else the global
default:

| | `open` | `standard` (default) | `locked` |
|---|---|---|---|
| Backend | direct subprocess | Docker | Docker |
| Root filesystem | full | read-only | read-only |
| Writable | full | `/workspace` (read-write) | none — `/workspace` is mounted read-only |
| Network | full | off by default | none |
| Environment | minimal allowlist (below) | none inherited from the sidecar | none inherited from the sidecar |

`open` is never the default and logs a loud boot warning — it runs unsandboxed
on the host, so only enable it on a disposable box.

A command under `open` does **not** inherit the sidecar's environment. The
sidecar passes only a fixed allowlist: `PATH`, `HOME` (or `USERPROFILE`), a temp
directory (`TMPDIR`/`TEMP`/`TMP`), locale and terminal settings, and the Windows
variables `cmd.exe` needs to start (`SystemRoot`, `COMSPEC`, `PATHEXT`, and the
like). Every other variable is dropped, including every key in `.env` — provider
keys, `DATABASE_URL`, `REDIS_URL`, `CELERY_*`, `DJANGO_SECRET_KEY`,
`ENCRYPTION_KEY` and `SHELL_EXEC_TOKEN`. Because it is an allowlist and not a
denylist, a variable an operator adds to the sidecar's environment is dropped
too. There is no setting to widen it.

This closes the environment, not the filesystem: an `open` command runs as the
sidecar's operating-system user and can read any file that user can read,
including `.env`. Run an `open` sidecar as a separate user that cannot read the
Kazi checkout, and start it without secrets in its environment.

A command under `locked` can read the room workspace but not change it: the
workspace is mounted read-only, so create/change/delete fail. `standard` keeps
the read-write `/workspace` mount.

The per-room workspace (`<SHELL_EXEC_ROOT>/workspaces/<room_id>`) persists across
commands. Before a **destructive** command the sidecar tars it to
`<SHELL_EXEC_ROOT>/snapshots/<room_id>/`; roll back with
`python Backend/manage.py shell_rollback --room <room_id> --snapshot <file>`.

| Variable | Default | Purpose |
|---|---|---|
| `SHELL_EXEC_TOKEN` | empty | Shared bearer token between Kazi and the sidecar. **Empty disables `run_command`.** |
| `SHELL_EXEC_HOST` | `127.0.0.1` | Sidecar bind host (and the host Kazi connects to). |
| `SHELL_EXEC_PORT` | `8765` | Sidecar bind/connect port. |
| `SHELL_EXEC_PROFILE` | `standard` | Default profile. `standard`/`locked` run in the Docker sandbox; `open` runs unsandboxed on the host. See the profile table above for what each may see and write. |
| `SHELL_EXEC_PROFILES` | the profile above | Comma-separated profiles the sidecar will serve. A request for any other profile is rejected with 400. Add `open` only on a disposable box. |
| `SHELL_EXEC_ROOT` | `<repo>/shell_workspaces` | Root for per-room workspaces mounted at `/workspace`. |
| `SHELL_EXEC_IMAGE` | `alpine:3.20` | Container image for the Docker backend. |
| `SHELL_EXEC_USER` | `65534:65534` | Non-root uid:gid the container runs as. |
| `SHELL_EXEC_MEMORY` | `256m` | Docker `--memory` cap. |
| `SHELL_EXEC_PIDS_LIMIT` | `128` | Docker `--pids-limit` cap. |
| `SHELL_EXEC_TIMEOUT_DEFAULT` | `120` | Default per-command timeout (seconds). |
| `SHELL_EXEC_TIMEOUT_MAX` | `600` | Hard per-command timeout ceiling (seconds). |
| `SHELL_EXEC_OUTPUT_BYTES_MAX` | `65536` | Truncate returned stdout/stderr beyond this many bytes. |
| `SHELL_EGRESS_PROXY` | `false` | Enforced egress for the `standard` profile. Each network command gets its own internal network and its own stock Squid, and can only open HTTPS tunnels to approved hosts. On an untainted run, HTTPS-capable commands (`pip`, `npm`, `git` over https, `curl`) then run **without a prompt**; a tainted run, and any publish/push/upload command, still asks. Needs Docker Engine 28+. Run `python scripts/verify_shell_egress.py` on the sidecar host first. |
| `SHELL_EGRESS_DEFAULT_HOSTS` | built-in registry list | Comma-separated hosts every room may reach through the proxy. Unset = the built-in list of package registries and their CDNs (`orchestration/shell/egress.py`). Set it, even to an empty value, to replace that list. A leading dot matches subdomains (`.example.com`). An approved host is trusted for upload as well as download. |
| `SHELL_EGRESS_PROXY_IMAGE` | `kazi-egress-squid:1` | Image that runs Squid. Built on first use from `Backend/orchestration/shell_exec/egress/Dockerfile` (Debian `squid-openssl`). |
| `SHELL_HOST_GRANT_DAYS` | `30` | Lifetime of a room's host approval (the chat reply `allow host <name>`). |
| `SHELL_HOST_GRANT_MAX_DAYS` | `365` | Upper bound on a host approval's lifetime, however it is created. |
| `SHELL_HOST_GRANT_APPROVERS` | `members` | Who may approve a new host for a room: `members` (any member of the room) or `staff` (a staff user who is a member). Any member may revoke. Unrecognised values mean `staff`. |
| `AGENT_TAINT_TTL_SECONDS` | `900` | How long a room stays tainted, for every member, after untrusted text (shell or web-search output) enters it. While tainted, egress shell commands (including allowlisted hosts) and stored "auto" rules for sensitive actions ask again. The window restarts when new untrusted output arrives; `0` limits taint to the run that picked it up. |
| `SHELL_AUTOPILOT_MINUTES` | `30` | Default length of the human-armed autopilot window. Only the unsandboxed `open` profile has one; sandboxed profiles auto-run anything that stays inside the sandbox and ask only for network. |
| `SHELL_AUTOPILOT_MAX_MINUTES` | `120` | Hard ceiling on an autopilot window, however it is armed (chat, ops inbox, `manage.py shell_autonomy`). |
| `SHELL_EXEC_NETWORK_ALLOWLIST` | empty | Comma-separated hosts whose network commands run **without a prompt** (e.g. your ISP/gateway). A UX shortlist, **not** a firewall — the sandbox still runs the command non-root, read-only, on the Docker bridge. With `SHELL_EGRESS_PROXY` on, host names here are also added to every room's enforced proxy allowlist (IP entries are ignored there). |

## Eval

| Variable | Default | Purpose |
|---|---|---|
| `SCENARIO_PACK_MIN_SIZE` | `3` | Minimum scenarios each capability pack in the golden harness must ship. Guarded by `test_scenario_packs`. See [Eval](eval.md). |

## Learning (preference mining)

Approve/deny history is mined nightly into room-scoped learned approval
overrides (v0.6 L2, #152). Denials outrank approvals; learned overrides are
reversible and never override an explicit user setting.

| Variable | Default | Purpose |
|---|---|---|
| `PREFERENCE_MINING_ENABLED` | `True` | Enable the nightly mining task. |
| `PREFERENCE_MINING_MIN_APPROVALS` | `3` | Approvals (with zero denials) needed to earn an `auto` override. |
| `PREFERENCE_MINING_WINDOW_DAYS` | `30` | How far back approval history is mined. |
| `PROACTIVE_BUDGET_PER_DAY` | `2` | Max proactive actions (rung 4) per user per day. |

The initiative ladder (#154) also adds a morning daily digest
(`send_daily_digest`) listing fired watches and open proposals, and approved-rule
promotion. Proactive actions never exceed the safe tier unless an approved rule
exists, and every rung-4 run writes a receipt.

## Celery tunables

| Variable | Default | Purpose |
|---|---|---|
| `CELERY_BEAT_SCHEDULER` | `django_celery_beat.schedulers:DatabaseScheduler` | Scheduler class. The beat schedule is materialized into the DB via `python Backend/manage.py sync_beat_schedule`, so runtime edits survive deploys. |
| `CELERY_TASK_IGNORE_RESULT` | `True` | Don't store task results by default. |
| `CELERY_RESULT_BACKEND` | `django-db` | Celery result backend (env-overridable; `.env.example`/compose point it at Redis). |
| `CELERY_CONCURRENCY` | `1` | Worker concurrency. |
| `CELERY_WORKER_MAX_TASKS_PER_CHILD` | `200` | Tasks per worker child. |
| `CELERY_WORKER_MAX_MEMORY_PER_CHILD` | `250000` | Memory cap (KiB) per child. |
| `CELERY_RESULT_EXPIRES` | `3600` | Result expiry (seconds). |

## See also

- [Deploy Safely](deploy-safely.md) — the production checklist
- [Run Locally](run-locally.md) — a minimal `.env` that works
