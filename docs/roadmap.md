# Roadmap

Here's where Kazi is, and where it's going — without the vaporware.

## Shipped: v0.6 — shell-first Jarvis

The governed shell shipped: `run_command` through the sandbox sidecar
(isolation profiles `open`/`standard`/`locked`, per-command network + host
allowlist), two-tier escalation (inline confirm / durable approval), persistent
workspace with tar snapshot + rollback, provider-agnostic TTS, and the learning
foundation that earns initiative one approval at a time (telemetry rollups,
preference mining, the initiative ladder). Typed connectors are retired; the
shell is the reach story. Read
[`v0.6-brief.md`](v0.6-brief.md) and [`v0.6-roadmap.md`](v0.6-roadmap.md);
the epic is [issue #139](https://github.com/bedah-kym/kazi-core/issues/139).

## Now: v0.7 — the teammate you can name

Shipped in 0.7.0: the learning foundation became product primitives. Skills
(a six-part contract, drafted from repeated work), routines (owner, inputs,
no-data policy, test run, pause-on-absence), per-rule standing grants that lapse
on drift, named personas, and the runtime for internal specialist handoffs —
all on top of versioned workflow definitions. Shell approvals now follow the
sandbox boundary, with an opt-in enforced egress proxy. Read
[`v0.7-brief.md`](v0.7-brief.md); the epic was
[issue #160](https://github.com/bedah-kym/kazi-core/issues/160).

## In flight: v0.8 — turns that own the truth

A persisted turn state machine, a typed stream protocol, and a verification
layer, so "done" is decided by the record and not by the narration. Read the
[proposal](https://github.com/bedah-kym/kazi-core/blob/main/docs/proposals/2026-10-turn-state-and-verification.md); the epic is
[issue #227](https://github.com/bedah-kym/kazi-core/issues/227).

## Why we publish it

Early access means breaking changes are possible before v1.0. The honest way to
handle that is to show you the plan and let you see what's real. If a milestone
stalls, the roadmap says so.

## See also

- [v0.7 brief](v0.7-brief.md) / [v0.6 brief](v0.6-brief.md) — the *why* behind the last two cycles
- [Changelog](https://github.com/bedah-kym/kazi-core/blob/main/CHANGELOG.md) — what actually shipped
- [GitHub issues](https://github.com/bedah-kym/kazi-core/issues) — where the work happens
