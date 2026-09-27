# Plan: #130 Shell-exec sidecar + Docker/local backends

Status: draft — awaiting human approval

## Goal

A deliberately dumb sidecar process (`manage.py run_shell_exec`) exposes
`POST /exec` and `GET /health`; it runs one command through Docker (standard /
locked) or a local subprocess (open), returns stdout/stderr/exit code, and holds
no Kazi credentials.

## Non-goals

- No policy, classifier, or autopilot in the sidecar — #131 owns all of that.
- No `run_command` connector here (that is #131).
- Only the `standard` Docker path is exercised in Phase 1; `open`/`locked`
  config lands in #132.
- No remote-exec backend, no true per-host egress firewall (roadmap §10, §11).
- No changes to the agent loop, tool executor, or connector registry.

## Touches

- New paths:
  - `Backend/orchestration/shell_exec/__init__.py`
  - `Backend/orchestration/shell_exec/backends.py` (`DockerBackend`, `LocalBackend`)
  - `Backend/orchestration/shell_exec/daemon.py` (aiohttp app)
  - `Backend/orchestration/management/commands/run_shell_exec.py`
  - Tests: `Backend/orchestration/test_shell_backends.py`,
    `Backend/orchestration/test_shell_daemon.py`
- Modified paths:
  - `Backend/Backend/settings.py` — `SHELL_EXEC_*`
  - `.env.example` — the same keys (protected: `.env*`)
  - `docs/configuration.md` — document the keys
- Protected paths? yes: `.env.example` (`.env*`)
- Contracts, migrations, approvals, payments, secrets? None. No migration, no
  contract change. The token is read from env; never committed, never logged.

## Risk class of any new action

outside-world command execution — but sandboxed by construction: non-root,
read-only rootfs, `--cap-drop=ALL`, `no-new-privileges`, network none by
default, memory/PID caps. `LocalBackend` (full trust) is only selected by the
`open` profile and is not the default.

## Approach

1. `backends.py`: `ExecResult`; `DockerBackend.execute()` builds argv
   `docker run --rm --user <uid> --read-only --cap-drop=ALL
   --security-opt no-new-privileges --network=<none|bridge> --memory=…
   --pids-limit=… -v <ws>:/workspace -w /workspace <image> sh -c <cmd>`, runs it
   with `asyncio.create_subprocess_exec`, enforces `timeout_s` (kill on expiry)
   and `output_bytes_max`. `LocalBackend.execute()` uses
   `create_subprocess_shell(cwd=<ws>)`. `get_backend(profile)` factory.
2. `daemon.py`: aiohttp app; constant-time token check on `X-Shell-Exec-Token`;
   `POST /exec` validates and delegates; `GET /health`. Caps enforced here too,
   independent of Kazi. No policy.
3. `run_shell_exec.py`: starts the app on `SHELL_EXEC_HOST`/`SHELL_EXEC_PORT`.
4. Settings + `.env.example` + `docs/configuration.md`.

## Verification (executable)

- `python Backend/manage.py test orchestration.test_shell_backends
  orchestration.test_shell_daemon` (Docker mocked; `LocalBackend` against
  `echo`/`pwd` in a temp workspace).
- Manual Docker acceptance (paste in PR): start daemon, `POST /exec`
  `{"command": "echo hi"}` → `{"stdout": "hi", "exit_code": 0}`; wrong token →
  401; a write outside `/workspace` and a network command both fail in the
  `standard` container.
- `flake8 …`, `bandit -r Backend --skip B101,B110`, `check_boundaries.py`.
- Failure paths: sidecar timeout, output truncation, Docker missing, non-zero
  exit, bad token.

## Rollback

Revert the commit. No schema, no persisted state; the sidecar is a separate
process that is simply not started.

## Open questions for the human

- `aiohttp` (3.14.3) is currently **transitive** (via `twilio`/`aiohttp-retry`),
  not in `requirements.txt`. Reuse it as the issue directs, or declare it and
  recompile the lock? `uv` is not installed on this machine, so declaring it
  would make the lock stale and fail CI. Recommend: reuse the locked version.
- Default container image — `alpine:3.20` (small, has `sh`), pulled on first
  run? Confirm the image name/registry policy.
