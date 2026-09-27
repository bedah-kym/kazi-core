# Plan: #134 Network on/off per command + host allowlist

Status: draft — awaiting human approval (protected path)

## Goal

A command that needs the network runs with Docker bridge **only** when the gate
allows it; a command whose hosts are all on `SHELL_EXEC_NETWORK_ALLOWLIST` runs
without a prompt. `locked` stays network-none; `open` is full network.

## Non-goals

- No true per-host egress firewall — the allowlist is a UX shortlist, not a
  packet filter (roadmap §4.5, §10). The container still runs non-root,
  read-only, on the default bridge (which is not the compose network).
- No remote-exec.

## Touches

- Protected: `Backend/orchestration/tool_executor.py` (risk gate un-gates an
  allowlisted `bounded` network command).
- `Backend/orchestration/shell/classifier.py`: `extract_hosts()` + a real
  `allowlisted` flag (all detected hosts listed).
- `Backend/orchestration/connectors/shell_connector.py`: send
  `network="bridge"` when the command needs the network.
- `Backend/orchestration/shell_exec/daemon.py`: accept `none`/`bridge`; reject
  anything else.
- `docs/configuration.md`, tests.

## Approach

1. `extract_hosts`: heuristic (URL host, IP/FQDN tokens, and a bare last arg for
   ping/dig/ssh/… commands); never treat the count in `ping -c 1` as a host.
2. `classify_command(..., allowlist=...)` sets `allowlisted` when hosts exist and
   all are listed. Tier is unchanged.
3. Risk gate: `requires_confirmation = tier != "safe"`, except
   `bounded` + allowlisted + needs_network -> no prompt.
4. Connector: `network = "bridge" if needs_network else "none"`.
5. Daemon: allow `none`/`bridge`; still no policy in the sidecar.

## Verification (executable)

- `test_shell_network`: host extraction + allowlist matching.
- Risk gate: allowlisted network -> not gated; non-allowlisted -> gated.
- Connector: network command -> bridge; safe -> none.
- Daemon: bridge accepted; unknown network rejected.
- Acceptance: with the ISP host allowlisted, `ping <isp>` runs without a prompt.
- Full suite + flake8 + bandit + `check_boundaries.py`.

## Rollback

Revert; the connector returns to always sending `network="none"`.

## Open questions for the human

- Confirm the allowlist auto-run: an allowlisted host skips the prompt and runs
  with bridge. This is the "ping my ISP with no prompt" acceptance item.
