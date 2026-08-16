# Contributing

Kazi is community-governed — no VC decides the roadmap, you do. *Harambee*:
we all pull together.

## The fastest first PR: add a connector

A connector is **one file plus one test**. That's the whole bar.

1. Read [Add a Connector](add-a-connector.md).
2. Write `Backend/orchestration/connectors/<your_connector>.py`.
3. Add a happy-path and an error-path test.
4. Open a PR — auto-discovery picks up your connector on restart.

That's it. No registration step, no config changes.

## Other ways to help

- **Docs** — every page has an *Edit this page* button. Fix a typo, clarify a
  step, or write a missing how-to.
- **Bug reports** — search first, then open an issue with steps to reproduce.
- **Vulnerabilities** — don't file a public issue; follow
  [SECURITY.md](https://github.com/bedah-kym/kazi-core/blob/main/SECURITY.md).
- **Ideas** — the [roadmap](roadmap.md) maps 1:1 to GitHub issues; comment there.

## Conventions

- Commits: `type(scope): summary` — e.g. `feat(orchestration): add slack connector`.
- One focused change per PR; don't bundle unrelated cleanup.
- Read [AGENTS.md](https://github.com/bedah-kym/kazi-core/blob/main/AGENTS.md)
  and [CONTRIBUTING.md](https://github.com/bedah-kym/kazi-core/blob/main/CONTRIBUTING.md)
  before you start.

## Be decent

We follow a code of conduct. Assume good faith, argue about ideas not people,
and remember there's a human on the other side of every review.
