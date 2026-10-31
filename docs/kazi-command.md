# The `kazi` command

`kazi` starts, stops, checks and explains a local checkout. It is a thin
launcher: it never imports Django and never shells out. It asks Django for the
effective settings (`manage.py kazi_status --json`) so the processes it runs
match what the app will actually do.

Run it from the repo root, either through the shim or directly:

```bash
./kazi up          # Windows: kazi up
./kazi status
./kazi down
```

The shim uses `.venv/bin/python` (POSIX) or `.venv\Scripts\python.exe`
(Windows) when present, otherwise `python3` / `python`.

## What `up` does

1. Takes the supervisor lock. If Kazi is already running, it prints status
   instead of starting a second stack.
2. Runs `doctor`. A **FAIL** stops the start; a **WARN** does not.
3. Prints the database **host** (never a password), then applies migrations.
   A failed migration stops the start.
4. Runs `sync_beat_schedule` when beat will run, and `collectstatic` when
   `DEBUG=False`.
5. Starts the supervisor, waits for web to answer HTTP and (when present) for
   the worker to answer a broker ping, then prints the URL.

`kazi run` does the same work in the foreground. It is the entry point a
systemd unit uses.

## Commands

| Command | What it does |
|---|---|
| `kazi up` | Start the stack in the background and print the URL. |
| `kazi run` | Run the supervisor in the foreground (systemd, or watching it). |
| `kazi down` | Ask the supervisor to stop its children gracefully. |
| `kazi status` | Show the supervisor, its children, restarts, DB host and drift. |
| `kazi logs [name] [-f]` | Print or follow `web`, `worker`, `beat`, `sidecar`, `supervisor` or `autostart`. |
| `kazi doctor` | Explain startup problems before they bite. |
| `kazi autostart enable` | Create the per-user logon entry. |
| `kazi autostart disable` | Remove exactly what `enable` created. |
| `kazi autostart status` | Say whether the entry is present. |

Logs live under `.kazi/logs/`, rotated by size (10 MB, five files per
process). They can contain user content and task arguments; treat them as
sensitive.

## Which processes run

The plan comes from the effective settings:

- **web** (uvicorn, one worker) and the **shell sidecar** always run.
- The **Celery worker** runs unless `KAZI_LITE_PROFILE` is on.
- **Celery beat** runs only when `IN_PROCESS_SCHEDULER` is off.

Every child is started with list-form arguments. There is no `shell=True` and
no `cmd /c`, which is what made a quoting mistake report success while
starting nothing during the 2026-10-25 start-up.

## Restarts, stop order and identity

The supervisor holds an exclusive OS lock, so a second `up` (manual or from
autostart) prints status instead of starting a second stack. A crashed child
restarts with backoff; five crashes in ten minutes makes the supervisor give
up and record why. The stop-requested flag wins over exit codes, so `down`
never restarts anything. On Windows a requested Ctrl+Break exit
(`0xC000013A`) is not counted as a crash.

Stop order is **web** (graceful, with `TURN_SHUTDOWN_GRACE_SECONDS`), then
**worker** (Celery warm shutdown through the broker), then **beat**, then the
**sidecar** last. Leases and the reaper make a hard kill safe for Turns only.

On Windows the supervisor is started with `CREATE_NO_WINDOW` and puts its
children in a job object with `KILL_ON_JOB_CLOSE`: killing the supervisor
takes its children with it. Each child gets `CREATE_NEW_PROCESS_GROUP`, so a
graceful Ctrl+Break can reach it. On POSIX each child gets its own session.

The state file records a pid **and** its creation time (Windows
`GetProcessTimes`, Linux `/proc/<pid>/stat`). Before a signal, kill or an
"already up" decision, both must match, so a reused pid is never touched.

## The control channel

The supervisor listens on `127.0.0.1` on a random port and writes the port and
a random token to `.kazi/run/control.json` with owner-only permissions where
the OS allows. `down` and `status` use that channel; `down` never signals the
children itself. The token is compared in constant time. A wrong token is
refused. The control file and logs hold no `.env` values.

## Doctor

Each check prints OK, WARN or FAIL with one plain sentence. It covers:

- Python 3.11–3.12 inside a virtual environment, the lock file installed,
  `.env` present and required settings set;
- process-environment variables that `.env` overrides (names only);
- Temporal enabled but unreachable (WARN);
- Redis (full profile) and database reachability;
- pending migrations and beat drift (WARN; `up` applies/syncs them);
- `SHELL_EXEC_TOKEN` set;
- the shell profile;
- web and sidecar ports free;
- `DEBUG=False` without collected static files;
- the worker-count variables the ASGI guard reads;
- pool size × process count against a typical server client limit (WARN).

The shell profile check is intentionally blunt. On the **open** profile:

> The open shell profile is unsandboxed. With great power comes great
> responsibility.

## Autostart

Autostart is per user. There are no admin rights and no Task Scheduler job
object.

### Windows

`kazi autostart enable` writes `kazi.cmd` to the user's Startup folder
(`shell:startup`). It runs `kazi up --autostart`, which waits and retries
service checks for up to three minutes because Redis and the database may not
be ready at logon. `kazi autostart disable` removes exactly that file.

### Linux

`kazi autostart enable` writes a systemd **user** unit at
`~/.config/systemd/user/kazi.service`. It runs the supervisor in the
foreground (`kazi run`) with `Type=simple` and `Restart=on-failure`, so systemd
does the supervising and its cgroup is not killed. Enable it with:

```bash
systemctl --user daemon-reload
systemctl --user enable --now kazi
```

Without `loginctl enable-linger` the unit runs only while you are logged in.

### macOS

macOS is documented only. A launchd plist can run `kazi run` at login:

```xml
<key>ProgramArguments</key>
<array>
  <string>/path/to/repo/scripts/kazi.py</string>
  <string>run</string>
</array>
<key>RunAtLoad</key><true/>
<key>KeepAlive</key><true/>
```

## What it does not do

- It is not a production process manager. Servers use systemd, containers or
  the platform.
- It does not manage Temporal, Postgres or Redis. Doctor reports them; it
  never starts them.
- It never edits `.env`. `doctor` names what to change.
- It does not cover unattended boot before logon; Windows auto-logon is your
  own choice.

## Troubleshooting

| Symptom | What to do |
|---|---|
| `up` stops at doctor | Fix the FAIL line; WARNs do not block. |
| `A Kazi supervisor is already running` | Run `kazi status`; use `kazi down` before a second `up`. |
| Port already in use | Stop the other process, or set `KAZI_WEB_PORT` / `SHELL_EXEC_PORT`. |
| Worker never answers | Check `kazi logs worker` and the broker settings. |
| Autostart seems to do nothing | Check `kazi autostart status` and `.kazi/logs/autostart.log`. |
| Static files missing with `DEBUG=False` | Run `python Backend/manage.py collectstatic --noinput`, or let `up` do it. |
