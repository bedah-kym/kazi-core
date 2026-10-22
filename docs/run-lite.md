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

Put this in `.env` at the repo root (never commit it). These are server
values: the host and origin are the public HTTPS name the reverse proxy serves.

```dotenv
DJANGO_DEBUG=False
DJANGO_SECRET_KEY=replace-with-a-long-random-value
DJANGO_ALLOWED_HOSTS=kazi.example.com
DJANGO_CSRF_TRUSTED_ORIGINS=https://kazi.example.com
KAZI_LITE_PROFILE=1

# Empty disables run_command. Set a long random value to enable the sidecar.
SHELL_EXEC_TOKEN=replace-with-a-shared-token

# Pick one LLM provider, or leave blank to boot without chat answers.
ANTHROPIC_API_KEY=
```

The lite profile starts the in-process scheduler automatically. Do not also
start `celery beat`; it refuses to start with this setting on. The web process
must have exactly one worker: in-memory Channels cannot fan a room out across
processes. The ASGI app refuses to load when a platform worker variable
(`WEB_CONCURRENCY`, `GUNICORN_WORKERS`, `UVICORN_WORKERS` or `KAZI_WEB_WORKERS`)
says more than one. `uvicorn --workers` does not export a count, so the
one-worker rule above is the contract for that launcher.

## Prepare the database and static files

```bash
python Backend/manage.py migrate
python Backend/manage.py createsuperuser
python Backend/manage.py collectstatic --noinput
```

SQLite is the default and needs no service. If you set `DATABASE_URL`, the lite
profile uses it unchanged.

With `DJANGO_DEBUG=False`, static files are served only from the collected
folder. Skip `collectstatic` and the chat page loads without its styles and
scripts, so messages cannot be sent. Run it again after each update.

## Start the sidecar

The sidecar does not import Django. Run it from `Backend/`:

```bash
cd Backend
python -m orchestration.shell_exec
```

`python manage.py run_shell_exec` starts the same server for a Django operator
who already has that process. Both read the same `SHELL_EXEC_*` environment and
`.env` file.

## Start the web process behind TLS

With `DJANGO_DEBUG=False` the settings turn on `SECURE_SSL_REDIRECT`, secure
session/CSRF cookies and HSTS. A plain-HTTP request therefore answers `301` to
HTTPS. That is deliberate: the lite profile is served **behind a TLS-terminating
reverse proxy** that sets `X-Forwarded-Proto: https`. Django trusts that header
through `SECURE_PROXY_SSL_HEADER`, so it sees the request as secure and the
redirect is not triggered.

Start the web process on loopback (in a second terminal, from `Backend/`):

```bash
uvicorn Backend.asgi:application --host 127.0.0.1 --port 8000 --workers 1
```

Use uvicorn, not Daphne: the in-process scheduler starts from the ASGI lifespan,
which Daphne does not run.

Point the proxy at it. A minimal Caddy config (Caddy obtains the certificate and
sets `X-Forwarded-Proto` for you):

```caddyfile
kazi.example.com {
    reverse_proxy 127.0.0.1:8000
}
```

With nginx, pass the header explicitly (plus the WebSocket upgrade headers).
`proxy_http_version 1.1` is required: nginx talks HTTP/1.0 upstream by default,
and a WebSocket upgrade needs HTTP/1.1.

```nginx
location / {
    proxy_pass http://127.0.0.1:8000;
    proxy_http_version 1.1;
    proxy_set_header Host $host;
    proxy_set_header X-Forwarded-Proto $scheme;
    proxy_set_header Upgrade $http_upgrade;
    proxy_set_header Connection "upgrade";
}
```

Browse `https://kazi.example.com`. If you open `http://` directly you will see
the `301`; that is the redirect working, not a failed start.

### Try it on one machine (plain HTTP)

To try the profile locally without a proxy or certificate, add
`KAZI_INSECURE_LOCALHOST=1` and keep the allowed host loopback-only:

```dotenv
DJANGO_ALLOWED_HOSTS=localhost,127.0.0.1
DJANGO_CSRF_TRUSTED_ORIGINS=http://localhost:8000
KAZI_INSECURE_LOCALHOST=1
```

That turns off the HTTPS redirect, secure cookies and HSTS for this process.
It is refused when `DJANGO_ALLOWED_HOSTS` names anything that is not loopback,
so it cannot silently weaken a public deployment. Never set it on a server.
See [Configuration](configuration.md#lite-profile-chat-only-no-redis-or-celery).

## Reminders

Reminders are stored in the database and the in-process scheduler's reminder
sweep delivers them. In the lite profile the sweep runs every 30 seconds
(`REMINDER_SWEEP_SECONDS`), so a reminder set for two minutes ahead is delivered
in chat shortly after it is due. The delivery claim works on LocMem and Redis
and fails closed: if the claim store cannot be written, the reminder is not
delivered twice.

## Moderation

Moderation needs the Redis profile: the message buffer and moderation batches
live in Redis. The lite profile therefore treats moderation explicitly:

- `MODERATION_ENABLED=1` with `KAZI_LITE_PROFILE=1` refuses to start and says
  moderation needs the Redis profile. Remove the setting or run the full
  profile.
- If moderation was only implied by `HF_API_TOKEN`, the lite profile disables
  it and prints one warning at start-up, so the operator knows the safety
  feature is not running here.

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
