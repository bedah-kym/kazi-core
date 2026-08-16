# Run Locally

The first developer flow. From a clean clone to **a paused workflow
waiting on operator approval in under 10 minutes**, no real API keys.

## Prerequisites

- Docker (Desktop or your distro's `docker` package).
- Python 3.11 or 3.12 if you want to run tests outside the container.
- ~2GB free disk for the Postgres + Redis + web image.

That's it. Anthropic / Gmail / Calendly / WhatsApp credentials are
**not** required for local development; they are only needed when you
swap demo connectors for real ones.

## The 10-minute path

```bash
git clone https://github.com/bedah-kym/kazi-core.git
cd kazi-core

# 1. The single-command demo driver (~90s on a warm Docker cache)
bash scripts/demo.sh
```

`scripts/demo.sh` boots the stack with `KAZI_DEMO_MODE=true`, runs
migrations, ensures a demo user exists, seeds the canonical demo
workflow, and prints the curl commands to drive it through the
**human-gated runtime loop** (start → pause for approval → approve →
complete → replay-safety check).

If the script printed a workflow id, you're done with the boot. Walk
through the operator commands it printed. The full walkthrough lives
at [`examples/workflows/follow_up_email/README.md`](https://github.com/bedah-kym/kazi-core/blob/main/examples/workflows/follow_up_email/README.md).

> **On Windows:** run `bash scripts/demo.sh` from Git Bash or WSL, or skip the
> script and use the manual path below.

## What just happened

The stack you booted has:

| Service | What it does |
|---|---|
| `db` | Postgres 15. Hosts workflow definitions, executions, receipts. |
| `redis` | Cache + Channels backend + Celery broker. |
| `web` | The Django ASGI server. Runs the agent loop, the workflow runtime, and the operator API. |
| `celery_worker` | Background tasks (notifications, deferred-run watchdog). |
| `celery_beat` | Periodic scheduling (watchdog ticks, retries). |

`KAZI_DEMO_MODE=true` (logged loudly at boot) enables the example
connector at `examples/connectors/echo/` and is the reason the demo
workflow can run with no credentials. See
[`docs/demo-mode.md`](demo-mode.md) for the full flag reference.

## Without the demo script

If you want the manual path:

```bash
docker compose up --build -d db redis web celery_worker celery_beat
docker compose exec web python Backend/manage.py migrate
docker compose exec web python Backend/manage.py createsuperuser
# Then drive any chat or workflow surface manually.
```

For a non-Docker setup (Postgres + Redis on host), see
[`docs/quickstart.md`](quickstart.md).

## Troubleshooting

| Symptom | Likely cause |
|---|---|
| `web` container exits immediately | Postgres or Redis health check is still failing — wait 30s and retry. |
| Browser hits `502` | Daphne is still starting — the demo script's health-poll handles this; retry after 5s. |
| Boot banner missing in logs | `KAZI_DEMO_MODE` not set; check your env or `.env`. |
| `demo_seed` errors with "User not found" | The demo script creates the user automatically; if you ran without it, `createsuperuser` first. |
| Tests use SQLite, want real Postgres | Set `DATABASE_URL=postgres://...` in your env before `manage.py test`. |

## Next

- **Add a connector** → [`add-a-connector.md`](add-a-connector.md)
- **Add a workflow** → [`add-a-workflow.md`](add-a-workflow.md)
- **Operate a running workflow** → [`operate-a-workflow.md`](operate-a-workflow.md)
- **Deploy to production** → [`deploy-safely.md`](deploy-safely.md)
