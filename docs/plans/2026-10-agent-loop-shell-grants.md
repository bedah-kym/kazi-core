# Plan: agent-loop shell autonomy — grants + armed autopilot

Status: approved + built (2026-10-03). Storage shipped as profile JSON (no
migration); autopilot window + exact-command grants are live in the chat loop.
Remaining follow-up: ops UI / chat "Always allow" button.

## Goal

A shell build task runs end-to-end without a "yes" per command. Default: every
`safe` command auto-runs, every `bounded` command asks **once** and can be
promoted to a scoped, expiring grant. Plus an explicit **Autopilot** the human
arms, which auto-runs `safe` + `bounded` for a bounded window. Only
`destructive`/`denied`, and any `bounded` **egress/sensitive step on a tainted
run**, still stop for a human.

## Auto vs. gate rule (the whole design in one place)

| Shell tier (`shell/classifier.py`) | Default | Autopilot armed | Why |
|---|---|---|---|
| `safe` | auto | auto | already auto today |
| `bounded` (local) | ask once → grant | auto | sandbox still bounds it |
| `bounded` (needs network/root) | ask | auto **only if** host allowlisted (#134), else ask | egress is the exfil leg |
| `destructive` | durable gate | durable gate | high risk; tripwire |
| `denied` | refuse | refuse | out of envelope |
| **any tier, tainted run + egress/sensitive** | gate | **gate** | Rule of Two; non-negotiable |

The last row is the one thing that cannot go auto. If untrusted text (web page,
file, command output) tainted the run, an egress/sensitive action is the
data-theft path (Simon Willison, "lethal trifecta"; Meta, "Agents Rule of Two",
Oct 2025). Auto-running it would delete the property that makes the agent safe.
Everything else — including all local build commands — is auto under autopilot.

## Autopilot mode (human-armed)

- **Arming is explicit and receipted**: a human turns it on for a *room +
  session*; start/stop are `action_receipt`s and the human is notified.
- **Bounded**: a wall-clock window (proposed 30 min, renewable) and a tool-call
  budget (proposed `SHELL_SESSION_MAX_TOOL_CALLS`). Expiry pauses, never
  silently continues.
- **Kill switch**: any human message cancels immediately.
- **No widening**: autopilot only removes prompts for tiers that were already
  inside the sandbox. It can never run `destructive`/`denied` or a tainted
  egress/sensitive step.
- **Visible**: ops UI shows armed state, remaining budget, and every step taken.

## Non-goals

- **No LLM in the approval/consent path.** An LLM approver is a filter; the
  credited literature shows filters are bypassed ("The Attacker Moves Second",
  arXiv:2510.09023 — 12 defenses bypassed >90%), and it burns tokens per action.
  Consent stays deterministic. (Autonomy here is a *pre-approved envelope*, not
  a model deciding.)
- Not "auto-approve everything". `destructive`, `denied`, and tainted
  egress/sensitive always gate; this removes a *prompt*, never a boundary.
- Not workflow grants (that is #159) and not the planner manager LLM.
- No change to sandbox reach or the network allowlist (#134); autopilot consumes
  them, it does not weaken them.

## Touches

- Paths: `Backend/orchestration/tool_executor.py` (`_run_command_risk_info`),
  `Backend/orchestration/agent_loop.py` (`_bucket_tool_calls`),
  `Backend/orchestration/coordinator.py` (load/inject),
  `Backend/orchestration/shell/grants.py` + `autopilot.py` (new),
  `Backend/orchestration/management/commands/shell_autonomy.py` (new), tests.
- Protected paths? yes — `agent_loop.py`, `tool_executor.py`.
- Contracts/migrations/payments/secrets? **No migration** — shipped as profile
  JSON, mirroring #152. No payments, no secrets, no contract change.
- Built: **chat triggers** (`always allow` / `autopilot`) and an **ops UI panel**
  on the operations inbox (`/workflows/inbox/`) to arm/disarm autopilot and
  revoke grants, plus `manage.py shell_autonomy`. Remaining follow-up: wiring
  `grants.sweep_expired_grants()` into the nightly sweep.

## Risk class of any new action

outside-world (shell can egress) / reversible-in-workspace. Auto-runs
previously human-approved command classes inside an armed, time-boxed envelope.
Bounded by tier, fingerprint, TTL, budget, kill switch, ask-first, taint
escalation. Every autonomous run writes an `action_receipt`.

## Approach

1. New `AgentShellGrant(user, room, tool, fingerprint, decision, status,
   expires_at, lapse_reason, created_from_approval)`, modeled on
   `StandingGrant`; additive migration.
2. `fingerprint(command)` = canonical verb + subcommand + argument *shape* (from
   `shell/classifier.py`). **Reject** shell metacharacters (`; & | > < \` $ % ( )`)
   and opaque `python -c` / `cmd /c` blobs — never auto-eligible.
3. "Always allow" creates a grant **only** for `bounded`; `destructive`/`denied`
   are never grantable.
4. `_run_command_risk_info`: `bounded` + active matching grant + not
   `user_asks_first` + not tainted → `safe`. When autopilot is armed for the
   room/session, `bounded` (and allowlisted network) → `safe` without a grant.
5. Taint rule enforced before any auto path: tainted + (network/root) → force
   gate, grant or autopilot notwithstanding. Reuse `_bucket_tool_calls` (#170).
6. Sweep lapses expired grants + expired autopilot; any deny revokes the
   fingerprint; ops UI lists both with one-click revoke/disarm.

## Verification (executable)

- `python Backend/manage.py test orchestration.test_shell_grants orchestration.test_shell_autopilot workflows.test_grants`
- Full suite + `flake8` + `bandit` + `python scripts/check_boundaries.py`.
- Failure paths covered: destructive/denied never auto; tainted egress still
  gates with autopilot armed; expired grant/autopilot asks; deny revokes;
  metacharacter/opaque command never eligible; ask-first wins; budget/window
  expiry pauses; kill switch cancels.
- Injection corpus cases added: golden scenario where fetched web text emits
  `run_command` with network — must gate even under autopilot.

## Rollback

Disarm autopilot + revert migration/code. Grants are additive rows; deleting
them returns shell to prompt-on-every-command. Autopilot defaults **off**.

## Manager LLM decision (long run)

**Stays, demoted — advisory plan rewriter only; never an approver.**

- Keep `ManagerVerifier` (deterministic) and `_llm_manager_review`
  (`workflow_planner.py:1023`) where they are: an evaluator-optimizer mapping
  intent → valid actions, opt-in (`manager_llm_enabled`), high-risk-only, and
  able to only `ask_user` or revise. It never bypasses the human gate.
- Autonomy here comes from **deterministic grants + an armed envelope + the
  taint model**, not a manager model. Zero tokens on the hot path.
- Trend: as deterministic coverage (catalog, mining #152, these grants) grows,
  the LLM manager's authority should shrink. Review annually.
- Never build an LLM that answers the confirmation prompt.

## Resolved at build (2026-10-03)

1. Fingerprint = **exact normalized command** (lowercased, whitespace-collapsed).
   Metacharacters and opaque interpreter blobs (`python -c`, `cmd /c`) are never
   grantable and always ask.
2. Storage: **profile JSON** (`notification_preferences`), no migration.
3. Autopilot default window: `SHELL_AUTOPILOT_MINUTES` (30). Per-call budget is
   covered by the existing `SHELL_SESSION_MAX_TOOL_CALLS` loop cap.
4. Scope: **per (user, room)** for both grants and autopilot.
5. Network: unchanged — #134 allowlist still applies; a tainted egress step
   always asks regardless of autopilot/grant.

## Verification (recorded)

- `manage.py test` full suite: **1033 tests, OK (skipped=1)**.
- `flake8 Backend ... --max-complexity=10`: 0 issues.
- `bandit -r Backend --skip B101,B110`: no issues.
- `scripts/check_boundaries.py`: clean.
- New coverage: `orchestration.test_shell_autonomy` (fingerprint, risk rule,
  store, autopilot), `test_shell_chat_intents` (phrase + negation detection,
  prompt hint), `workflows.test_shell_autonomy_ui` (panel + POST actions) +
  existing shell/coordinator suites green.
