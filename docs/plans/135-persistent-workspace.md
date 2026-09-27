# Plan: #135 Persistent workspace + snapshot/rollback

Status: draft for implementation (no protected paths)

## Goal

The per-room workspace persists across commands, and a destructive command is
snapshotted first so it can be rolled back: a bad session is a workspace reset,
not a reinstall.

## Non-goals

- No ZFS/btrfs snapshots (tar first, roadmap §4.6/§11).
- No snapshot retention/GC policy beyond "files on disk".

## Touches

- `Backend/orchestration/shell_exec/backends.py`: `snapshot_workspace`,
  `restore_snapshot`.
- `Backend/orchestration/shell_exec/daemon.py`: `/exec` takes `snapshot: true`
  and returns the snapshot id; new `POST /rollback`.
- `Backend/orchestration/connectors/shell_connector.py`: request a snapshot for a
  `destructive` tier.
- `Backend/orchestration/management/commands/shell_rollback.py` (new).
- `docs/configuration.md`, tests.
- Protected paths? none.

## Approach

1. `snapshot_workspace(config, room)` tars the room workspace to
   `<root>/snapshots/<room>/<utc>.tar.gz`; `restore_snapshot` clears the
   workspace and extracts with `filter="data"` (hostile member paths refused).
2. Daemon: snapshot before executing when asked (Kazi decides, no policy in the
   sidecar); include the id in the response. `POST /rollback` restores.
3. Connector: `snapshot: true` for `destructive`.
4. `manage.py shell_rollback --room --snapshot` for operators.

## Verification (executable)

- `test_shell_workspace`: snapshot/restore round-trip; unknown snapshot raises;
  a file written in one execution is readable in a second (persistence).
- Daemon: `/exec` snapshot returns an id; `/rollback` restores; missing
  snapshot -> 400.
- Connector: destructive -> `snapshot: true`; safe -> absent.
- Full suite + flake8 + bandit + `check_boundaries.py`.

## Rollback

Revert. Snapshots are plain files and can be deleted.

## Open questions for the human

- Snapshot retention: keep all snapshots (current) or cap per room? Fine to
  defer; flag if you want a cap now.
