# Plan: #132 Isolation profiles (open / standard / locked)

Status: draft for implementation (no protected paths)

## Goal

The roadmap §3 profile table lives in code: a `Profile` registry with resolution
(per-room override, else the global default `standard`), and a loud boot warning
when the global default is `open`.

## Non-goals

- No escalation routing change (#133) and no network toggle/allowlist (#134):
  the classifier's tier mapping stays as shipped; this PR makes profiles the
  single config source and adds a consistency guard.
- No persistent workspace/snapshot (#135).

## Touches

- `Backend/orchestration/shell/profiles.py` (new): `Profile`, `PROFILES`,
  `get_profile`, `resolve_profile`, `shell_profile_pref_key`, `warn_if_open_profile`.
- `Backend/orchestration/apps.py`: call `warn_if_open_profile()` at boot.
- `docs/configuration.md`: the profile table + resolution.
- `Backend/orchestration/test_shell_profiles.py` (new).
- Protected paths? none.

## Approach

1. `Profile(name, backend, user, rootfs_readonly, writable, network,
   escalation, description)` with the three roadmap profiles.
2. `resolve_profile(room_id, preferences)`: per-room override (Redis preference
   key, fail-closed to the global default on any lookup error) else
   `settings.SHELL_EXEC_PROFILE` (default `standard`, unknown -> `standard`).
3. `warn_if_open_profile()` logs a banner when the default is `open`; called from
   `OrchestrationConfig.ready()` next to the demo banner.
4. Tests: registry shape, default resolution, room override, fail-closed on
   cache error, `open` warning, and escalation values consistent with
   `classify_command` for representative commands.

## Verification (executable)

- `python Backend/manage.py test orchestration.test_shell_profiles`.
- Full suite + flake8 + bandit + `check_boundaries.py`.
- Failure paths: cache error -> default; unknown profile -> standard.

## Rollback

Revert the commit. No schema, no runtime behavior change beyond the warning.

## Open questions for the human

- Per-room override storage: Redis preference key `kazi:shell:profile:<room_id>`
  (mirrors `model_pref_key`), fail-closed to the default. OK?
