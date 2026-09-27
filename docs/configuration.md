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
| `SHELL_EXEC_NETWORK_ALLOWLIST` | empty | Comma-separated hosts whose network commands run **without a prompt** (e.g. your ISP/gateway). A UX shortlist, **not** a firewall — the sandbox still runs the command non-root, read-only, on the Docker bridge. |

## Eval

| Variable | Default | Purpose |
|---|---|---|
| `SCENARIO_PACK_MIN_SIZE` | `3` | Minimum scenarios each capability pack in the golden harness must ship. Guarded by `test_scenario_packs`. See [Eval](eval.md). |

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
