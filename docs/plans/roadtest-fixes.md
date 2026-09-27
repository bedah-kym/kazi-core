# Plan: fix the four gaps from the 2026-09-27 road test

Status: draft — human approved the fixes in chat

## Goal

1. A shell script that needs the network can get it.
2. `ip`/`ifconfig`/route diagnostics run with a NIC (so the agent stops saying "no gateway exists").
3. A shell command is no longer blocked by the parameter-injection regex (the word "root" false-positived), and a policy block is terminal (no reword/retry loop).
4. The per-room profile resolves in the connector/gate so `open` can be used in chat.

## Non-goals

- No raw-shell/ungoverned reach; the sandbox stays the boundary.
- No change to non-shell tools' injection blocking.
- No remote-exec.

## Touches

- Protected `action_catalog.py`: add an optional `network` param to `run_command`.
- `shell/classifier.py`: `ip`/`ifconfig`/`route`/`ss`/`netstat`/`arp` count as network;
  `classify_command(..., requested_network=)`.
- Protected `tool_executor.py`: pass `network` into the classifier; resolve the profile
  from `preferences["shell_profile"]`; **exempt `run_command` from the param-injection
  block** (the sandbox bounds it; a regex firewall on shell is a non-goal).
- Protected `agent_loop.py`: stop a request after 3 consecutive policy-block results.
- `connectors/shell_connector.py`: per-room `resolve_profile`; send bridge when the
  command or the request needs it.
- `coordinator.py`: put the resolved profile into preferences for the loop.
- Tests: `test_shell_network`, `test_shell_connector`, `test_shell_escalation`.

## Verification (executable)

- `netcheck.sh`-style: a script run with `network: "bridge"` reaches the allowlist.
- `ip route` tiers `bounded` and runs with a network.
- An audit command containing "root"/"/etc" is not blocked.
- 3 blocked results stop the loop.
- Full suite + flake8 + bandit + `check_boundaries.py`.

## Rollback

Revert; the connector returns to classifier-only network and the regex block returns.
