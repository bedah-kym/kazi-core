# Credential Scoping — Shell & Delegate

Version: 1.0
Status: stable (documented only)

Defines what the shell-exec sidecar (#130) and any future delegated runtime
(#137) are allowed to touch. The **sandbox is the security boundary**; the
command classifier is UX plus a tripwire, never the boundary (v0.6 brief §3,
roadmap §4.3). This contract constrains the execution environment itself, so
every shell/delegate PR has a fixed target to check against.

## Rule 1 — Filesystem

- The root filesystem is read-only (`--read-only`).
- The **only** writable mount is the per-room workspace
  `<SHELL_EXEC_ROOT>/workspaces/<room_id>`, mounted rw at `/workspace`.
- Never mount a live host path that holds credentials or control: the repo,
  `.env`, `~/.ssh`, `~/.aws`, the Docker socket, or anything outside
  `<SHELL_EXEC_ROOT>`.
- The container runs as a non-root user with `--cap-drop=ALL`.

**Checkable:** a shell PR is wrong if it mounts anything other than the room
workspace, or omits `--read-only`, `--cap-drop=ALL`, or the non-root user.

## Rule 2 — Network egress

- Default is `--network=none`. Network is enabled per command **only** through
  the profile/escalation path (roadmap §4.5).
- `SHELL_EXEC_NETWORK_ALLOWLIST` is a UX shortlist of hosts that skip the
  inline prompt — it is not a packet filter.
- By policy the environment must never reach: Kazi Postgres / Redis / broker,
  any credential store or secret manager, production hosts, or the Docker
  socket.

**Checkable:** every networking command runs with `--network=none` unless a
human or profile path enabled it; no Kazi DSN or credential-store host ever
appears in the allowlist.

## Rule 3 — API keys

- The sidecar holds exactly **one** secret: the shared `SHELL_EXEC_TOKEN`. It
  holds no Kazi DB / Redis / provider key and no user admin key.
- Any key an approved command needs is scoped, ephemeral, injected for that
  call only, and revoked afterwards.
- The sandbox never sees Kazi's `.env`, Django settings, or any connector
  credential.

**Checkable:** the sidecar's only credential read is `SHELL_EXEC_TOKEN`; no code
path passes a Kazi or user key into the workspace or a command's environment.

## Rule 4 — Revocation & rotation

- `SHELL_EXEC_TOKEN` is a **single revocable bearer token** shared Kazi →
  sidecar. It is not an identity, and it grants only `POST /exec` and
  `GET /health`.
- Rotation: regenerate the token, restart the sidecar, update Kazi's setting;
  the old value is invalid immediately (the sidecar compares against the
  current value on every request).
- Blast radius of a leak: the holder can run a command in the room workspace
  the orchestration layer selects. Under `standard`/`locked` that command runs
  in the Docker sandbox (non-root, read-only, `--cap-drop=ALL`, network off).
  Under `open` it runs **directly on the sidecar host** with the sidecar user's
  permissions and environment, so `open` must only be enabled on a disposable
  box — the sidecar serves `standard` by default (`SHELL_EXEC_PROFILES`).
  Revoke and rotate contains the blast.
- The `room_id` a request carries is chosen by Kazi's orchestration layer from
  authenticated context, never from model output, so room scoping is enforced
  upstream of the sidecar; the sidecar only sanitizes the id into a safe path.

**Checkable:** a leaked token cannot be exchanged for broader access, and
losing it never exposes Kazi credentials.

## How to use this in review

Point any shell or delegate PR at these four rules. Each rule names the
setting or flag that enforces it, so a reviewer can confirm the environment,
not the command text, is what keeps the action safe.
