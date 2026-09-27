# Plan: #131 run_command connector + dynamic risk gate

Status: draft — awaiting human approval

## Goal

`run_command` is a registered action whose risk is computed from the command
itself: `get_tool_risk_info(tool_name, preferences, tool_input)` dispatches it
to the shell classifier and returns a tier, and a thin connector forwards the
command to the #130 sidecar.

## Non-goals

- No inline confirmation, no tier-aware routing in the loop (#133).
- No profiles config / `open`/`locked` settings UX (#132).
- No network toggle or allowlist UX (#134).
- No safe-tier auto-execution in Phase 1 — **everything is gated** (roadmap §8).
- The classifier is not a security boundary; no regex firewall.

## Touches

- New paths:
  - `Backend/orchestration/shell/__init__.py`
  - `Backend/orchestration/shell/classifier.py`
  - `Backend/orchestration/connectors/shell_connector.py`
  - Tests: `Backend/orchestration/test_shell_classifier.py`,
    `Backend/orchestration/test_shell_connector.py`
- Modified protected paths:
  - `Backend/orchestration/action_catalog.py` — register `run_command`
  - `Backend/orchestration/tool_executor.py` — `get_tool_risk_info` gains
    `tool_input` and dispatches `run_command`
  - `Backend/orchestration/agent_loop.py` — pass `tc["input"]` at both risk-gate
    call sites (lines ~774 and ~1266)
- Protected paths? yes: `action_catalog.py`, `tool_executor.py`, `agent_loop.py`
- Contracts, migrations, approvals, payments, secrets? None. No migration, no
  contract change. The sidecar token stays in the connector's env, never in a
  result.

## Risk class of any new action

outside-world command execution; the *change* itself is a read-only risk
computation. In Phase 1 every `run_command` pauses for the existing durable
approval (`requires_confirmation=True` for all tiers), so no shell runs without
a human.

## Approach

1. `classifier.classify_command(command, profile="standard", allowlist=None)`
   returns `{"tier", "reason", "needs_root", "needs_network", "destructive"}`.
   Three honest checks (root / network-binaries / destructive tripwire); short
   lists, documented as UX not boundary. Tier mapping per profile:
   standard: root→denied, network→bounded, destructive→destructive, else→safe;
   locked: network→denied, non-read→denied; open: root→bounded.
2. `get_tool_risk_info(..., tool_input=None)`: when the resolved action is
   `run_command`, call the classifier and return `requires_confirmation=True`
   (Phase 1), plus `shell_tier` and `shell_reason`. Non-shell behaviour is
   byte-for-byte unchanged.
3. `ShellConnector` (`run_command`): posts to the sidecar with `httpx` and the
   shared token; normalizes connect/timeout failures to an error status. No
   policy. Refuses a `denied` tier defensively even if invoked directly.
4. Register the catalog entry and update the two agent-loop call sites.

## Verification (executable)

- `python Backend/manage.py test orchestration.test_shell_classifier
  orchestration.test_shell_connector` — every tier × profile, root/network/
  destructive detection, and obfuscation edge cases (an obfuscated destructive
  command may classify `safe`; a separate backends test asserts the sandbox
  still runs read-only/non-root, i.e. the false negative costs nothing).
- Risk-gate tests: `run_command` → `requires_confirmation=True`; a destructive
  command → tier `destructive`; existing high-risk actions unchanged.
- Connector: mocked sidecar happy path + sidecar-down error path.
- Full suite + `flake8` + `bandit` + `check_boundaries.py`.

## Rollback

Revert the commit; remove the static catalog entry, the classifier, and the
connector. The loop reverts to its prior static risk gate.

## Open questions for the human

- Confirm Phase 1 intent: even `safe` (`echo hi`) pauses for durable approval
  until #133 makes the loop tier-aware. Yes per roadmap "everything gated".
- `run_command` static `risk_level: high`, `confirmation_policy: always` as the
  fail-closed fallback if the classifier ever errors — confirm.
