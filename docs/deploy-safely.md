# Deploy Safely

The fifth developer flow. What to set, what to lock down, what to
monitor, what *not* to expose.

If you can fit your deploy into one of these patterns, this page has
you covered. If you need something more bespoke (multi-region,
zero-downtime migrations, custom secret stores), use this as the
checklist and adjust.

## The required env

These environment variables **must** be set in production. Missing
any of them is a misconfiguration; the runtime should not boot.

| Var | Purpose |
|---|---|
| `DJANGO_SECRET_KEY` | Must be a real, random secret. Never use the demo or CI value. |
| `DJANGO_ALLOWED_HOSTS` | Comma-separated list of hostnames you serve. |
| `DJANGO_CSRF_TRUSTED_ORIGINS` | Comma-separated list of `https://...` origins. |
| `DATABASE_URL` | A real Postgres URL. SQLite is **not** supported in production. |
| `REDIS_URL` | Real Redis (Upstash `rediss://` works). Channels + Celery require it. |
| `CELERY_BROKER_URL` | Usually the same `REDIS_URL`. |
| `CELERY_RESULT_BACKEND` | Usually the same `REDIS_URL`. |

Plus per-connector credentials for whichever connectors you actually
use (`ANTHROPIC_API_KEY`, `OPENWEATHER_API_KEY`, `CALENDLY_CLIENT_*`,
`GMAIL_*`, `TWILIO_*`, etc.). The runtime skips connectors with
missing credentials at boot with a clean log line — it doesn't crash.

## The required env that **must not** be set

| Var | Why it must be off in prod |
|---|---|
| `KAZI_DEMO_MODE` | Enables example connectors with fake credentials. The boot banner is loud, but loud isn't enough — never set this in prod. |
| `DJANGO_DEBUG=true` | Leaks tracebacks. Always `false` in prod. |

## Deployment shape

The minimum production stack:

| Service | Image | Notes |
|---|---|---|
| Web (ASGI) | The official `kazi-core` container | Daphne or uvicorn; behind a reverse proxy with TLS. |
| Celery worker | Same image, different command | At least one. Scale based on background-task throughput. |
| Celery beat | Same image, different command | Exactly one across the cluster. |
| Postgres 15+ | Managed (RDS, Cloud SQL) or self-hosted | Backups + WAL archiving. |
| Redis 7+ | Managed (Upstash, ElastiCache) or self-hosted | Persistence on. |
| Temporal worker (optional) | Same image, different command | Required for durable workflows; the human-gated runtime depends on this for cross-restart approval pauses. |

A reverse proxy (nginx, Caddy, or the cloud's ingress) terminates TLS
and forwards `/ws/` traffic with the right WebSocket headers.

## What to lock down

- **`/admin/`** — Django admin should be IP-restricted or behind SSO.
  Operators can drive every workflow action from here, including
  `force=true` reruns.
- **`/api/workflows/executions/<id>/{approve,reject,cancel,rerun}/`**
  — these endpoints currently rely on Django session auth. Consider
  putting them behind your normal auth proxy and enforcing role-based
  access on top.
- **Webhook endpoints** — verify signatures (Calendly, IntaSend,
  Mailgun, etc.). The framework includes
  [`webhook_validator.py`](https://github.com/bedah-kym/kazi-core/blob/main/Backend/orchestration/webhook_validator.py)
  helpers.
- **Connector credentials** — never commit. Use your platform's
  secret manager (AWS Secrets Manager, GCP Secret Manager, Doppler,
  1Password Connect) and inject as env at deploy time.
- **The container image** — official releases are
  [cosign-signed](https://github.com/sigstore/cosign) (keyless via
  Sigstore OIDC) and ship SBOM + SLSA provenance attestations.
  Verify before pulling into your prod registry.

## What to monitor

| Signal | Why |
|---|---|
| Boot log line "Total connector map: N action mappings" | Should match the connector set you expect; anything missing is a credential or import problem. |
| Boot warning "catalog entry violates tool-schema contract" | A connector PR shipped a malformed entry. Fix it. |
| `WorkflowExecution.status` distribution | Sustained `waiting` or `abandoned` counts mean operators aren't responding or watchdog is dead-lettering. |
| `dead_letter_reason` time series | Spikes correlate with external integrations going down. |
| Celery beat liveness | If the beat dies, the watchdog stops; runs accumulate in `waiting`. |
| Temporal worker lag | If you use Temporal, lag means approval signals queue up. |

The runtime emits a JSONL stream to
`telemetry/orchestration.jsonl` when
`ORCHESTRATION_TELEMETRY_ENABLED=true` (default). Ship those lines
to your log aggregator for free observability.

## Rolling deploys

The runtime is designed to survive worker restarts:

- In-flight `WorkflowExecution` rows persist their state to the DB.
- Approval pauses are durable — Temporal signal-waits resume after
  worker restart.
- Watchdog is idempotent — a missed tick gets caught up on the next
  beat.

The safe rolling-deploy order is **migrate → workers → beat → web**.
Migrations should be backward-compatible (add columns nullable, then
backfill, then enforce in a follow-up release).

## What can happen and what you do

| Incident | First step |
|---|---|
| Web returns 502 | Check Daphne; restart the container if needed. In-flight runs survive. |
| All workflows stuck in `waiting` | Check Temporal worker; check Celery worker; check Redis. |
| One workflow abandoned with `dead_letter_reason: temporal_unavailable` | Restart Temporal worker; rerun the abandoned execution from the safe step. |
| A connector spams errors | Check that connector's credentials and rate limits; it'll be skipped at boot if creds go missing. |
| You suspect a malformed payload from an LLM | `kazi_trace <execution_id> --json` gives you the full struct to attach to the bug. |

## Things you don't have to do

- **Run a separate API server.** Django + Channels + Daphne is the
  whole web tier.
- **Operate your own LLM.** The default
  [`llm_client`](https://github.com/bedah-kym/kazi-core/blob/main/Backend/orchestration/llm_client.py) talks to
  Anthropic; you bring the key. HuggingFace is the fallback.
- **Stand up a UI.** Operator surfaces ship as JSON APIs + Django
  admin. A community web UI may follow under
  [`examples/`](https://github.com/bedah-kym/kazi-core/tree/main/examples/) — until then, your existing internal
  tools or a `curl`-driven runbook is enough.

## Next

- [`docs/run-locally.md`](run-locally.md) — to test config changes
  before pushing.
- [`docs/operate-a-workflow.md`](operate-a-workflow.md) — what your
  on-call will be doing in production.
- [`docs/contracts/`](contracts/README.md) — what your monitoring can
  rely on staying stable.
