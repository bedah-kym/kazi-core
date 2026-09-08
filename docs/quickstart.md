# Quick Start

The shortest path from a fresh clone to a working conversation with
**Mathia**, Kazi's built-in AI assistant. You'll end with a self-hosted stack
running in Docker and a chat room where your messages are answered by your own
LLM key.

> **In a hurry?** The [README](https://github.com/bedah-kym/kazi-core#quick-start)
> has the four-command version. This page is the full walkthrough — every step
> explained, every pitfall called out, and a troubleshooting table at the end.

## The journey

```
  git clone ──► cp .env.example .env ──► docker compose up ──► createsuperuser ──► say hi
                 (add your LLM key)       (migrate + seed       (your login)       (Mathia replies)
                                          the Mathia bot)
```

**Time to first reply:** ~5 minutes on a warm Docker cache, ~15 on a cold one.
**What you need:** git, Docker (Desktop or engine + Compose), and one LLM API key.

---

## 0. Prerequisites

Check the boxes before you start:

- [ ] Docker is installed **and the daemon is running** (`docker info` succeeds).
- [ ] Docker Compose is available (`docker compose version`).
- [ ] You have an API key for at least one LLM provider (see the tabs below).

!!! warning "Windows users, read this first"
    The shell scripts in this repo are Linux-formatted. The repo ships a
    [`.gitattributes`](https://git-scm.com/docs/gitattributes) file that forces
    `LF` line endings on checkout, so a normal `git clone` is safe. If you see
    errors like `set: Illegal option -` or `no such file or directory` when
    containers start, your files have `CRLF` endings — re-clone rather than
    hand-editing scripts.

---

## 1. Clone and configure `.env`

```bash
git clone https://github.com/bedah-kym/kazi-core.git
cd kazi-core
cp .env.example .env
```

Open `.env` in your editor. Two things matter for a first run: **your LLM key**
and the **encryption key**.

### 1a. Pick an LLM provider

Set *one* of these (leave the others empty). Kazi falls back automatically if
you set more than one, in this order: Anthropic → DeepSeek → Hugging Face.

=== "Anthropic (recommended)"

    ```ini
    ANTHROPIC_API_KEY=sk-ant-...
    ```

    Best reasoning quality. Set `LLM_PLANNER_PROVIDER=anthropic` to route
    planning here (it's the default).

=== "DeepSeek"

    ```ini
    DEEPSEEK_API_KEY=sk-...
    ```

    Good balance of cost and quality. OpenAI-compatible with full tool-calling.

=== "Hugging Face (free tier)"

    ```ini
    HF_API_TOKEN=hf_...
    ```

    Free tier available. The default executor provider. Some models require
    router access on your HF token.

### 1b. Leave `ENCRYPTION_KEY` blank (for dev)

This key encrypts every room's message key in the database. In development it's
handled for you: the first time the app boots it generates a key and persists it
to `Backend/.encryption.key` (git-ignored), so it stays stable across restarts.

!!! danger "Production: set it yourself"
    In production (`DJANGO_DEBUG=0`) the app **refuses to start** if
    `ENCRYPTION_KEY` is blank. Generate a permanent key once and keep it:

    ```bash
    python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
    ```

    If `ENCRYPTION_KEY` changes after rooms exist, those rooms can no longer be
    decrypted and the chat socket rejects the connection (`403`) — a room that
    never loads. Treat it like a password: keep it out of git, and never rotate
    it without a re-encryption plan.

---

## 2. Boot the stack

```bash
docker compose up --build -d
```

This one command builds the image and starts five services. The `web` service's
entrypoint waits for Postgres, then **automatically** runs migrations, seeds the
Mathia bot user, and collects static files — no manual `migrate` step.

| Service | Role | Key detail |
|---|---|---|
| `db` | Postgres 15 | Workflow definitions, executions, receipts, chat data. |
| `redis` | Redis 7 | Cache + Channels backend + Celery broker. |
| `web` | Django ASGI server | Runs the agent loop and serves the chat UI. |
| `celery_worker` | Background tasks | Notifications, deferred-run watchdog. |
| `celery_beat` | Scheduler | Watchdog ticks, retries. |

### Confirm it's up

```bash
docker compose ps
```

Wait until `db` and `redis` report `healthy` and `web` shows `Up`. Then check the
web logs for the gunicorn boot line:

```bash
docker compose logs web | grep "Booting worker"
```

If you see `Booting worker with pid: …`, the server is accepting requests.
Full startup can take ~30s the first time (image build + migrations).

---

## 3. Create your login

```bash
docker compose exec web python Backend/manage.py createsuperuser
```

Follow the prompts (username, email, password).

!!! info "What just happened under the hood"
    Creating the user fires a signal that:

    1. Creates a `UserProfile` for you.
    2. Creates a **General Room**.
    3. Adds the **Mathia** bot as a participant (it already exists — step 2
       seeded it).
    4. Drops a welcome message from Mathia into the room.

    So you land in a room that's already wired for AI — no extra setup.

---

## 4. Log in and say hi

1. Open **<http://localhost:8000/accounts/login/>** and log in.
2. After login you land on the dashboard. Open your **General Room** (or visit
   **<http://localhost:8000/chatbot/redirect/>** to be taken straight to your
   first room).
3. Type a message and send it.

Because your General Room contains Mathia plus exactly one human, **every
message routes to the AI automatically** — no special prefix needed. Try one of
these to prove the loop works end-to-end:

- `hi — what can you help me with?`
- `what's the weather in Nairobi?`
- `remind me to call John tomorrow at 9am`

!!! tip "Prefix with `@mathia` anywhere else"
    In a room with multiple humans (or one you create without Mathia), only
    messages starting with `@mathia` trigger the AI. The General Room is the
    exception because it's a 1-human + Mathia room.

---

## Verify it actually works

- **Mathia replies** → your LLM key is good and the agent loop ran.
- **Message echoed, no reply** → see the "no AI reply" pitfall below.
- **Room never loads (spinner forever)** → see the `403` / encryption pitfall.

---

## Pitfalls and how to solve them

| Symptom | Likely cause | Fix |
|---|---|---|
| `ERROR: Couldn't find env file .env` | You skipped step 1. | `cp .env.example .env`, then `docker compose up -d` again. |
| No AI reply — your message just sits there | No LLM key set, or an invalid key. | Add a key to `.env`, then `docker compose restart web celery_worker`. |
| Room won't load; browser console shows a `403` on `/ws/chat/...` | `ENCRYPTION_KEY` (or `Backend/.encryption.key`) changed/lost after rooms were created. | Restore the original key, or wipe the DB volume (`docker compose down -v`) and start over. |
| `web` container exits immediately | Postgres/Redis health check still failing. | Wait ~30s and retry; check `docker compose logs db`. |
| `set: Illegal option -` in container logs | Scripts have `CRLF` line endings. | Re-clone (`.gitattributes` fixes this on checkout). |
| "Mathia user not found" warning in logs | You created a user *before* the web container finished seeding. | Run `docker compose exec web python Backend/manage.py seed_mathia`. |
| Changed `POSTGRES_*` but can't connect | `DATABASE_URL` still points at the old creds. | Update `DATABASE_URL` in `.env` to match `POSTGRES_USER`/`POSTGRES_PASSWORD`/`POSTGRES_DB`. |

!!! success "The two most common mistakes"
    Almost every "it doesn't work" report comes down to one of two things:
    **(1)** no LLM key in `.env`, or **(2)** a changed/lost `ENCRYPTION_KEY`.
    Check those first.

---

## Local development without Docker

Prefer the host? You'll need Postgres and Redis running locally, plus Python
3.11 or 3.12.

=== "Linux / macOS"

    ```bash
    python -m venv .venv && source .venv/bin/activate
    pip install -r requirements.lock
    # export the .env vars into your shell, then:
    python Backend/manage.py migrate
    python Backend/manage.py seed_mathia
    python Backend/manage.py runserver
    ```

=== "Windows (PowerShell)"

    ```powershell
    python -m venv .venv
    . .venv/Scripts/Activate.ps1
    pip install -r requirements.lock
    # set the .env vars in your session, then:
    python Backend/manage.py migrate
    python Backend/manage.py seed_mathia
    python Backend/manage.py runserver
    ```

In two more terminals:

```bash
celery -A Backend worker -l info
celery -A Backend beat -l info --scheduler django_celery_beat.schedulers:DatabaseScheduler
```

> The Docker path is recommended for first-time setup — it sidesteps the
> Postgres/Redis/credentials plumbing.

---

## Optional: Temporal for durable workflows

```bash
docker compose up -d temporal_worker
```

Workflows then survive restarts via Temporal. See
[Operate a Workflow](operate-a-workflow.md) for what that unlocks.

---

## Next steps

- [Add a connector](add-a-connector.md) — teach Kazi a new skill in one file
- [Add a workflow](add-a-workflow.md) — sequence tools with approval gates
- [Configuration](configuration.md) — every environment variable, grouped
- [Architecture](architecture.md) — how a message flows through the engine
