# Plan: Reconcile the #128 shell success metrics with the shipped API

Status: draft — awaiting human OK (protected paths); human said "yeap" on chat

## Goal

Make #128's two remaining `expectedFailure` shell metrics pass honestly: the
dynamic shell gate exposes an effective `tier` and per-command `is_high_risk`,
and `run_command` writes an action receipt. Remove both decorators.

## Non-goals

- No change to non-shell tools' risk behavior.
- No change to the approval-record contract.
- No change to the classifier's raw tiers (network stays `bounded` per roadmap
  §4.3); only the *effective* gate tier collapses an allowlisted network command
  to `safe`.

## Touches

- Protected: `Backend/orchestration/tool_executor.py` — `_run_command_risk_info`
  returns `tier` (effective), per-command `is_high_risk`/`risk_level`, keeps
  `shell_tier` (raw).
- Protected: `Backend/orchestration/action_receipts.py` — add `run_command` to
  `_AUDITED_ACTIONS`.
- `Backend/orchestration/test_v06_metrics.py` — roadmap-correct assertions,
  remove both `@expectedFailure`.
- `Backend/orchestration/test_shell_connector.py` — align `is_high_risk`/`tier`.

## Approach

1. Effective tier: raw `bounded` + needs_network + allowlisted -> `safe`
   (no prompt); otherwise raw tier.
2. `is_high_risk = tier in {destructive, denied}`; `risk_level` high/medium/low
   by tier.
3. Audit `run_command` so a confirmed shell action leaves a receipt.

## Verification (executable)

- `test_v06_metrics` both shell metrics pass (decorators removed).
- `test_shell_connector` tier/is_high_risk assertions updated.
- Full suite + flake8 + bandit + `check_boundaries.py`.

## Rollback

Revert; the metrics return to their prior (red, expected-failure) state.

## Open questions for the human

- The metric predates the shell-first rewrite and called `ping` "safe"; network
  commands are `bounded` per roadmap §4.3, so the metric now asserts a read-only
  command is `safe`/ungated and an **allowlisted** `ping` is ungated. Confirm
  that reading (vs. forcing `ping` to `safe`).
