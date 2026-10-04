# Credential Scoping — Shell & Delegate

Version: 1.1
Status: stable (documented only)

Defines what the shell-exec sidecar (#130) and any future delegated runtime
(#137) are allowed to touch. The **sandbox is the security boundary**; the
command classifier is UX plus a tripwire, never the boundary (v0.6 brief §3,
roadmap §4.3). This contract constrains the execution environment itself, so
every shell/delegate PR has a fixed target to check against.

Changes in 1.1: Rule 2 describes the enforced egress proxy
(`SHELL_EGRESS_PROXY`); Rule 1 states what the proxy container may mount.

## Rule 1 — Filesystem

- The root filesystem is read-only (`--read-only`).
- The **only** writable mount is the per-room workspace
  `<SHELL_EXEC_ROOT>/workspaces/<room_id>`, mounted rw at `/workspace`.
- Never mount a live host path that holds credentials or control: the repo,
  `.env`, `~/.ssh`, `~/.aws`, the Docker socket, or anything outside
  `<SHELL_EXEC_ROOT>`.
- The container runs as a non-root user with `--cap-drop=ALL`.
- The egress proxy container (Rule 2) is infrastructure, not the sandbox. It is
  also read-only, non-root and `--cap-drop=ALL`, and mounts only that command's
  generated files under `<SHELL_EXEC_ROOT>/egress/<exec_id>/`: its config and
  host list read-only, and a log directory. That directory is never mounted
  into an exec container.

**Checkable:** a shell PR is wrong if an exec container mounts anything other
than the room workspace, or omits `--read-only`, `--cap-drop=ALL`, or the
non-root user.

## Rule 2 — Network egress

- Default is `--network=none`. A command gets network only when the classifier
  or the model asks for it, and then in one of two ways:
  - **Proxied** (`SHELL_EGRESS_PROXY=true`, `standard` profile, HTTPS-capable
    command): the container joins a per-command internal network whose only
    exit is a stock Squid that tunnels HTTPS to approved hosts and nothing
    else. Here the allowlist **is** enforced, by the network and the proxy.
  - **Open bridge** (everything else that needs network): only after a human
    approved that command, or when the operator's
    `SHELL_EXEC_NETWORK_ALLOWLIST` names every host in it. On the bridge that
    list is a UX shortlist that skips the inline prompt — nothing enforces it.
- An approved host is still a place data can be sent. On the sandboxed
  profiles a prompt is therefore never skipped for a network command on a run
  tainted by untrusted content. On the proxied path, plain publish / push /
  upload commands also always ask; that check is a tripwire for the plain
  forms, not a boundary.
- No credential for an approved host may exist inside the sandbox.
- By policy the environment must never reach: Kazi Postgres / Redis / broker,
  any credential store or secret manager, production hosts, or the Docker
  socket. The proxied network is created in isolated gateway mode so services
  the Docker host binds on `0.0.0.0` are unreachable, and the proxy refuses
  private, loopback, link-local and metadata addresses.

**Checkable:** every networking command runs with `--network=none`, or on a
per-command `--internal` network with an isolated gateway, unless a human
approved that command for the open bridge or the operator allowlisted its
hosts; no Kazi DSN or credential-store host ever appears in an allowlist;
`scripts/verify_shell_egress.py` passes on the sidecar host.

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
