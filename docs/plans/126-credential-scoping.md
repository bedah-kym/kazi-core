# Plan: #126 Credential-scoping spec for shell + delegate

Status: approved by maintainer on 2026-09-27

## Goal

One reviewable page defines exactly what the shell/delegate execution
environment may touch, so every later shell/delegate PR (#130, #137) has a
fixed target to check against.

## Non-goals

- No runtime code. This is the Phase 0 spec only.
- Does not implement the sidecar (#130) or the delegate connector (#137, blocked
  to v0.7+).
- Does not define a true per-host egress firewall; v0.6 is network on/off plus
  an allowlist-as-UX (roadmap §4.5, §10).
- Does not pick the delegation runtime (the #125 spike is deferred).

## Touches

- Paths:
  - `docs/contracts/credential-scoping.md` (new)
  - `docs/contracts/README.md` (index row)
  - `docs/security.md` (link from "Where it lives" / layers)
- Protected paths? yes: `docs/contracts/*`
- Contracts, migrations, approvals, payments, secrets? Adds a new documented
  contract (v1.0, tier 1 "documented only"). No migrations. No runtime writes.
  No secrets in the doc — rules only, no key material.

## Risk class of any new action

read-only — a documentation rule set. It constrains future actions; it performs
none.

## Approach

1. State the boundary in one sentence: the sandbox is the security boundary,
   the classifier is UX/tripwire, secrets never enter the environment.
2. Four rule sections, each a concrete enforceable rule (not "be careful"):
   - **Filesystem** — read-only rootfs; only `<SHELL_EXEC_ROOT>/workspaces/<room_id>`
     mounted rw at `/workspace`; never a live mount of a sensitive host path.
   - **Network egress** — default `--network=none`; allowlist shortlist only;
     unreachable by policy: Kazi Postgres/Redis, credential stores, prod hosts.
   - **API keys** — scoped + ephemeral, injected per call, revoked after; never
     Kazi's DB/Redis/provider keys, never the user's admin key.
   - **Revocation/rotation** — a single revocable shared token (`SHELL_EXEC_TOKEN`),
     rotation procedure, and the leak blast radius (one token, not an identity).
3. Add the index row and link it from `docs/security.md`.

## Verification (executable)

- `python scripts/check_boundaries.py` → exit 0 (doc-only change; no ratchet impact).
- `python Backend/manage.py check` → no issues.
- Manual review: each of the four concerns has one concrete rule with a
  checkable consequence; reviewer can point a shell/delegate PR at it.

## Rollback

Revert the doc commit; remove the index row and the `docs/security.md` link.

## Open questions for the human (resolved 2026-09-27)

- Home: keep in `docs/contracts/` as the issue recommends. (Approved.)
- Networking: name the concrete future setting `SHELL_EXEC_NETWORK_ALLOWLIST`
  here so #132 documents the same key. (Chosen: yes.)
