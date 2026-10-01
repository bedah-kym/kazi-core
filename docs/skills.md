# Skills

A **skill** is a drop-in instruction pack — a folder with a `SKILL.md` that
teaches the agent one thing well. No database, no new dependencies.

## How it works

The registry scans the `skills/` directory on boot. Each skill is a folder
containing a `SKILL.md` with a small frontmatter block:

```yaml
---
name: web-scraper
description: Extract structured data from a public webpage
tools: search_info
stage: active        # staging | review | active | stale | archived
pinned: false        # pinned skills are never archived by the curator
---
```

Only skills marked `active` are exposed to the agent. The others stay parked.

## Promotion gates

Skills move through five stages:

- `staging` — draft, invisible to the agent
- `review` — visible to operators, not yet live
- `active` — loaded and usable
- `stale` — unused, demoted but recoverable
- `archived` — retired; only comes back to `stale` for re-review

Promotion is human-only and runs `staging → review → active` through
`orchestration.skill_registry.transition_skill`; illegal jumps (for example
`staging → active`) are refused. Demotion is reversible:
`active → stale → active`, and `archived → stale → active` after re-review.

A weekly curator (`orchestration.tasks.curate_skill_lifecycle`) may only
**demote**: unused active skills become `stale` after `SKILL_STALE_AFTER_DAYS`
(default 90), stale skills become `archived` after `SKILL_ARCHIVE_AFTER_DAYS`
(default 180). It never promotes, never touches `staging`/`review`, and never
archives a skill with `pinned: true`. `curate_skills(dry_run=True)` returns the
same proposal without writing anything.

There's a `SKILL_MAX_CHARS` cap (default `8000`) on the instruction body so
one skill can't eat the context.

## Using skills

The agent can call the `list_skills` and `load_skill` meta-tools to discover
and load a skill mid-conversation — you don't have to restart to use one.

## Shipped example

`skills/report-formatting/` shows the full shape: a `SKILL.md` with frontmatter
and instructions.

## Next

- [Configuration](configuration.md) — `SKILL_MAX_CHARS` and friends
- [Features](features.md) — where Skills fit in the bigger picture
