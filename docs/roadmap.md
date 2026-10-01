# Roadmap

Here's where Kazi is, and where it's going — without the vaporware.

## Now: v0.6 — shell-first Jarvis

The governed shell shipped: `run_command` through the sandbox sidecar
(isolation profiles `open`/`standard`/`locked`, per-command network + host
allowlist), two-tier escalation (inline confirm / durable approval), persistent
workspace with tar snapshot + rollback, provider-agnostic TTS, and the learning
foundation that earns initiative one approval at a time (telemetry rollups,
preference mining, the initiative ladder). Typed connectors are retired; the
shell is the reach story. Read
[`v0.6-brief.md`](v0.6-brief.md) and [`v0.6-roadmap.md`](v0.6-roadmap.md);
the epic is [issue #139](https://github.com/bedah-kym/kazi-core/issues/139).

## In flight: v0.7 — the teammate you can name

The next cycle turns the learning foundation into product primitives: skills
(a six-part contract, drafted from what Jon just did), routines (owner, inputs,
no-data policy, test run, pause-on-absence), per-rule standing grants that lapse
on drift, named personas, and internal specialist handoffs — all on top of
versioned workflow definitions. Read [`v0.7-brief.md`](v0.7-brief.md), then pick
up work from the
[v0.7 epic](https://github.com/bedah-kym/kazi-core/issues/160). The epic's
build order is the checklist — start with W-A (definition versioning).

## Why we publish it

Early access means breaking changes are possible before v1.0. The honest way to
handle that is to show you the plan and let you see what's real. If a milestone
stalls, the roadmap says so.

## See also

- [v0.7 brief](v0.7-brief.md) / [v0.6 brief](v0.6-brief.md) — the *why* behind the current and next cycle
- [Changelog](https://github.com/bedah-kym/kazi-core/blob/main/CHANGELOG.md) — what actually shipped
- [GitHub issues](https://github.com/bedah-kym/kazi-core/issues) — where the work happens
