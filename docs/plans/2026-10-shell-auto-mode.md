# Plan: shell auto mode — ask at the boundary, not per command

Status: Phase 1 approved by Bedan (2026-10-03) and built; he applied the two
protected-file changes himself. Phases 2 and 3 are not approved or built.
Supersedes: `2026-10-agent-loop-shell-grants.md` (PR #238 as built)

## Goal

A shell task runs end-to-end with a prompt only when a command **crosses the
sandbox boundary**: reaching a host nobody approved, or doing something that
cannot be rolled back. Everything that stays inside the sandbox runs without
asking, tainted run or not.

## Why the current design asks 100 times

`run_command` output taints the run (#170), and `run_command` is itself
taint-gated (`risk_level: high`, `confirmation_policy: always` in the catalog).
So after the first shell command, `_bucket_tool_calls` pauses every later one —
`ls` included, on every profile, with autopilot armed. PR #238 never touches
that branch, and its grants/autopilot only affect `sudo` on the `open` profile.

## The rule (whole design)

`standard` profile (Docker, non-root, read-only rootfs, no secrets):

| Command | Untainted | Tainted |
|---|---|---|
| Runs with `network=none` | auto | **auto** |
| Network, every host approved (Phase 2: proxy-enforced) | auto | auto |
| Network, unapproved host | ask per **host** | ask per host |
| Destructive tripwire | Phase 3: auto after snapshot; until then ask | same |
| Root / `denied` | refuse | refuse |

Why tainted + `network=none` is safe to auto-run: the Rule of Two needs an
exfiltration leg. A container with no network and no secrets has none. The
connector already sends `network=none` unless the classifier or the model asks
for bridge, and asking for bridge is what triggers the prompt — so a classifier
miss fails the command, it does not leak.

`locked`: unchanged (no network, no workspace writes; local commands auto).

`open` (unsandboxed host): no boundary exists, so no auto mode. Keeps today's
behaviour, plus the human-armed window from #238 as the explicit, time-boxed,
receipted "I accept this box is disposable" switch. Destructive still asks.

## Non-goals

- No LLM in the consent path.
- No auto mode for non-shell tools: send / pay / publish keep their gates and
  the #170 taint escalation.
- No exact-command grants. Local commands no longer need one; network consent
  moves to hosts. `shell/grants.py` command fingerprints are removed.
- No chat-regex consent ("stop asking" must never execute anything).

## Touches

- Protected: `agent_loop.py` (`_bucket_tool_calls`, sub-agent batch),
  `tool_executor.py` (`_run_command_risk_info`). Phase 2: `docker-compose*.yml`, one
  migration.
- `shell/classifier.py`, `connectors/shell_connector.py`, `shell_exec/daemon.py`,
  `coordinator.py`, `shell/autopilot.py`, `workflows/ui_views.py`, tests, eval corpus.

## Risk class

Phase 1: reversible-in-workspace only (nothing new can reach the outside
world). Phase 2: outside-world, bounded by an enforced host allowlist.
Phase 3: reversible-in-workspace via snapshot.

## Approach

### Phase 1 — boundary-scoped taint (kills most prompts; no new infra)

1. `_run_command_risk_info` returns `egress: bool` = command will run with
   bridge (classifier `needs_network` or requested `network: "bridge"`).
2. `_bucket_tool_calls`: for shell tools on a Docker-backed profile, the taint
   escalation applies only when `egress` is true. `network=none` commands follow
   their tier. Non-shell tools unchanged.
3. Sub-agents inherit the parent's taint, taint themselves the same way, and
   use the same bucketing function; they still cannot pause. `delegate_task`
   output taints the parent run.
4. Consent replies: cancel is checked first. `autopilot` arms only as a
   whole-message exact match, only when the pending action is a non-destructive
   shell command, and only on the `open` profile; arming then confirms that
   command. "always allow" no longer exists.
5. Armed window (`open` only): capped at `SHELL_AUTOPILOT_MAX_MINUTES` (120),
   POST-only, room membership required, refused unless the arm receipt is
   written. Kill switch: a cancel or `autopilot off` in chat, the ops UI, or
   expiry. State is re-read every loop iteration.
6. Every shell receipt records its basis in `result.approval_basis`: `human`,
   `sandbox`, `allowlist`, `armed_window`, or `open_profile`.

### Phase 2 — enforced egress + host grants (network without per-command asks)

1. Sidecar runs a small allowlisting forward proxy (stdlib, no new dependency).
   Bridge containers join an internal network whose only route out is the proxy.
2. Approved hosts = `SHELL_EXEC_NETWORK_ALLOWLIST` + per-room host grants.
   Ship a default package-registry set (PyPI, npm, crates, Go proxy), read-only.
3. A blocked host comes back as a structured denial; the loop pauses with
   "allow `host`? once / this room for N days / no". One prompt per new host.
4. `ShellHostGrant(user, room, host, expires_at, revoked_at, created_from)` as
   a real table. Profile JSON is dropped: other writers clobber it and it has
   no audit trail.
5. With enforcement in place, a tainted run may reach approved hosts.

### Phase 3 — destructive inside the workspace

On `standard`, a tripwire match that snapshots successfully auto-runs and the
receipt carries the snapshot id; rollback stays one command. Snapshot failure
asks. `open` always asks.

## Verification (executable)

- Tainted run: `ls`, `make test` auto on `standard`; `curl <unapproved>` and
  `network: "bridge"` ask; same through `delegate_task`.
- Regression: tainted `send_email` still asks; `open` tainted still asks
  without an armed window; `locked` network still denied.
- Replies: "no, stop asking", "what does autopilot do?", "cancel, never ask
  again" never execute or arm.
- Injection case (unit level; the golden corpus only scores message-level
  detection): a tainted run emitting a network `run_command` is gated, also
  through `delegate_task`.
- Phase 2: container cannot reach an unlisted host by IP, DNS, or direct socket.
- Full suite, flake8, bandit, `check_boundaries.py`.

## Rollback

Phase 1 is a pure rule change: revert the commit and every tainted shell call
asks again. Phase 2: detach the proxy network and revert the migration; network
commands return to per-command prompts.

## Residual risk (accepted, stated)

A tainted run can write a poisoned file into the persistent workspace that a
later network command executes. Phase 1 bounds this with the per-command
network prompt; Phase 2 bounds it to approved hosts. The sandbox holds no
secrets, so the exposure is the workspace's own contents.

## Open questions for the human

1. Phase 2 needs a migration and a compose change — approve, or stop at Phase 1?
2. Phase 3: auto-run destructive-with-snapshot, or keep the ask?
3. `open` profile: armed window only (recommended), or no autonomy at all?
