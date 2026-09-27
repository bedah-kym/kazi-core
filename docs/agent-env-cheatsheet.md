# Agent Environment Cheatsheet (Windows + Docker Desktop)

Machine-specific gotchas learned the hard way. For agents working in this
repo on this machine. Code conventions live in `AGENTS.md`; this file is
**environment + tooling only**.

## TL;DR daily loop

```powershell
# Test something (hermetic, SQLite, no services needed) — see recipe below
# Lint:    .venv\Scripts\python.exe -m flake8 Backend --count --statistics --select=E,W,F --ignore=E501,E402,W503 --max-line-length=127 --max-complexity=10
# Sec:     .venv\Scripts\python.exe -m bandit -r Backend --skip B101,B110
# Bounds:  .venv\Scripts\python.exe scripts/check_boundaries.py
# Site:    http://127.0.0.1:8000/   (admin / admin1234)
# Logs:    docker compose logs web --since 15m
```

## Python

- **ALWAYS use the venv**: `.venv\Scripts\python.exe` (Python 3.12 +
  `requirements.lock`). System `python` is 3.14 with Django 6 and missing
  deps — everything breaks subtly. Never run tests with it.
- Dev tools already in the venv: `flake8`, `bandit`, `coverage`.
- Windows test runner is serial only (`--parallel > 1` crashes on pickle).

## .env — the highest-risk file on this machine

- `settings.py` calls `load_dotenv(override=True)` → **.env values override
  process env vars**. You CANNOT override `DATABASE_URL` by setting an env
  var in PowerShell. To run against SQLite you must move `.env` aside.
- Editing `.env` with an Edit tool has clobbered user values before.
  **Back up first** (`Copy-Item .env .env.local-backup`), verify with a
  read-only grep after every edit.
- Never print secret VALUES. Check presence only:
  `Get-Content -Encoding UTF8 .env | Select-String '^KEY=' | ForEach-Object { ($_.Line -split '=',2)[0] + ' len=' + ($_.Line -split '=',2)[1].Trim().Length }`
- `.env` contains em-dashes; read/write as UTF-8. PowerShell 5.1 reads it
  as mojibake unless `-Encoding UTF8`.
- Key facts: DeepSeek key is LIVE (LLM fallback works). Mailgun keys live
  on the **EU region** (`MAILGUN_API_BASE=https://api.eu.mailgun.net`).
  Gmail OAuth unconfigured. Telegram/WhatsApp/payments/weather keys absent.

## Hermetic test recipe (the ONLY reliable way to run tests here)

```powershell
$moved = $false; if (Test-Path .env) { Move-Item .env .env.kazi-tmp -Force; $moved = $true }
try {
  $env:DJANGO_DEBUG = "true"; $env:DJANGO_ALLOWED_HOSTS = "localhost,127.0.0.1"
  $env:DJANGO_SECRET_KEY = "ci-test-key-not-for-prod"
  $env:ENCRYPTION_KEY = "I2m1pOTNatH-LkRdlnWXZXqZ9-8oQRm-JCwpwshx6dc="
  $env:PYTHONIOENCODING = "utf-8"   # cp1252 crashes on the dev-key warning emoji
  & ".venv\Scripts\python.exe" Backend/manage.py test --noinput
  Write-Output "EXIT: $LASTEXITCODE"
} finally {
  if ($moved -and (Test-Path .env.kazi-tmp)) { Move-Item .env.kazi-tmp .env -Force }
}
```

- Without `ENCRYPTION_KEY`, any test creating a user 500s (signals create
  encrypted Chatrooms).
- Full suite ≈ 600+ tests, ~3–5 min. Targeted:
  `... test orchestration.test_reminder_delivery_policy --noinput`.
- Expected noise in output: `intasend-python not installed`,
  `telegram_bot skipped`, `django_ratelimit.W001`, payments callback
  ERROR lines (negative-path tests). Ignore.
- Cache pollution trap: user PKs restart at 1 each test rollback but
  LocMem cache persists → throttle/dedupe history leaks between tests.
  New tests that touch cache-keyed state should `cache.clear()` in setUp
  and `addCleanup(cache.clear)`.
- ORM-in-async trap: `sync_to_async` runs on a separate thread → TestCase
  transactions are invisible to it. Use `TransactionTestCase` when a test
  exercises ORM through `sync_to_async`.

## Docker (Docker Desktop, Windows)

- **Daemon**: Hyper-V is intentionally off on this machine (display driver
  conflict) — if Docker is down, tell the human; don't try to fix it.
  Check: `docker info`.
- Stack: `docker compose up --build -d db redis web celery_worker celery_beat`
  (Temporal not included; `TEMPORAL_DISABLED=1`).
- **Code is volume-mounted** (`.` → `/app`) → editing host files changes
  the live site; `docker compose restart web celery_worker` reloads.
  env_file changes need `docker compose up -d` (recreate).
- The live site runs the **working tree**, not the deployed branch —
  `git checkout` + restart changes what production serves. Keep the
  working tree on the branch you want live (`fix/stress-test-findings`).
- Web takes 20–30s after restart (gunicorn boot). Poll:
  `Invoke-WebRequest http://127.0.0.1:8000/ -UseBasicParsing` until 200.
- Logs: `docker compose logs web --since 15m` — note gunicorn's
  `Control server error: /nonexistent` is cosmetic noise, ignore.
- DB/shell access:
  `docker compose exec -T web python Backend/manage.py shell -c "..."` —
  `-T` is required for non-interactive use; PowerShell will mangle complex
  quoting, keep the `-c` string simple.
- Celery tasks: ETA tasks for reminders (`chatbot.tasks.send_reminder`),
  beat sweeps (`check-due-reminders` hourly, `sweep-stuck-approvals` 5min).
  Watch the worker log for task IDs when debugging delivery.
- Stress script (gitignored): `Backend/tests/local_stress_workflow.py`
  with a token from `/tmp/kazi_stress_token` inside the web container.
  Inside-container throughput is the real number; Docker Desktop's Windows
  port-forwarding caps host-side requests at ~2–3/s.

## PowerShell 5.1 gotchas

- No `&&`, no `-Parallel`, no `Select-String` on native stderr without
  noise (stderr shows as "NativeCommandError" — usually harmless).
- `$env:TEMP\opencode` is the scratch dir, BUT files there can disappear
  between tool calls — re-verify `Test-Path` before use; write and consume
  in the same command.
- `gh` + `--jq` with pipes/parens gets mangled. Prefer:
  `gh api ... | ConvertFrom-Json` then `foreach` over the objects.
- Long-running/background processes get killed by the shell tool. Use
  `Start-Process -WindowStyle Hidden` with `-RedirectStandardOutput` for
  anything that must outlive the command, or keep server runs in Docker.
- `python ... | Out-File -Encoding utf8` then `Select-String` is the
  reliable pattern for capturing long test output.

## GitHub / PR workflow

- Branch per issue from `main`; conventional commits
  (`fix(scope): summary`); PR template wants real validation output.
- Branch protection: CI + CodeRabbit review required; **auto-merge is
  disabled** — the human merges manually. After the human merges, fetch
  main and re-merge into any sibling PR branches (they go BLOCKED).
- CodeRabbit: check with `gh pr view <n> --json reviews`. Its comments are
  usually right; fix with a follow-up commit. Stale comments referencing
  fixed code: reply on the comment explaining the fixing commit.
- Protected paths: `.claude/protected_paths.txt` — get human OK before
  editing those files, even when the change looks trivial.

## Credentials / users (dev)

- Site login: `admin` / `admin1234` (admin's email is
  bedankimani860@gmail.com — reminder offline delivery goes there).
- `mathia` is the bot user, not for login.
- Mailgun sends for real now (EU endpoint). "Mock" in a connector result
  means the key is missing.
