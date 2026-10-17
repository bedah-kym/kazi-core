# Run the lite profile

The lite profile is for a small, chat-only install: one web process, the shell
sidecar, an in-memory cache and Channels, no Redis, no Celery worker or beat,
and no Temporal. It is selected by `KAZI_LITE_PROFILE=1` and does **not**
require `DEBUG`, so it is safe to expose on a server.

This page runs it from a checkout. See [Configuration](configuration.md#lite-profile-chat-only-no-redis-or-celery)
for the setting reference.

## What you need

- Python 3.11 or 3.12 and the repository dependencies (`pip install -r requirements.lock`).
- Docker only if you want `run_command` with the sandboxed `standard` profile.
- No Postgres, no Redis, no Celery. `DATABASE_URL` unset uses SQLite.

## Configure

Put this in `.env` at the repo root (never commit it):

```dotenv
DJANGO_DEBUG=False
DJANGO_SECRET_KEY=replace-with-a-long-random-value
DJANGO_ALLOWED_HOSTS=localhost,127.0.0.1
DJANGO_CSRF_TRUSTED_ORIGINS=http://localhost:8000
KAZI_LITE_PROFILE=1

# Empty disables run_command. Set a long random value to enable the sidecar.
SHELL_EXEC_TOKEN=replace-with-a-shared-token

# Pick one LLM provider, or leave blank to boot without chat answers.
ANTHROPIC_API_KEY=
```

The lite profile starts the in-process scheduler automatically. Do not also
start `celery beat`; it refuses to start with this setting on. The web process
must have exactly one worker: in-memory Channels cannot fan a room out across
processes, and the ASGI app refuses to load when a platform worker variable
says more than one.

## Prepare the database

```bash
python Backend/manage.py migrate
python Backend/manage.py createsuperuser
```

SQLite is the default and needs no service. If you set `DATABASE_URL`, the lite
profile uses it unchanged.

## Start the sidecar

The sidecar does not import Django. Run it from `Backend/`:

```bash
cd Backend
python -m orchestration.shell_exec
```

`python manage.py run_shell_exec` starts the same server for a Django operator
who already has that process. Both read the same `SHELL_EXEC_*` environment and
`.env` file.

## Start the web process

In a second terminal, from `Backend/`:

```bash
uvicorn Backend.asgi:application --host 0.0.0.0 --port 8000 --workers 1
```

Use uvicorn, not Daphne: the in-process scheduler starts from the ASGI lifespan,
which Daphne does not run.

## Reminders

Reminders are stored in the database and the in-process scheduler's reminder
sweep delivers them. In the lite profile the sweep runs every 30 seconds
(`REMINDER_SWEEP_SECONDS`), so a reminder set for two minutes ahead is delivered
in chat shortly after it is due. The delivery claim works on LocMem and Redis
and fails closed: if the claim store cannot be written, the reminder is not
delivered twice.

## Memory

Acceptance target for the reference small install: **at or under 220 MB peak RSS
for the web process plus the sidecar after five turns**, and **at or under
45 MB for the sidecar**, serving in under a second. A Django process is about
125 MB before any request work; the lite profile keeps the total in that band by
running no Redis, no Celery worker and no beat. The lead's T-F5 live run records
the exact numbers for a release.

## See also

- [Configuration](configuration.md) — the `KAZI_LITE_PROFILE`,
  `KAZI_IN_PROCESS_SCHEDULER` and `REMINDER_SWEEP_SECONDS` rows.
- [Run Locally](run-locally.md) — the full Docker stack.
- [Deploy Safely](deploy-safely.md) — the production checklist.
