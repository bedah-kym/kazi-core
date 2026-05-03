# AGENTS.md

Guidance for AI coding agents (Claude Code, Codex CLI, Cursor, Cline, Aider, Copilot, etc.) working in **Kazi Core** — the open-source agentic engine published as `bedah-kym/kazi-core`.

This file is the contract: read it before editing. If you change behavior described here, update this file in the same PR.

---

## 1. Project at a glance

- **What it is:** A self-hostable Django + Channels backend that runs an agent loop, plans multi-step workflows, executes tool calls through a pluggable connector registry, and persists conversation memory.
- **Language / runtime:** Python 3.11, Django 5.x (ASGI). Postgres + Redis required in production; SQLite works for unit tests.
- **Async model:** ASGI (Daphne / Uvicorn) for HTTP + WebSockets via Django Channels. Celery + Beat for background work. Optional Temporal for durable workflows.
- **Open vs in-house:** This repo (`kazi-core`) ships the **agent core**. The maintainer also runs an in-house SaaS called **Mathia OS** built on top of this core. Anything Mathia-specific is held back from this branch. If you find a path that is intentionally `.gitignore`d (e.g. `frontend/`, parts of `docs/`), assume it belongs to Mathia and is not yours to recreate.

## 2. Repo map (where to look first)

```
Backend/
  orchestration/           # Agent core — start here for most changes
    agent_loop.py          # ReAct loop (think -> act -> observe)
    workflow_planner.py    # Multi-step plan decomposition
    manager_verifier.py    # Deterministic plan verification + reordering
    tool_executor.py       # Tool dispatch + safety gates
    base_connector.py      # BaseConnector interface
    connector_registry.py  # Auto-discovery (directory scan + entry points)
    action_catalog.py      # Tool definitions + risk levels
    security_policy.py     # Prompt-injection detection, parameter sanitization
    action_receipts.py     # Audit trail for sensitive actions
    memory_state.py        # Entity tracking + preference learning
    llm_client.py          # LLM provider abstraction (Anthropic first, HF fallback)
    connectors/            # Built-in connectors — add yours here
  chatbot/                 # WebSocket consumers, room memory, encryption
  notifications/           # Unified in-app / email / WhatsApp routing
  workflows/               # Durable execution (Temporal activities)
  travel/                  # Travel search + booking connectors
  payments/                # Double-entry ledger, invoices, wallets
  users/                   # Auth, profiles, quotas, encryption keys
  tests/                   # Cross-cutting test scripts (see Section 6)
.github/
  workflows/               # CI: lint, bandit, Django tests, CodeQL, container release
  copilot-instructions.md  # Legacy Copilot-specific notes (this file supersedes)
docs/                      # Public docs (quickstart, architecture, connector guide)
```

When in doubt, read `Backend/orchestration/mcp_router.py` and `Backend/orchestration/agent_loop.py` first — they show how everything wires together.

## 3. Setup commands

**Docker (recommended):**
```bash
docker compose up --build -d db redis web celery_worker celery_beat
docker compose exec web python Backend/manage.py migrate
docker compose exec web python Backend/manage.py createsuperuser
```

**Local without Docker** (PowerShell shown — adjust for bash):
```powershell
python -m venv .venv
. .venv/Scripts/Activate.ps1
pip install -r requirements.txt
python Backend/manage.py migrate
python Backend/manage.py runserver
```

You need Postgres + Redis running and a `.env` in the repo root (one level above `Backend/`). Required keys: `DJANGO_SECRET_KEY`, `DATABASE_URL`, `REDIS_URL`, `CELERY_BROKER_URL`, `CELERY_RESULT_BACKEND`. Optional: `ANTHROPIC_API_KEY`, `HF_API_TOKEN`, plus per-connector keys (`OPENWEATHER_API_KEY`, `CALENDLY_CLIENT_*`, etc.).

**Never commit `.env`.** It is listed in `.gitignore`.

## 4. Code style

- **Python:** flake8 enforced in CI with `--max-line-length=127 --max-complexity=10`. Match existing style in the touched module rather than imposing your own.
- **Comments:** default to none. Only comment when the *why* is non-obvious (a hidden constraint, a workaround for a specific bug). Don't narrate what the code does.
- **Imports:** standard → third-party → local, blank line between groups.
- **Async:** orchestration code is async-first. Use `asgiref.sync.sync_to_async` when calling Django ORM from async paths. Never block the event loop.
- **Type hints:** preferred for new public functions, optional for tests and one-offs.
- **No new heavy dependencies** without flagging it in the PR description and justifying the size cost.

## 5. Common change patterns

### Adding a connector (most frequent contribution)

1. Create `Backend/orchestration/connectors/<your_connector>.py` subclassing `BaseConnector`.
2. Implement `name`, `version`, `actions`, `get_action_catalog_entries()`, and `async def execute(self, parameters, context)`.
3. Return `{"status": "success" | "error", "message": "...", "data": {...}}` shaped dicts.
4. Auto-discovery picks it up on restart — no manual registration in most cases. (If you're touching `mcp_router.py`'s legacy `connectors` map, register there too.)
5. Add tests — at minimum a happy-path and an error path with the external service mocked.
6. Document the action(s) in `docs/connector-api-reference.md` if the API surface is new.

Full guide: [`docs/writing-a-connector.md`](docs/writing-a-connector.md).

### Touching the LLM client

Keep `generate_text` and `stream_text` semantics stable — `mcp_router.py`, `agent_loop.py`, and the chatbot consumers all depend on them. Use `get_llm_client()` to reuse the singleton; don't instantiate provider clients directly. Use `extract_json()` when expecting structured output.

### Touching security boundaries

`security_policy.py`, `tool_executor.py` (risk gates), and `action_receipts.py` are high-risk surfaces. Changes there need:
- A test that demonstrates the new behavior.
- A test that demonstrates the *old* attack/case is still blocked.
- A note in the PR description explicitly calling out the security implication.

## 6. Testing

**Run the full suite:**
```bash
python Backend/manage.py check
python Backend/manage.py test
```

**Lint + security:**
```bash
flake8 Backend
bandit -r Backend --skip B101
```

**Test conventions** (see `Backend/tests/README.md` for the full version):
- Deterministic, mocked, no real API calls. Use `example@example.com`, `fake-token`, etc.
- Real credentials, customer data, or machine-specific paths must not land in tracked tests.
- Personal verification scripts go in `Backend/tests/local_*.py` or `manual_*.py` (both are git-ignored).
- Public agentic coverage lives in `Backend/tests/test_agentic.py` and `test_agentic_scenarios.py` — extend those before creating new ad-hoc files.

**Known limitations:**
- CI runs against SQLite. Some Postgres JSON SQL functions don't have SQLite equivalents — tests that rely on them should be marked or moved.
- `Backend/tests/` is a loose folder, not a Django app. If you add tests there and `manage.py test` doesn't pick them up, that's why; either co-locate tests with the relevant app (`Backend/<app>/tests.py`) or use a test runner configured to walk that directory.

## 7. Commits and pull requests

**Commit format:** `type(scope): short summary`

Examples:
- `fix(notifications): sync unread count after dismiss`
- `feat(orchestration): add deterministic plan reorder for parallel tools`
- `test(connectors): cover Calendly OAuth refresh path`
- `docs(readme): clarify local setup`

**PR rules:**
- One focused change per PR. Don't bundle unrelated cleanup.
- Update tests with the change. Update docs (`README.md`, this file, `docs/*`) when behavior or contracts shift.
- Use the PR template. Fill in the validation section with actual command output, not just check-boxes.
- Maintainers may ask for scope reduction on large PRs — split rather than push back.

## 8. Security and operational guardrails

- **Secrets** live in `.env` (dev) or process env (prod). Never inline a key, never commit one. The repo's git-ignore covers `.env`, `*.key`, `*.pem`.
- **Production requires `REDIS_URL`** for Channels and Celery. Upstash (`rediss://`) is supported — `Backend/Backend/settings.py` adjusts SSL handling.
- **Celery tunables** (timeouts, prefetch multipliers in `settings.py`) affect throughput and concurrency. Treat changes there as performance changes, not bug fixes.
- **Channel layer**: if Redis is down, real-time features degrade but HTTP views still serve.
- **Vulnerabilities**: do not open public issues. Follow `SECURITY.md`.

## 9. What NOT to do (common agent failure modes)

- **Don't recreate or commit content under `frontend/` or `docs/`** without first checking `.gitignore` and confirming with a maintainer. These paths intentionally hold private Mathia-OS content that is not part of this OSS repo.
- **Don't bypass safety checks** (`--no-verify`, `bandit --skip` beyond `B101`) to make CI green. Fix the root cause.
- **Don't add backwards-compatibility shims** for code you just changed. If a caller is internal, update the caller.
- **Don't hand-roll JSON parsing of LLM output** — use `extract_json()` from `llm_client.py`.
- **Don't introduce a new connector by editing many existing files.** A new connector should be one new file plus its tests.
- **Don't write multi-paragraph docstrings or planning markdown** unless asked. Conversation context and PR descriptions are the right place for that, not committed files.

## 10. When this guide is wrong

If reality and this file disagree, reality wins — but file the disagreement. Either:
- Open a PR that updates `AGENTS.md` together with the code, or
- Note the discrepancy in your PR description so a maintainer can correct the guide.

The point of this file is that the next agent reading it inherits the lessons of the previous one. Keep it current.
