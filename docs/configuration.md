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

## LLM providers & routing

| Variable | Default | Purpose |
|---|---|---|
| `ANTHROPIC_API_KEY` | — | Claude API key (primary provider). |
| `HF_API_TOKEN` | — | Hugging Face token (fallback provider). |
| `DEEPSEEK_API_KEY` | — | DeepSeek API key (OpenAI-compatible, full tool-calling). |
| `DEEPSEEK_MODEL` | `deepseek-chat` | Model name for DeepSeek. |
| `DEEPSEEK_BASE_URL` | `https://api.deepseek.com/v1/chat/completions` | DeepSeek endpoint. |
| `LLM_PLANNER_PROVIDER` | `anthropic` | Provider for the planner. |
| `LLM_EXECUTOR_PROVIDER` | `huggingface` | Provider for tool execution. |
| `LLM_PLANNER_MODEL` / `LLM_EXECUTOR_MODEL` | empty | Override the model per role. |
| `LLM_REVIEWER_PROVIDER` | `anthropic` | Provider for the workflow reviewer. |
| `LLM_REVIEWER_MODEL` | empty | Reviewer model. The scheduled reviewer runs only when this names a model different from the authoring models; empty keeps it off. |
| `LLM_MAX_TOKENS` | `700` | Hard per-call token ceiling. |
| `LLM_PROMPT_CHAR_LIMIT` | `4000` | User prompt truncation cap. |
| `LLM_CACHE_ENABLED` | `True` | Response caching toggle. |
| `LLM_CACHE_TTL_SECONDS` | `600` | Cache TTL. |
| `LLM_CACHE_MIN_TEMP` | `0.3` | Don't cache calls below this temperature. |
| `MANAGER_LLM_ENABLED` | `True` | Manager-verifier LLM passes. |

The **model catalog** (`orchestration/model_catalog.py`) is a fixed list of
models the chatroom picker offers; which ones are available is gated purely by
which provider keys are set. Users override the model **per room** from the
chatroom header (or `GET/POST /api/rooms/<id>/model/`) — the override is stored
as a Redis preference, not an env var. Leave it on *Auto* to use the provider
fallback above.

## Quotas

| Variable | Default | Purpose |
|---|---|---|
| `LLM_TOKEN_QUOTA_ENABLED` | `True` | Per-user token quota enforcement. |
| `LLM_TOKEN_LIMIT_PER_USER_PER_HOUR` | `50000` | Hourly token budget per user. |

## Memory & context budgets

| Variable | Default | Purpose |
|---|---|---|
| `CONTEXT_PROMPT_MAX_CHARS` | `8000` | Cap on room context injected into the system prompt. |
| `HISTORY_MAX_CHARS` | `60000` | History budget before compaction. |
| `HISTORY_MAX_MESSAGES` | `50` | Max history turns kept per agent loop. |
| `HISTORY_COMPACTION_ENABLED` | `True` | Trim the oldest turns when the budget is exceeded (opt-out). |

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
| `PRESENCE_BEAT_SECONDS` | `30` | How often each open chat connection refreshes its presence entry. |
| `PRESENCE_WINDOW_SECONDS` | `75` | How long after its last beat a connection still counts as online. This bounds how long a user stays "online" after a web process dies without closing its sockets. Values below `2 × beat + 15` are raised to that, so one late beat does not drop a connected user. |

## Moderation & scheduled sweeps

| Variable | Default | Purpose |
|---|---|---|
| `MODERATION_ENABLED` | auto (on if `HF_API_TOKEN` is set) | Content moderation toggle. |
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
`python Backend/manage.py run_shell_exec`. The sandbox is the security
boundary; the command classifier is UX and a tripwire. See the
[credential-scoping contract](contracts/credential-scoping.md).

Isolation profiles (roadmap §3), resolved per room (override) else the global
default:

| | `open` | `standard` (default) | `locked` |
|---|---|---|---|
| Backend | direct subprocess | Docker | Docker |
| Root filesystem | full | read-only | read-only |
| Writable | full | `/workspace` | none |
| Network | full | off by default | none |

`open` is never the default and logs a loud boot warning — it runs unsandboxed
on the host, so only enable it on a disposable box.

The per-room workspace (`<SHELL_EXEC_ROOT>/workspaces/<room_id>`) persists across
commands. Before a **destructive** command the sidecar tars it to
`<SHELL_EXEC_ROOT>/snapshots/<room_id>/`; roll back with
`python Backend/manage.py shell_rollback --room <room_id> --snapshot <file>`.

| Variable | Default | Purpose |
|---|---|---|
| `SHELL_EXEC_TOKEN` | empty | Shared bearer token between Kazi and the sidecar. **Empty disables `run_command`.** |
| `SHELL_EXEC_HOST` | `127.0.0.1` | Sidecar bind host (and the host Kazi connects to). |
| `SHELL_EXEC_PORT` | `8765` | Sidecar bind/connect port. |
| `SHELL_EXEC_PROFILE` | `standard` | Default profile. `standard`/`locked` run in the Docker sandbox; `open` runs unsandboxed on the host (Phase 2 refines the per-profile rules). |
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
