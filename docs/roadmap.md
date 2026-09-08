# Roadmap

Here's where Kazi is, and where it's going — without the vaporware.

## Now: v0.5.0

Shipped and tagged. An operator web UI (workflow operations inbox + notification
center), per-room model selection with a frozen model catalog, durable
agent-loop approvals, retry backoff + circuit breakers, an LLM-driven
timezone-aware reminder parser, and a big reliability + security pass
(append-only receipts, agent budget caps, prompt-injection corpus, stack-trace
hardening, payments integrity).

## In flight: hardening toward v1.0

The v0.4 human-gated-runtime cycle is complete; its backlog lives in
[`v0.4-roadmap.md`](v0.4-roadmap.md) for reference. What ships next is queued as
GitHub issues — no hidden backlog, no "coming soon" that never comes. Carryover
still tracked: the two queued contracts (telemetry events, memory updates) and
the 24-of-25-connector envelope migration.

## Why we publish it

Early access means breaking changes are possible before v1.0. The honest way to
handle that is to show you the plan and let you see what's real. If a milestone
stalls, the roadmap says so.

## See also

- [Release notes](v0.4-brief.md) — the *why* behind the cycle
- [Changelog](https://github.com/bedah-kym/kazi-core/blob/main/CHANGELOG.md) — what actually shipped
- [GitHub issues](https://github.com/bedah-kym/kazi-core/issues) — where the work happens
