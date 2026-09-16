# Roadmap

Here's where Kazi is, and where it's going — without the vaporware.

## Now: v0.5.0

Shipped and tagged. An operator web UI (workflow operations inbox + notification
center), per-room model selection with a frozen model catalog, durable
agent-loop approvals, retry backoff + circuit breakers, an LLM-driven
timezone-aware reminder parser, and a big reliability + security pass
(append-only receipts, agent budget caps, prompt-injection corpus, stack-trace
hardening, payments integrity).

## In flight: v0.6 — shell-first Jarvis

The next cycle gives Kazi a governed shell — sandboxed reach with two-tier
escalation, persistent workspace, and the learning foundation that earns
initiative one approval at a time. Typed connectors are retired; the shell is
the reach story. Read the
[`v0.6-brief.md`](v0.6-brief.md) and [`v0.6-roadmap.md`](v0.6-roadmap.md), then
pick up work from the
[v0.6 epic](https://github.com/bedah-kym/kazi-core/issues/139). The epic's
checklist is the build order — start with the Phase 0 `good first issue`s.

## Why we publish it

Early access means breaking changes are possible before v1.0. The honest way to
handle that is to show you the plan and let you see what's real. If a milestone
stalls, the roadmap says so.

## See also

- [Release notes](v0.4-brief.md) — the *why* behind the cycle
- [Changelog](https://github.com/bedah-kym/kazi-core/blob/main/CHANGELOG.md) — what actually shipped
- [GitHub issues](https://github.com/bedah-kym/kazi-core/issues) — where the work happens
