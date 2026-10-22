# Plan: a small, capped Postgres connection pool per process
Status: approved by Bedan on 2026-10-21 (opt-in, timeout 10 s, health checks off when pooled).
Sizing revised the same day after the live test: 6 on this machine, not 3 (see step 5).

## Goal
Each Kazi process opens at most `DB_POOL_MAX_SIZE` Postgres connections and reuses them,
so the web server can no longer fill a small server-side limit and fail page loads.

## What happens now
- `settings.py` builds `DATABASES` with `conn_max_age=600, conn_health_checks=True`.
  Under ASGI every worker thread that touches the ORM keeps its own connection for 10
  minutes. Django's docs advise against persistent connections in async mode and point to
  the backend's pool instead (Django 5.1+, `OPTIONS["pool"]`, needs `psycopg_pool`).
- Measured 2026-10-21: after a few page loads `runserver` held **12** connections and the
  Celery worker 1. Supabase's session pooler allows 15 clients, so `/accounts/dashboard/`
  failed with `EMAXCONNSESSION ... max clients are limited to pool_size: 15`. A web restart
  dropped it to 2; it climbs back with use. The same limit broke page loads on 2026-10-10.
- `psycopg_pool` is not installed (`requirements.txt` has `psycopg[binary]>=3.3.5`).

## Non-goals
- No change for installs that do not set the new variable: no pool, `CONN_MAX_AGE` 600 as
  today (production behaviour is unchanged unless the operator opts in).
- No switch to Supabase's transaction pooler (port 6543); that is an `.env` choice and
  needs prepared statements and server-side cursors off.
- No pooling for SQLite (the hermetic lane and dev fallback).

## Touches
- Paths: `requirements.txt` (`psycopg[binary,pool]>=3.3.5`), `requirements.lock`
  (recompiled with the AGENTS.md `uv pip compile` command; adds `psycopg-pool`),
  `Backend/Backend/settings.py` (DATABASES block plus `_database_settings(url, environ)`, a
  pure function beside `_load_or_create_dev_secret_key`), `Backend/tests/test_db_pool.py`,
  `docs/configuration.md` (one row), `.env.example` (one commented line).
- Protected paths? **yes**: `.env.example` (matches `.env*`). Also the local `.env` on this
  machine, which is not committed: set `DB_POOL_MAX_SIZE=6` with your OK, after a backup.
- Contracts, migrations, approvals, payments, secrets? None. **New dependency**:
  `psycopg-pool` (pure Python, maintained by the psycopg authors; exact size to be measured from the wheel
  and stated in the PR). It is the
  module Django itself loads for `OPTIONS["pool"]`.

## Risk class of any new action
No new action. Configuration of database connections only.

## Approach
1. `settings._database_settings(url, environ)` returns the `default` dict. Postgres URL and
   `DB_POOL_MAX_SIZE` >= 1: `CONN_MAX_AGE=0` (Django refuses a pool otherwise) and
   `OPTIONS["pool"] = {min_size: 1, max_size: N, timeout: 10, max_idle: 300}`.
   Unset or 0: today's dict exactly. SQLite: never pooled.
2. A value that is not an integer, or is negative, raises `ImproperlyConfigured` at boot
   (fail closed: never run silently unpooled when a pool was asked for).
3. Health checks: off when pooled (each check is a round trip, about 195 ms to Supabase
   from this desk); `max_idle` 300 s retires idle connections before the server does.
4. `settings.py` calls the function; the in-memory SQLite fallback stays before it.
5. Sizing. The value is per process and must exceed that process's concurrent request
   threads: under ASGI each concurrent request runs its ORM work on its own thread, and a
   request that finds the pool empty waits 10 s and then fails with `PoolTimeout`.
   Live test 2026-10-21: web at 3 under 6 parallel page loads queued and hit `PoolTimeout`;
   web at 6 (others at 3) served 120 loads, all 200, peak 6 connections per PID, no
   `EMAXCONNSESSION`. The pool is a ceiling (`min_size` 1), so a process only opens what it
   uses.
   This machine: `DB_POOL_MAX_SIZE=6` in `.env` for every process. `.env` overrides process
   env, so it cannot be overridden per launcher. Realistic peak with the Temporal worker off:
   web up to 6, Celery worker 1 (solo pool), beat 1, sidecar about 1, plus a shell: about 10
   of 15. The Temporal worker stays off by default; with it on and busy the total can reach
   the 15-client limit. Chosen over per-launcher values because a process started without
   the variable runs unpooled silently (the original bug), while this setup fails loudly.
   Production: set it per service, largest for web.

## Verification (executable)
- Unit tests (hermetic, must fail on `main` because the behaviour does not exist there):
  Postgres URL + `DB_POOL_MAX_SIZE=3` gives `CONN_MAX_AGE` 0 and the pool options above;
  unset and `0` give today's dict (600, health checks on, no pool); SQLite URL with the
  variable set gives no pool; `abc` and `-1` raise `ImproperlyConfigured`.
- Postgres CI lane: one test that, with the pool enabled for its connection, runs a query
  and sees `connection.pool` set (skipped on SQLite).
- `python Backend/manage.py check`, `python Backend/manage.py test` (serial), flake8,
  bandit, `python scripts/check_boundaries.py`, and CI's stale-lock check.
- Live, this machine, full stack: web, Celery worker, beat, sidecar and the Temporal worker
  up; parallel loads of the dashboard, room 5 and the notification centre; one chat turn
  and one reminder that fires through Celery. Evidence: `netstat` count of `:5432`
  connections per PID stays at or under the pool size and no `EMAXCONNSESSION` in the
  logs. Done 2026-10-21 (results in step 5).
- Failure paths covered: invalid values, pool exhausted (a request waits up to 10 s then
  errors, it does not open an extra connection), SQLite unaffected.
- Injection corpus cases added: none (no untrusted input).

## Rollback
Remove `DB_POOL_MAX_SIZE` from `.env` and restart: today's behaviour. Reverting the PR
also removes the dependency.

## Decisions (Bedan, 2026-10-21)
1. Opt-in.
2. Timeout 10 s. Size 6 in `.env` on this machine (revised from 3 after the live test).
3. Health checks off when pooled.
4. The lead codes it.
